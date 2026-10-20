"""Operator-owned model settings shared with authenticated, trusted workers.

Credentials remain encrypted at rest in the control directory. The gateway
re-encrypts them for one worker; browsers receive only the public catalog. No
workspace database, cloud-management key or control-directory key is shared.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from pydantic import ValidationError
from starlette.responses import JSONResponse

from backend.errors import ResourceValidationError, RevisionConflictError
from backend.security.llm_settings import LlmSettingsStore, SecretStorageError
from backend.security.model_connections import CatalogEdit, ConnectionEdit, ModelConnectionStore

MAX_DOCUMENT = 8 * 1024 * 1024
_AUTH_DOMAIN = b"oaw-shared-models-auth-v1\0"
_CIPHER_DOMAIN = b"oaw-shared-models-encryption-v1\0"


def worker_cipher(secret):
    return Fernet(base64.urlsafe_b64encode(hmac.digest(bytes.fromhex(secret), _CIPHER_DOMAIN, "sha256")))


def service_token(secret, profile_id, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    message = f"{profile_id}:{timestamp}".encode()
    return f"{timestamp}." + hmac.digest(bytes.fromhex(secret), _AUTH_DOMAIN + message, "sha256").hex()


def valid_service_token(value, profile):
    try:
        timestamp, signature = value.split(".")
        issued = int(timestamp)
        return (str(issued) == timestamp and time.time() - 30 < issued <= time.time() + 5
                and hmac.compare_digest(value, service_token(profile.secret, profile.profile_id, issued)))
    except (ValueError, TypeError, AttributeError):
        return False


class SettingsDatabase:
    """A small settings database; no world or user-workspace tables."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        self.path.chmod(0o600)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS application_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")

    @contextmanager
    def transaction(self, *, immediate=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if immediate:
                    db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    locked = transaction


class SharedModelSource:
    """Read-only catalog source. Cached briefly in memory, never in a profile DB."""

    def __init__(self, config, *, transport=None):
        self.config = config
        self.cipher = worker_cipher(config.secret)
        self.transport = transport
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0

    def read(self):
        with self.lock:
            if self.cached is not None and time.monotonic() < self.expires:
                return self.cached
            # A failed refresh must not silently keep a disabled model usable.
            self.cached = None
            headers = {"Host": urlsplit(self.config.public_origin).netloc,
                       "Authorization": "Bearer " + service_token(self.config.secret, self.config.profile_id)}
            try:
                with httpx.Client(transport=self.transport, timeout=5, trust_env=False, follow_redirects=False) as client:
                    with client.stream("GET", self.config.shared_models_origin + "/hosting/internal/models/" + self.config.profile_id,
                                       headers=headers) as response:
                        response.raise_for_status()
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_DOCUMENT:
                                raise ValueError("Catalog too large")
                value = json.loads(data)
                # Validate the public shape before any runtime can consume it.
                ModelConnectionStore._public(value)
                if any(c.get("auth_mode") == "environment" for c in value["connections"]):
                    raise ValueError("Shared models cannot use worker environment credentials")
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise ResourceValidationError("Shared model settings are unavailable. Retry or contact your administrator.") from None
            self.cached, self.expires = value, time.monotonic() + 2
            return value


class SharedModelAdmin:
    def __init__(self, gateway):
        self.gateway = gateway
        root = gateway.settings.accounts_path.parent / "services"
        self.store = ModelConnectionStore(LlmSettingsStore(SettingsDatabase(root / "settings.sqlite3"), root))

    async def authorize(self, request):
        if not self.gateway.check_origin(request.scope):
            return self.gateway.error(403, "origin_rejected", "Origin rejected")
        user = await self.gateway.auth.user_from_scope(request.scope)
        if user is None:
            return self.gateway.error(401, "authentication_required", "Sign in to continue.")
        if user["role"] != "admin":
            return self.gateway.error(403, "admin_required", "Administrator access required.")
        return None

    @staticmethod
    async def edit(request, model):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_DOCUMENT:
                raise ValueError("Document too large")
        value = model.model_validate_json(body)
        connections = value.connections if isinstance(value, CatalogEdit) else [value]
        if any(c.auth_mode == "environment" or c.environment_variable for c in connections):
            raise ValueError("Use an explicit shared key, not process environment credentials")
        return value

    async def models(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        try:
            value = self.store.read() if request.method == "GET" else self.store.save(await self.edit(request, CatalogEdit))
            return JSONResponse(value.model_dump())
        except RevisionConflictError:
            return self.gateway.error(409, "revision_conflict", "Settings changed. Reload before saving.")
        except (ValueError, ValidationError, ResourceValidationError):
            return self.gateway.error(422, "invalid_settings", "Check the model settings. Keep existing model IDs and disable entries instead of deleting them.")
        except SecretStorageError:
            return self.gateway.error(503, "credentials_unavailable", "Stored credentials are unavailable. Contact the deployment operator.")

    async def discover(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        try:
            edit = await self.edit(request, ConnectionEdit)
        except (ValueError, ValidationError):
            return self.gateway.error(422, "invalid_connection", "Check the service address and explicit API key.")
        from backend.security.model_discovery import discover_models
        try:
            return JSONResponse(await discover_models(edit, self.store))
        except ResourceValidationError as exc:
            return self.gateway.error(422, "discovery_failed", str(exc))
        except SecretStorageError:
            return self.gateway.error(503, "credentials_unavailable", "Stored credentials are unavailable.")

    async def status(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        return JSONResponse({"shared_models_enabled": self.gateway.settings.shared_models_origin is not None,
                             "workspace_count": len(self.gateway.settings.profiles),
                             "users": [{k: user[k] for k in ("username", "display_name", "role", "enabled")}
                                       for user in self.gateway.auth.store.list_users()]})

    async def worker_catalog(self, request):
        profile = next((p for p in self.gateway.settings.profiles if p.profile_id == request.path_params["profile_id"]), None)
        headers = request.headers.getlist("authorization")
        if (self.gateway.settings.shared_models_origin is None or profile is None or len(headers) != 1
                or not headers[0].startswith("Bearer ") or not valid_service_token(headers[0][7:], profile)):
            return self.gateway.error(403, "service_auth_required", "Worker authentication required.")
        user = self.gateway.auth.store.get_user(profile.user_id)
        if not user or not user["enabled"]:
            return self.gateway.error(403, "account_disabled", "Workspace account is unavailable.")
        try:
            with self.store.database.locked() as db:
                value = self.store._read(db)
            cipher = worker_cipher(profile.secret)
            for connection in value["connections"]:
                encrypted = connection.get("api_key_encrypted")
                if encrypted:
                    secret = self.store.secrets._fernet(create=False).decrypt(encrypted.encode())
                    connection["api_key_encrypted"] = cipher.encrypt(secret).decode()
            return JSONResponse(value)
        except (SecretStorageError, InvalidToken, ValueError, OSError):
            return self.gateway.error(503, "credentials_unavailable", "Shared model credentials are unavailable.")
