"""Profile-owned UI preferences, shared by browsers and the desktop window."""
from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend import __version__
from backend.api.dependencies import get_services

KEY = "ui_preferences.v1"
IDENTITY = "application_identity.v1"
PREFERENCE_KEYS = frozenset({
    "oaw-onboarding-v1", "oaw-canvas-viewport-v1", "oaw-glue-v1",
    "oaw-node-surfaces-v1", "oaw-library-preferences", "oaw-theme", "oaw.locale",
    "oaw-model-settings", "open-agent-world.decks.v2", "open-agent-world.custom-decks.v1",
})


def read_preferences(db):
    row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"generation": str(uuid4()), "values": {}}


def write_setting(db, key, value):
    db.execute("INSERT INTO application_settings(key,value_json) VALUES (?,?) "
               "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(value)))


def application_snapshot(services):
    with services.database.transaction(immediate=True) as db:
        row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (IDENTITY,)).fetchone()
        identity = json.loads(row[0]) if row else str(uuid4())
        if not row:
            write_setting(db, IDENTITY, identity)
        existing = db.execute("SELECT 1 FROM application_settings WHERE key=?", (KEY,)).fetchone()
        preferences = read_preferences(db)
        if not existing:
            write_setting(db, KEY, preferences)
    return {"mode": services.settings.application_mode, "profile_id": identity,
            "version": __version__, **preferences}


class PreferenceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(max_length=64)
    generation: str = Field(max_length=64)
    changes: dict[str, str | None] = Field(max_length=len(PREFERENCE_KEYS))

    @field_validator("changes")
    @classmethod
    def validate_changes(cls, changes):
        if set(changes) - PREFERENCE_KEYS:
            raise ValueError("Unsupported preference key")
        if any(value is not None and len(value.encode("utf-8")) > 1_000_000 for value in changes.values()):
            raise ValueError("Preference is too large")
        # Old browser builds may have saved a credential here. Never import it.
        value = changes.get("oaw-model-settings")
        if value:
            parsed = json.loads(value)
            changes["oaw-model-settings"] = json.dumps({k: parsed[k] for k in ("baseUrl", "models") if k in parsed})
        return changes


router = APIRouter(prefix="/api/application", tags=["application"])


@router.get("")
def get_application(services=Depends(get_services)):
    return application_snapshot(services)


@router.patch("/preferences")
def save_preferences(edit: PreferenceEdit, services=Depends(get_services)):
    with services.database.transaction(immediate=True) as db:
        identity = db.execute("SELECT value_json FROM application_settings WHERE key=?", (IDENTITY,)).fetchone()
        current = read_preferences(db)
        if not identity or json.loads(identity[0]) != edit.profile_id or current["generation"] != edit.generation:
            raise HTTPException(409, "The profile was reset or changed. Reload this window.")
        for key, value in edit.changes.items():
            if value is None:
                current["values"].pop(key, None)
            else:
                current["values"][key] = value
        write_setting(db, KEY, current)
    return {"generation": current["generation"]}
