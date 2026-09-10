"""Versioned model connections, sharing the application's encrypted secret storage."""
from __future__ import annotations

import json
import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.errors import ResourceValidationError, RevisionConflictError
from backend.security.llm_settings import LlmSettingsStore

MODEL_REF_PREFIX = "oaw:model:"
_KEY = "model_connections"


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class ModelConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=120)
    adapter: Literal["openai", "anthropic", "gemini", "legacy"] = "openai"
    base_url: str = Field(default="", max_length=2000)
    enabled: bool = True
    api_key_configured: bool = False
    auth_mode: Literal["api_key", "none", "environment"] = "api_key"
    environment_variable: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    models: list[ModelEntry] = Field(default_factory=list, max_length=100)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Use an HTTP(S) base URL without credentials, query or fragment")
        return value.rstrip("/")


class ConnectionEdit(ModelConnection):
    api_key: str | None = Field(default=None, max_length=16000, repr=False)
    clear_api_key: bool = False


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    connections: list[ModelConnection] = Field(default_factory=list, max_length=100)
    default_model: str | None = None


class CatalogEdit(ModelCatalog):
    connections: list[ConnectionEdit] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_references(self):
        connections = [c.id for c in self.connections]
        models = [m.id for c in self.connections for m in c.models]
        if len(set(connections)) != len(connections) or len(set(models)) != len(models):
            raise ValueError("Connection and model IDs must be unique")
        available = {MODEL_REF_PREFIX + m.id for c in self.connections if c.enabled for m in c.models if m.enabled}
        if self.default_model is not None and self.default_model not in available:
            raise ValueError("Choose an enabled model as the default")
        for connection in self.connections:
            if connection.api_key and not connection.clear_api_key:
                # Entering a key is sufficient; callers need not also change
                # an old environment/no-key selection to activate that key.
                connection.auth_mode = "api_key"
            if connection.id == "legacy" and connection.adapter != "legacy":
                raise ValueError("The previous connection must retain automatic routing. Add a new connection for a different API format.")
            if connection.auth_mode == "none" and not connection.base_url:
                raise ValueError("A service without authentication needs an explicit base URL")
            if connection.api_key and connection.clear_api_key:
                raise ValueError("Choose either replacing or removing the API key")
        return self


class ModelConnectionStore:
    def __init__(self, secrets: LlmSettingsStore):
        self.secrets = secrets
        self.database = secrets.database

    def _read(self, db):
        row = db.execute("SELECT value_json FROM application_settings WHERE key = ?", (_KEY,)).fetchone()
        if row:
            return json.loads(row["value_json"])
        # Import only the old connection here. Model names live in the old browser
        # and are imported explicitly by the settings editor on first save.
        legacy = self.secrets.read()
        connections = []
        if legacy.base_url or legacy.api_key:
            connections = [dict(id="legacy", name="Default connection", adapter="legacy", auth_mode="api_key",
                                base_url=legacy.base_url, enabled=True, models=[],
                                api_key_encrypted=self.secrets._fernet(create=True).encrypt(legacy.api_key.encode()).decode() if legacy.api_key else None)]
        return dict(revision=0, connections=connections, default_model=None)

    @staticmethod
    def _public(raw):
        return ModelCatalog(revision=raw["revision"], default_model=raw["default_model"], connections=[
            ModelConnection(**{k: v for k, v in c.items() if k != "api_key_encrypted"},
                            api_key_configured=bool(c.get("api_key_encrypted"))) for c in raw["connections"]
        ])

    def read(self):
        with self.database.locked() as db:
            return self._public(self._read(db))

    def save(self, edit: CatalogEdit):
        with self.database.transaction(immediate=True) as db:
            old = self._read(db)
            if old["revision"] != edit.revision:
                raise RevisionConflictError("Model settings changed in another window. Reload before saving.")
            previous = {c["id"]: c for c in old["connections"]}
            incoming = {c.id: c for c in edit.connections}
            for cid, connection in previous.items():
                if cid not in incoming or not {m["id"] for m in connection["models"]}.issubset({m.id for m in incoming[cid].models}):
                    raise ResourceValidationError("Disable saved connections and models instead of removing them; existing references must be preserved.")
            connections = []
            for item in edit.connections:
                encrypted = previous.get(item.id, {}).get("api_key_encrypted")
                if item.clear_api_key:
                    encrypted = None
                elif item.api_key:
                    encrypted = self.secrets._fernet(create=True).encrypt(item.api_key.encode()).decode()
                connection = item.model_dump(exclude={"api_key", "clear_api_key", "api_key_configured"})
                connection["api_key_encrypted"] = encrypted
                connections.append(connection)
            raw = dict(revision=old["revision"] + 1, connections=connections, default_model=edit.default_model)
            db.execute("INSERT INTO application_settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (_KEY, json.dumps(raw)))
            # Keep legacy readers consistent, including key removal. There is no
            # second independently editable copy once the catalog is saved.
            for connection in connections:
                if connection["id"] == "legacy":
                    db.execute("INSERT INTO application_settings (key, value_json) VALUES ('llm_connection', ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                               (json.dumps({"base_url": connection["base_url"], "api_key_encrypted": connection["api_key_encrypted"]}),))
            return self._public(raw)

    def legacy_options(self):
        with self.database.locked() as db:
            raw = self._read(db)
        if not raw["revision"]:
            return None
        for connection in raw["connections"]:
            if connection["id"] == "legacy":
                if not connection["enabled"]:
                    raise ResourceValidationError("The legacy model connection is disabled. Choose a configured model.")
                options = {}
                if connection["base_url"]:
                    options["api_base"] = connection["base_url"]
                encrypted = connection.get("api_key_encrypted")
                if encrypted and connection.get("auth_mode") == "api_key":
                    options["api_key"] = self.secrets._fernet(create=False).decrypt(encrypted.encode()).decode()
                if connection.get("auth_mode") == "environment" and connection.get("environment_variable"):
                    variable = connection["environment_variable"]
                    key = os.environ.get(variable)
                    if not key:
                        raise ResourceValidationError(f"Backend environment variable {variable!r} is not set for this connection.")
                    options["api_key"] = key
                if connection.get("auth_mode") == "api_key" and not encrypted:
                    raise ResourceValidationError("This connection needs an API key. Add one in Settings.")
                if connection.get("auth_mode") == "none":
                    options["api_key"] = "oaw-no-auth"
                return options
        return None

    def resolve(self, reference: str):
        with self.database.locked() as db:
            raw = self._read(db)
        for connection in raw["connections"]:
            for model in connection["models"]:
                if MODEL_REF_PREFIX + model["id"] != reference:
                    continue
                if not connection["enabled"] or not model["enabled"]:
                    raise ResourceValidationError("Selected model or connection is disabled. Choose another model in Settings.")
                auth_mode = connection.get("auth_mode", "api_key")
                encrypted = connection.get("api_key_encrypted")
                key = self.secrets._fernet(create=False).decrypt(encrypted.encode()).decode() if encrypted and auth_mode == "api_key" else None
                if auth_mode == "api_key" and not key:
                    raise ResourceValidationError("This connection needs an API key. Add one in Settings.")
                if auth_mode == "environment":
                    variable = connection.get("environment_variable") or _provider_environment_variable(connection["adapter"])
                    key = os.environ.get(variable)
                    if not key:
                        raise ResourceValidationError(f"Backend environment variable {variable!r} is not set for this connection.")
                if auth_mode == "none":
                    key = "oaw-no-auth"
                return connection["adapter"], model["model_id"], connection["base_url"], key
        raise ResourceValidationError("Selected model connection is missing on this backend. Choose a model in Settings.")


def _provider_environment_variable(adapter: str) -> str:
    return {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(adapter, "OPENAI_API_KEY")
