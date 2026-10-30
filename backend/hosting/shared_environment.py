"""Administrator-owned Sandbox variables, encrypted and read per invocation."""
from __future__ import annotations

import base64
import hmac
import json
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from starlette.responses import JSONResponse

from backend.errors import ResourceValidationError, RevisionConflictError
from backend.execution_config import SecretRequirement
from backend.sandbox.environment import validate_command_environment
from backend.sandbox.models import SandboxValidationError
from backend.security.environment_source import ExecutionEnvironmentSnapshot
from backend.security.llm_settings import LlmSettingsStore, SecretStorageError
from .shared_models import SettingsDatabase, service_token, valid_service_token

MAX_DOCUMENT = 256 * 1024
SETTINGS_KEY = "shared_execution_environment"


def environment_cipher(secret):
    domain = b"oaw-shared-execution-environment-v1\0"
    return Fernet(base64.urlsafe_b64encode(hmac.digest(bytes.fromhex(secret), domain, "sha256")))


class EnvironmentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0, strict=True)
    environment_variables: dict[str, StrictStr | SecretRequirement] = Field(default_factory=dict, max_length=100)

    @field_validator("environment_variables")
    @classmethod
    def validate_variables(cls, values):
        validate_command_environment({k: v if isinstance(v, str) else "" for k, v in values.items()}, allow_target=False)
        return values


class EnvironmentEdit(EnvironmentDefinition):
    secrets: dict[str, StrictStr] = Field(default_factory=dict, exclude=True, max_length=100)

    @field_validator("secrets")
    @classmethod
    def validate_secrets(cls, values):
        if any(not value or "\0" in value or len(value.encode()) > 16000 for value in values.values()):
            raise ValueError("Invalid environment secret")
        return values


class EnvironmentDocument(EnvironmentDefinition):
    encrypted_secrets: dict[str, str] = Field(default_factory=dict, repr=False, max_length=100)


class SharedEnvironmentStore:
    def __init__(self, root):
        self.database = SettingsDatabase(root / "environment.sqlite3")
        self.crypto = LlmSettingsStore(self.database, root)

    def _read(self, db):
        row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (SETTINGS_KEY,)).fetchone()
        return EnvironmentDocument.model_validate_json(row[0]) if row else EnvironmentDocument()

    @staticmethod
    def references(document):
        return {v.secret_ref for v in document.environment_variables.values() if isinstance(v, SecretRequirement)}

    @classmethod
    def public(cls, document):
        return {**EnvironmentDefinition.model_validate({
            "revision": document.revision, "environment_variables": document.environment_variables}).model_dump(),
            "secret_bindings": {ref: ref in document.encrypted_secrets for ref in cls.references(document)}}

    def read(self):
        with self.database.locked() as db:
            return self._read(db)

    def decrypt(self, document):
        if not document.encrypted_secrets:
            return {}
        cipher = self.crypto._fernet(create=False)
        return {ref: cipher.decrypt(value.encode()).decode() for ref, value in document.encrypted_secrets.items()}

    def save(self, edit):
        with self.database.transaction(immediate=True) as db:
            previous = self._read(db)
            if previous.revision != edit.revision:
                raise RevisionConflictError("Environment changed")
            references = self.references(edit)
            if set(edit.secrets) - references:
                raise ValueError("Unreferenced secrets")
            encrypted = {ref: value for ref, value in previous.encrypted_secrets.items() if ref in references}
            if edit.secrets:
                cipher = self.crypto._fernet(create=not bool(previous.encrypted_secrets))
                encrypted.update({ref: cipher.encrypt(value.encode()).decode() for ref, value in edit.secrets.items()})
            if set(encrypted) != references:
                raise ValueError("Bind every secret before saving")
            document = EnvironmentDocument(revision=previous.revision + 1,
                                           environment_variables=edit.environment_variables, encrypted_secrets=encrypted)
            values = self.decrypt(document)
            validate_command_environment({
                name: value if isinstance(value, str) else values[value.secret_ref]
                for name, value in document.environment_variables.items()}, allow_target=False)
            db.execute("INSERT INTO application_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (SETTINGS_KEY, document.model_dump_json()))
            return document


class SharedEnvironmentAdmin:
    def __init__(self, gateway, authorize):
        self.gateway, self.authorize = gateway, authorize
        self.store = SharedEnvironmentStore(gateway.settings.accounts_path.parent / "services")

    async def settings(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        try:
            if request.method == "GET":
                document = self.store.read()
            else:
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > MAX_DOCUMENT:
                        raise ValueError("Document too large")
                document = self.store.save(EnvironmentEdit.model_validate_json(data))
            return JSONResponse(self.store.public(document))
        except RevisionConflictError:
            return self.gateway.error(409, "revision_conflict", "Settings changed. Reload before saving.")
        except (ValueError, SandboxValidationError):
            return self.gateway.error(422, "invalid_environment", "Check variable names, values and secret bindings. Host controls and paths are not supported.")
        except (SecretStorageError, InvalidToken, OSError):
            return self.gateway.error(503, "credentials_unavailable", "Stored environment credentials are unavailable.")

    async def worker_environment(self, request):
        await self.gateway.refresh()
        profile = next((p for p in self.gateway.settings.profiles if p.profile_id == request.path_params["profile_id"]), None)
        headers = request.headers.getlist("authorization")
        if (self.gateway.settings.shared_environment_origin is None or profile is None or len(headers) != 1
                or not headers[0].startswith("Bearer ") or not valid_service_token(headers[0][7:], profile)):
            return self.gateway.error(403, "service_auth_required", "Worker authentication required.")
        user = self.gateway.auth.store.get_user(profile.user_id)
        if not user or not user["enabled"]:
            return self.gateway.error(403, "account_disabled", "Workspace account is unavailable.")
        try:
            document = self.store.read()
            values = self.store.decrypt(document)
            cipher = environment_cipher(profile.secret)
            document.encrypted_secrets = {ref: cipher.encrypt(value.encode()).decode() for ref, value in values.items()}
            return JSONResponse(document.model_dump())
        except (SecretStorageError, InvalidToken, ValueError, OSError, SandboxValidationError):
            return self.gateway.error(503, "credentials_unavailable", "Shared environment is unavailable.")


class SharedEnvironmentSource:
    def __init__(self, config, *, transport=None):
        self.config, self.transport = config, transport

    def read(self):
        # Fetch a coherent snapshot for each resolution. No plaintext is persisted
        # in user profiles and a failed refresh never falls back to revoked values.
        headers = {"Host": urlsplit(self.config.public_origin).netloc,
                   "Authorization": "Bearer " + service_token(self.config.secret, self.config.profile_id)}
        try:
            with httpx.Client(transport=self.transport, timeout=5, trust_env=False, follow_redirects=False) as client:
                url = self.config.shared_environment_origin + "/hosting/internal/environment/" + self.config.profile_id
                with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_DOCUMENT:
                            raise ValueError("Environment too large")
            document = EnvironmentDocument.model_validate_json(data)
            if set(document.encrypted_secrets) != SharedEnvironmentStore.references(document):
                raise ValueError("Incomplete secret bindings")
            cipher = environment_cipher(self.config.secret)
            values = {ref: cipher.decrypt(value.encode()).decode() for ref, value in document.encrypted_secrets.items()}
            validate_command_environment({
                name: value if isinstance(value, str) else values[value.secret_ref]
                for name, value in document.environment_variables.items()}, allow_target=False)
            return ExecutionEnvironmentSnapshot(document.environment_variables, values)
        except (httpx.HTTPError, ValueError, InvalidToken, SandboxValidationError):
            raise ResourceValidationError("Shared environment settings are unavailable. Retry or contact your administrator.") from None
