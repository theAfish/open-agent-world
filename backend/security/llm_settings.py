from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backend.persistence.database import Database


_SETTINGS_KEY = "llm_connection"
_DPAPI_PREFIX = b"dpapi:"


class SecretStorageError(RuntimeError):
    """A persisted secret cannot be safely read or written."""


def _protect_key(key: bytes) -> bytes:
    if os.name != "nt":
        return key
    return _DPAPI_PREFIX + _windows_dpapi(key, protect=True)


def _unprotect_key(stored: bytes) -> bytes:
    if not stored.startswith(_DPAPI_PREFIX):
        return stored
    if os.name != "nt":
        raise SecretStorageError(
            "This credential key is protected by Windows and cannot be opened here."
        )
    return _windows_dpapi(stored[len(_DPAPI_PREFIX):], protect=False)


def _windows_dpapi(value: bytes, *, protect: bool) -> bytes:
    # DPAPI binds the encryption key to the Windows account running the backend.
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(value)
    source = DataBlob(
        len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    result = DataBlob()
    crypt32 = ctypes.windll.crypt32
    if protect:
        succeeded = crypt32.CryptProtectData(
            ctypes.byref(source),
            "Open Agent World settings",
            None,
            None,
            None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(result),
        )
    else:
        succeeded = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)
        )
    if not succeeded:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


@dataclass(frozen=True, slots=True)
class LlmConnectionSettings:
    base_url: str = ""
    api_key: str | None = None

    def public(self) -> "LlmPublicSettings":
        return LlmPublicSettings(
            base_url=self.base_url,
            api_key_configured=bool(self.api_key),
        )


@dataclass(frozen=True, slots=True)
class LlmPublicSettings:
    base_url: str = ""
    api_key_configured: bool = False


class LlmSettingsStore:
    """Stores LLM credentials encrypted at rest and never exposes them via the API."""

    def __init__(self, database: Database, data_root: Path) -> None:
        self.database = database
        self.key_path = data_root / "secrets" / "settings.key"

    def read(self) -> LlmConnectionSettings:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT value_json FROM application_settings WHERE key = ?",
                (_SETTINGS_KEY,),
            ).fetchone()
        if row is None:
            return LlmConnectionSettings()
        try:
            value = json.loads(str(row["value_json"]))
            base_url = value.get("base_url", "")
            encrypted = value.get("api_key_encrypted")
            if not isinstance(base_url, str) or (
                encrypted is not None and not isinstance(encrypted, str)
            ):
                raise ValueError("invalid settings shape")
            api_key = (
                self._fernet(create=False).decrypt(encrypted.encode()).decode()
                if encrypted
                else None
            )
            return LlmConnectionSettings(base_url=base_url, api_key=api_key)
        except (
            OSError,
            ValueError,
            UnicodeError,
            InvalidToken,
            json.JSONDecodeError,
        ) as exc:
            raise SecretStorageError(
                "Saved model credentials could not be decrypted. Restore the secrets/settings.key "
                "file that belongs to this data directory, or clear the saved credentials."
            ) from exc

    def save(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> LlmConnectionSettings:
        current = self.read()
        selected_key = None if clear_api_key else (api_key if api_key else current.api_key)
        encrypted = (
            self._fernet(create=True).encrypt(selected_key.encode()).decode()
            if selected_key
            else None
        )
        payload = json.dumps(
            {"base_url": base_url, "api_key_encrypted": encrypted},
            separators=(",", ":"),
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO application_settings (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (_SETTINGS_KEY, payload),
            )
        return LlmConnectionSettings(base_url=base_url, api_key=selected_key)

    def _fernet(self, *, create: bool) -> Fernet:
        try:
            key = _unprotect_key(self.key_path.read_bytes())
        except FileNotFoundError:
            if not create:
                raise
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            try:
                descriptor = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                key = _unprotect_key(self.key_path.read_bytes())
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(_protect_key(key))
                try:
                    self.key_path.chmod(0o600)
                except OSError:
                    pass
        try:
            return Fernet(key.strip())
        except (TypeError, ValueError) as exc:
            raise SecretStorageError("The model credential encryption key is invalid.") from exc
