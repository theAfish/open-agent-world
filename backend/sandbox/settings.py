"""Persisted Sandbox creation defaults and live global environment settings."""
from __future__ import annotations

from pathlib import Path
import json

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from backend.persistence.database import Database
from .manager import SandboxManager
from .registry import SandboxRuntimeRegistry
from backend.execution_config import EnvironmentProfile, SecretRequirement
from backend.security.execution_credentials import GlobalExecutionCredentialStore
from backend.errors import ResourceValidationError


class SandboxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str | None = Field(default=None, max_length=4096)
    runtime: str = Field(default="auto", min_length=1, max_length=200)
    environment_variables: dict[str, StrictStr | SecretRequirement] = Field(default_factory=dict)

    @field_validator("environment_variables")
    @classmethod
    def validate_environment(cls, value: dict[str, str | SecretRequirement]) -> dict[str, str | SecretRequirement]:
        return EnvironmentProfile(variables=value).variables

    @field_validator("workspace_root", "runtime")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("settings must be non-empty and NUL-free")
        return value


class SandboxSettingsUpdate(SandboxSettings):
    secrets: dict[str, StrictStr] = Field(default_factory=dict, exclude=True)

    @field_validator("secrets")
    @classmethod
    def validate_secrets(cls, values):
        if any(not v or "\0" in v or len(v.encode()) > 16000 for v in values.values()):
            raise ValueError("Secrets must be nonempty NUL-free strings up to 16 KB")
        return values


class SandboxSettingsStatus(SandboxSettings):
    secret_bindings: dict[str, bool] = Field(default_factory=dict)
    backup_paths: list[str] = Field(default_factory=list)


class SandboxSettingsStore:
    def __init__(self, database: Database, data_root: Path) -> None:
        self.database = database
        self.credentials = GlobalExecutionCredentialStore(database, data_root)
        self.validator = SandboxManager(data_root, SandboxRuntimeRegistry())

    def read(self) -> SandboxSettings:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT value_json FROM application_settings WHERE key = 'sandbox'"
            ).fetchone()
        return SandboxSettings.model_validate_json(row["value_json"]) if row else SandboxSettings()

    def resolve_workspace_root(self) -> Path:
        """Resolve the current host default, including its system-managed fallback."""
        root = self.read().workspace_root
        if root is None:
            return self.validator.root
        return Path(self.validator.validate_workspace(root))

    def public(self) -> SandboxSettingsStatus:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT value_json FROM application_settings WHERE key = 'sandbox_workspace_backups'"
            ).fetchone()
            settings = self.read()
            bindings = {v.secret_ref: self.credentials.configured(None, v.secret_ref)
                        for v in settings.environment_variables.values() if isinstance(v, SecretRequirement)}
            return SandboxSettingsStatus(**settings.model_dump(), secret_bindings=bindings,
                                         backup_paths=json.loads(row["value_json"]) if row else [])

    def record_backups(self, paths: list[str]) -> None:
        """Called in the same transaction as the workspace/settings switch."""
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO application_settings (key, value_json) VALUES ('sandbox_workspace_backups', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (json.dumps(list(dict.fromkeys(paths)), ensure_ascii=False),),
            )

    def validate_bindings(self, settings):
        secrets = getattr(settings, "secrets", {})
        references = {v.secret_ref for v in settings.environment_variables.values() if isinstance(v, SecretRequirement)}
        if set(secrets) - references:
            raise ResourceValidationError("Secret does not belong to this environment")
        for name, value in settings.environment_variables.items():
            if isinstance(value, SecretRequirement) and value.secret_ref not in secrets and not self.credentials.configured(None, value.secret_ref):
                raise ResourceValidationError(f"Enter a secret for {name}, or remove the unused variable")
        return references

    def save(self, settings: SandboxSettings) -> SandboxSettings:
        references = self.validate_bindings(settings)
        root = self.validator.validate_workspace(settings.workspace_root)
        settings = settings.model_copy(update={"workspace_root": root})
        with self.database.transaction(immediate=True) as connection:
            old = self.read()
            connection.execute(
                "INSERT INTO application_settings (key, value_json) VALUES ('sandbox', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (settings.model_dump_json(),),
            )
            old_references = {v.secret_ref for v in old.environment_variables.values() if isinstance(v, SecretRequirement)}
            for reference, value in getattr(settings, "secrets", {}).items():
                self.credentials.bind(None, reference, value)
            for reference in old_references - references:
                self.credentials.unbind(None, reference)
        return settings
