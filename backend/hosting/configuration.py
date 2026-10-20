"""Operator-owned hosted routing configuration; never populated from HTTP input."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID


def identifier(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected a canonical UUID")
    return value


def unix_socket_path(upstream: str) -> str | None:
    """A filesystem socket, never an abstract socket or a URL-decoded path."""
    if not upstream.startswith("unix:"):
        return None
    url = urlsplit(upstream)
    if (upstream != "unix://" + url.path or url.scheme != "unix" or url.netloc or url.query or url.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", url.path)
            or "//" in url.path or any(part in {".", ".."} for part in url.path.split("/"))
            or len(url.path.encode()) > 100 or not url.path.endswith(".sock")):
        raise ValueError("Worker Unix socket must be a short, absolute filesystem path")
    return url.path


def default_upstream(profile_id: str, name: str = "oaw-hosted") -> str:
    identifier(profile_id)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", name):
        raise ValueError("Invalid systemd deployment name")
    return f"unix:///run/{name}/{profile_id}.sock"


def public_origin(value: str, allow_local_http: bool = False) -> str:
    url = urlsplit(value)
    local = allow_local_http and url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (url.scheme != "https" and not local) or not url.netloc or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Use an exact HTTPS public origin (local HTTP requires --allow-local-http)")
    return value


@dataclass(frozen=True)
class HostedProfile:
    profile_id: str
    user_id: str
    application_id: str
    name: str
    mode: str
    upstream: str
    secret: str = field(repr=False)

    def __post_init__(self):
        identifier(self.profile_id)
        identifier(self.user_id)
        if self.mode not in {"builder", "runtime"}:
            raise ValueError("Profile mode must be builder or runtime")
        if (not isinstance(self.application_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.application_id)
                or not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 120):
            raise ValueError("Application ID and name are required and must be bounded")
        socket_path = unix_socket_path(self.upstream)
        url = urlsplit(self.upstream)
        if socket_path is None and (url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password
                or url.path or url.query or url.fragment):
            raise ValueError("Worker upstream must be an exact operator-configured HTTP(S) origin")
        if (not isinstance(self.secret, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){32,64}", self.secret)):
            raise ValueError("Each worker requires an independent secret of 32 to 64 random bytes encoded as hex")

    def public(self) -> dict:
        return {"id": self.profile_id, "application_id": self.application_id, "name": self.name, "mode": self.mode}


@dataclass(frozen=True)
class GatewaySettings:
    public_origin: str
    accounts_path: Path
    profiles: tuple[HostedProfile, ...] = ()
    allow_local_http: bool = False

    def __post_init__(self):
        public_origin(self.public_origin, self.allow_local_http)
        for key in ("profile_id", "user_id", "upstream", "secret"):
            values = [getattr(profile, key) for profile in self.profiles]
            if key == "secret":
                values = [value.lower() for value in values]
            if len(values) != len(set(values)):
                raise ValueError(f"Hosted profiles require unique {key} values")


def read_gateway_settings(path: Path) -> GatewaySettings:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1:
        raise ValueError("Unsupported hosting configuration")
    return GatewaySettings(
        value["public_origin"], path.parent / "accounts.sqlite3",
        tuple(HostedProfile(**profile) for profile in value.get("profiles", [])),
        value.get("allow_local_http", False),
    )


def write_private_json(path: Path, value: dict) -> None:
    """Atomic owner-only configuration writes; do not overwrite via a symlink."""
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Hosting configuration must not use symlinks")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import secrets
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
