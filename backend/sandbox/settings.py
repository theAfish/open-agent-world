"""Persisted Sandbox creation defaults and live global environment settings."""
from __future__ import annotations

from pathlib import Path
import json

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from backend.persistence.database import Database
from .manager import SandboxManager
from .registry import SandboxRuntimeRegistry
from .environment import validate_command_environment


class SandboxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str | None = Field(default=None, max_length=4096)
    runtime: str = Field(default="auto", min_length=1, max_length=200)
    environment_variables: dict[str, StrictStr] = Field(default_factory=dict)

    @field_validator("environment_variables")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_command_environment(value, allow_target=False)

    @field_validator("workspace_root", "runtime")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("settings must be non-empty and NUL-free")
        return value


class SandboxSettingsStatus(SandboxSettings):
    backup_paths: list[str] = Field(default_factory=list)


class SandboxSettingsStore:
    def __init__(self, database: Database, data_root: Path) -> None:
        self.database = database
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
            return SandboxSettingsStatus(**self.read().model_dump(), backup_paths=json.loads(row["value_json"]) if row else [])

    def record_backups(self, paths: list[str]) -> None:
        """Called in the same transaction as the workspace/settings switch."""
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO application_settings (key, value_json) VALUES ('sandbox_workspace_backups', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (json.dumps(list(dict.fromkeys(paths)), ensure_ascii=False),),
            )

    def save(self, settings: SandboxSettings) -> SandboxSettings:
        root = self.validator.validate_workspace(settings.workspace_root)
        settings = settings.model_copy(update={"workspace_root": root})
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO application_settings (key, value_json) VALUES ('sandbox', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (settings.model_dump_json(),),
            )
        return settings
