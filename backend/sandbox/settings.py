"""Persisted defaults for newly created Sandbox cards on this backend host."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.persistence.database import Database
from .manager import SandboxManager
from .registry import SandboxRuntimeRegistry


class SandboxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_root: str | None = Field(default=None, max_length=4096)
    runtime: str = Field(default="auto", min_length=1, max_length=200)

    @field_validator("workspace_root", "runtime")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("settings must be non-empty and NUL-free")
        return value


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
