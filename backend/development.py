"""Development-only reset plans. Mutations run after a clean server shutdown."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from backend.api.dependencies import get_services
from backend.application import IDENTITY, KEY as UI_KEY, application_snapshot, read_preferences, write_setting
from backend.card_library import KEY as LIBRARY_KEY, Deck, LibraryState
from backend.config import Settings
from backend.storage_location import file_lock

PROFILES = Path(__file__).resolve().parents[1] / ".open-agent-world" / "development" / "profiles"
SCOPES = {"workspace", "decks", "packs", "tutorial", "interface", "models", "runtime"}
Scope = Literal["workspace", "decks", "packs", "tutorial", "interface", "models", "runtime", "all"]
MARKER = ".development-profile.json"
RECOVERY = ".reset-recovery.json"
MANAGED_DIRECTORIES = {"assets", "projects", "sandboxes", "sandbox-bindings", "sandbox-runtimes", "runtime", "secrets"}


def profile_path(name: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name):
        raise ValueError("Profile names must contain 1-64 letters, digits, underscores, or hyphens")
    if name.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        raise ValueError("Reserved profile name")
    return PROFILES / name


def reject_links(root: Path):
    # Validate the lexical path before resolve() can hide a junction target.
    for part in (root, *root.parents):
        if part.is_symlink() or part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Development profiles must not use symbolic links or junctions")


def validate_profile(root: Path, *, require_marker=True):
    reject_links(root)
    if root.absolute().parent != PROFILES.absolute() or root.resolve().parent != PROFILES.resolve():
        raise ValueError("Reset is restricted to this checkout's development profiles")
    if require_marker:
        marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
        if marker != {"kind": "oaw-development", "profile": root.name}:
            raise ValueError("Invalid development profile marker")


def prepare_profile(name: str) -> Path:
    root = profile_path(name)
    validate_profile(root, require_marker=False)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError("Refusing to adopt an existing directory as a disposable development profile")
        marker.write_text(json.dumps({"kind": "oaw-development", "profile": name}), encoding="utf-8")
    validate_profile(root)
    recover_interrupted_reset(root)
    return root


def recover_interrupted_reset(root: Path):
    journal = root / RECOVERY
    if not journal.exists():
        return
    with file_lock(root / ".oaw-storage.lock"):
        record = json.loads(journal.read_text(encoding="utf-8"))
        backup = Path(record["backup"])
        reject_links(backup)
        expected = PROFILES.parent / "backups" / root.name
        if backup.resolve().parent != expected.resolve() or set(record["directories"]) - MANAGED_DIRECTORIES:
            raise ValueError("Invalid reset recovery journal")
        with closing(sqlite3.connect(root / "database/world.sqlite3")) as db:
            committed = read_preferences(db)["generation"] == record["generation"]
        if not committed:
            for name in record["directories"]:
                source, archived = root / name, backup / name
                reject_links(archived)
                if archived.exists():
                    if source.exists():
                        raise ValueError(f"Reset recovery needs manual review: both {source} and {archived} exist")
                    archived.rename(source)
        journal.unlink()


def effective_scopes(scopes):
    selected = set(scopes)
    if not selected or selected - (SCOPES | {"all"}):
        raise ValueError("Choose at least one supported reset scope")
    if "all" in selected:
        return SCOPES | {"all"}
    # A tutorial session and its surface references cannot survive world deletion.
    if "workspace" in selected:
        selected.add("tutorial")
    return selected


class ResetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[Scope] = Field(min_length=1, max_length=8)


class ResetRequest(ResetSelection):
    profile_id: str = Field(max_length=64)
    generation: str = Field(max_length=64)


@dataclass
class DevelopmentControl:
    settings: Settings
    pending: ResetRequest | None = None

    def __post_init__(self):
        if self.settings.application_mode != "development":
            raise ValueError("Reset requires development mode")
        validate_profile(self.settings.data_root)

    def install(self, app):
        router = APIRouter(prefix="/api/debug", tags=["development"])

        @router.get("")
        def status(services=Depends(get_services)):
            return {**application_snapshot(services), "data_root": str(self.settings.data_root),
                    "profile": self.settings.data_root.name, "pending": self.pending is not None}

        @router.post("/plan")
        def plan(selection: ResetSelection, services=Depends(get_services)):
            validate_profile(self.settings.data_root)
            with services.database.transaction() as db:
                cards = db.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            return {**status(services), "scopes": sorted(effective_scopes(selection.scopes)), "world_cards": cards}

        @router.post("/reset", status_code=202)
        async def reset(request: ResetRequest, services=Depends(get_services)):
            validate_profile(self.settings.data_root)
            snapshot = application_snapshot(services)
            if self.pending or request.profile_id != snapshot["profile_id"] or request.generation != snapshot["generation"]:
                raise HTTPException(409, "The profile changed or a reset is already pending. Refresh the plan.")
            effective_scopes(request.scopes)
            self.pending = request
            return {"status": "restarting", "generation": snapshot["generation"]}

        # Reject new writes while Uvicorn drains existing requests and WebSockets.
        @app.middleware("http")
        async def maintenance(request, call_next):
            if self.pending and request.method not in {"GET", "HEAD", "OPTIONS"}:
                return JSONResponse({"detail": "Development profile is restarting"}, status_code=503)
            return await call_next(request)

        app.include_router(router)


def reset_profile(settings: Settings, request: ResetRequest) -> Path:
    """No live service container may exist here. Keep a recoverable DB/file backup."""
    root = settings.data_root
    if settings.application_mode != "development":
        raise ValueError("Reset requires development mode")
    validate_profile(root)
    scopes = effective_scopes(request.scopes)
    backup = PROFILES.parent / "backups" / root.name / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    moved: list[tuple[Path, Path]] = []
    journal = root / RECOVERY
    if journal.exists():
        raise ValueError("Restart the development launcher to recover the interrupted reset first")
    # The same lock used by application startup prevents another process opening it.
    with file_lock(root / ".oaw-storage.lock"):
        validate_profile(root)
        with closing(sqlite3.connect(settings.database_path)) as db:
            old = read_preferences(db)
            identity = db.execute("SELECT value_json FROM application_settings WHERE key=?", (IDENTITY,)).fetchone()
            if not identity or json.loads(identity[0]) != request.profile_id or old["generation"] != request.generation:
                raise ValueError("The reset plan no longer matches this profile")
            backup.mkdir(parents=True)
            reject_links(backup)
            with closing(sqlite3.connect(backup / "world.sqlite3")) as saved:
                db.backup(saved)
            try:
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute("BEGIN IMMEDIATE")
                if "workspace" in scopes:
                    keep = {"application_settings", "legions"} if "all" not in scopes else {"application_settings"}
                    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    for (table,) in tables:
                        if table not in keep and not table.startswith("sqlite_"):
                            db.execute('DELETE FROM "' + table.replace('"', '""') + '"')
                    db.execute("DELETE FROM application_settings WHERE key LIKE 'execution_credential:%'")
                if "all" in scopes:
                    db.execute("DELETE FROM application_settings")
                    write_setting(db, IDENTITY, json.loads(identity[0]))
                    # The SQLite file still exists, so explicitly seed a fresh library
                    # rather than letting startup interpret it as a pre-Pack upgrade.
                    write_setting(db, LIBRARY_KEY, LibraryState().model_dump(mode="json"))
                    old["values"] = {}
                else:
                    if "models" in scopes:
                        db.execute("DELETE FROM application_settings WHERE key IN ('model_connections','llm_connection')")
                        old["values"].pop("oaw-model-settings", None)
                    if "decks" in scopes or "packs" in scopes:
                        row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (LIBRARY_KEY,)).fetchone()
                        library = LibraryState.model_validate_json(row[0]) if row else LibraryState()
                        if "decks" in scopes:
                            library.decks = [Deck(id="starter", name="My deck")]
                            library.active_deck_id = "starter"
                        if "packs" in scopes:
                            library.collection.clear()
                            for pack in library.packs.values():
                                pack.opened = False
                                pack.opened_at = None
                            for deck in library.decks:
                                deck.entries = [entry for entry in deck.entries if entry.kind == "legion"]
                        library.migration_pending = False
                        library.revision += 1
                        write_setting(db, LIBRARY_KEY, library.model_dump(mode="json"))
                    if "tutorial" in scopes:
                        old["values"].pop("oaw-onboarding-v1", None)
                    if "interface" in scopes:
                        old["values"] = {k: v for k, v in old["values"].items() if k in {"oaw-onboarding-v1", "oaw-model-settings"}}
                    elif "workspace" in scopes:
                        for key in ("oaw-canvas-viewport-v1", "oaw-glue-v1", "oaw-node-surfaces-v1"):
                            old["values"].pop(key, None)
                old["generation"] = str(uuid4())
                write_setting(db, UI_KEY, old)
                directories = []
                if "workspace" in scopes:
                    directories += ["assets", "projects", "sandboxes", "sandbox-bindings", "sandbox-runtimes"]
                if "runtime" in scopes:
                    directories += ["runtime"]
                if "all" in scopes:
                    directories += ["secrets"]
                # Persist the recovery decision before the first filesystem move.
                # SQLite rolls an interrupted transaction back; startup then restores
                # the archived directories unless the new generation was committed.
                with journal.open("x", encoding="utf-8") as stream:
                    json.dump({"backup": str(backup), "generation": old["generation"], "directories": directories}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                for name in directories:
                    source = root / name
                    if source.exists():
                        if source.is_symlink() or getattr(source.lstat(), "st_file_attributes", 0) & 0x400:
                            raise ValueError(f"Managed directory must not be a junction: {source}")
                        target = backup / name
                        source.rename(target)  # Move the owned directory itself; never walk external workspaces.
                        moved.append((source, target))
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Reset would leave inconsistent database references")
                (backup / "reset.json").write_text(json.dumps({"scopes": sorted(scopes), "profile": str(root)}, indent=2), encoding="utf-8")
                db.commit()
            except BaseException:
                db.rollback()
                for source, target in reversed(moved):
                    target.rename(source)
                journal.unlink(missing_ok=True)
                raise
            journal.unlink(missing_ok=True)
    return backup
