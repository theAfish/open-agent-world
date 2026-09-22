"""Create or serve a locked application from a published, stopped source profile."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from dataclasses import replace
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
from uuid import UUID, uuid4

from backend.config import Settings
from backend.deployments import MANIFEST, PREFIX, configuration_digest
from backend.errors import DomainError
from backend.storage_location import (
    LOCK, _checkpoint, _copy, _fingerprint, _rebase_managed_metadata,
    _reparse, _verify_database, _write, _entries, file_lock,
)


def validate_manifest(value):
    from backend.legion_workspace import WorkspaceLayout
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unsupported deployment manifest")
    for key in ("id", "name", "created_at", "plugin_versions", "permissions", "panels", "password", "runtime_settings"):
        if key not in value:
            raise ValueError("Incomplete deployment manifest")
    UUID(value["id"])
    WorkspaceLayout.model_validate(value["layout"])
    from backend.plugins.deployment import DeploymentSurface
    for node_id, access in value.get("plugin_access", {}).items():
        if node_id not in value["permissions"]:
            raise ValueError("Plugin deployment access must belong to a published node")
        DeploymentSurface.model_validate(access)
    password = value["password"]
    if len(bytes.fromhex(password["salt"])) != 16 or len(bytes.fromhex(password["hash"])) != 32:
        raise ValueError("Invalid deployment password record")


def _rebase_database(stage, source, target):
    """Relocate host-owned paths, preserving user documents and conversation text."""
    def relocate(value):
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str):
            for old, new in ((str(source), str(target)), (source.as_posix(), target.as_posix())):
                if value == old or value.startswith((old + "/", old + "\\")):
                    return new + value[len(old):]
        return value
    with closing(sqlite3.connect(stage / "database/world.sqlite3")) as db, db:
        for node_id, raw in db.execute("SELECT id,config_json FROM cards").fetchall():
            config = relocate(json.loads(raw))
            db.execute("UPDATE cards SET config_json=? WHERE id=?", (json.dumps(config), node_id))
        for key, raw in db.execute("SELECT key,value_json FROM application_settings").fetchall():
            if key.startswith((PREFIX, "ui_preferences.", "application_identity.")):
                db.execute("DELETE FROM application_settings WHERE key=?", (key,))
            else:
                db.execute("UPDATE application_settings SET value_json=? WHERE key=?", (json.dumps(relocate(json.loads(raw))), key))


def _rebind_native_sandboxes(stage, source, target, *, native=None):
    """A copied Windows sandbox must not retain the source profile's OS identity/ACLs."""
    manifests = [*stage.glob("sandboxes/*/sandbox.json"), *stage.glob("sandbox-runtimes/*/sandboxes/*/sandbox.json")]
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "identity" not in data:
            if data.get("unit"):
                raise ValueError("A Linux Sandbox still owns a live service. Stop all Sandboxes before deployment.")
            continue
        if os.name != "nt" and native is None:
            raise ValueError("Windows Sandbox snapshots must be prepared on their original Windows host/account")
        if data.get("workspace_authorized") or data.get("network_filters_installed") or data.get("state") == "running":
            raise ValueError("Stop all Windows Sandboxes cleanly before deployment")
        relative = path.parents[2].relative_to(stage)
        def identity(root):
            return "OpenAgentWorld." + hashlib.sha256(f"{root / relative}|{data['sandbox_id']}".encode()).hexdigest()[:40]
        if data["identity"] != identity(source):
            raise ValueError("Source Sandbox identity does not match its data directory")
        if native is None:
            from backend.sandbox.win32 import WindowsNativeApi
            native = WindowsNativeApi()
        old = native.ensure_appcontainer(data["identity"])
        new = None
        try:
            new = native.ensure_appcontainer(identity(target))
            # _copy preserved ACLs, including protected child ACLs and hard links.
            # Remove the old identity throughout the COPY; never change source ACLs.
            native.revoke_path(stage, old.sid)
            for entry in _entries(stage):
                if not _reparse(entry):
                    native.revoke_path(entry, old.sid)
            native.grant_path(path.parent / "workspace", new.sid, read_only=False)
            data.update(identity=identity(target), state="stopped", workspace_authorized=False,
                        workspace_identity=None, network_filters_installed=False)
            _write(path, data)
        finally:
            if new is not None:
                native.free_appcontainer_sid(new)
            native.free_appcontainer_sid(old)


def create_deployment(source: Path, target: Path, release_id: str, *, password: str,
                      allow_external_workspaces=False, secure_cookie=False):
    from backend.deployment_runtime import password_record
    UUID(release_id)
    source, target = source.resolve(), target.absolute()
    for path in (target, *target.parents):
        if _reparse(path):
            raise ValueError("Deployment destination must not traverse links or junctions")
    target = target.resolve()
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Choose a deployment directory outside the source profile")
    if target.exists():
        raise ValueError("Destination already exists. Use --serve to preserve its runtime data, or choose a new directory.")
    if not (source / "database/world.sqlite3").is_file() or (source / MANIFEST).exists():
        raise ValueError("Source must be an existing engineering profile")
    if len(password) < 12:
        raise ValueError("Use an access password of at least 12 characters")
    with file_lock(source / LOCK):
        _checkpoint(source)
        with closing(sqlite3.connect(source / "database/world.sqlite3")) as db:
            row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (PREFIX + release_id,)).fetchone()
            if not row:
                raise ValueError("Release not found in this profile. Publish the Legion workspace first.")
            release = json.loads(row[0])
            if configuration_digest(db) != release["configuration_digest"]:
                raise ValueError("Configuration changed after publication. Publish a new release before deploying.")
            active = db.execute("SELECT 1 FROM runs WHERE status IN ('created','running','waiting') LIMIT 1").fetchone()
            if active:
                raise ValueError("Source has unfinished runs. Restart it, finish/stop the runs, then shut down cleanly.")
            external = []
            for node_id, raw in db.execute("SELECT id,config_json FROM cards"):
                config = json.loads(raw)
                for key in ("workspace_path", "working_directory", "cwd", "project_path"):
                    path = config.get(key)
                    if path and Path(path).is_absolute() and not Path(path).resolve().is_relative_to(source):
                        external.append({"node_id": node_id, "field": key, "path": path})
            if external and not allow_external_workspaces:
                raise ValueError("External workspaces are present. Use --allow-external-workspaces only if sharing those folders is intended: "
                                 + ", ".join(item["path"] for item in external))
        before = _fingerprint(source)
        stage = target.with_name(target.name + ".partial-" + uuid4().hex)
        stage.mkdir(parents=True, mode=0o700)
        # A failed copy stays visibly incomplete and is never served or silently merged.
        _copy(source, stage)
        if _fingerprint(stage) != before or _fingerprint(source) != before:
            raise ValueError(f"Source changed or copy verification failed. Incomplete copy retained at {stage}")
        _verify_database(stage)
        _rebase_managed_metadata(stage, source, target)
        _rebind_native_sandboxes(stage, source, target)
        _rebase_database(stage, source, target)
        manifest = {**release, "password": password_record(password), "secure_cookie": secure_cookie,
                    "external_workspaces": external}
        manifest.pop("source_path", None)
        validate_manifest(manifest)
        _write(stage / MANIFEST, manifest)
        if os.name != "nt":
            (stage / MANIFEST).chmod(0o600)
        stage.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Engineering profile printed by Publish application")
    parser.add_argument("--release", help="Published release ID")
    parser.add_argument("--output", type=Path, help="New deployment data directory")
    parser.add_argument("--serve", type=Path, help="Restart an existing deployment without copying/resetting its data")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--allow-external-workspaces", action="store_true")
    parser.add_argument("--secure-cookie", action="store_true", help="For access through an HTTPS reverse proxy")
    parser.add_argument("--ask-password", action="store_true", help="Prompt privately instead of generating an access password")
    parser.add_argument("--reset-password", action="store_true", help="Rotate a stopped deployment's password with --serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=38474)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if args.reset_password and not args.serve:
        parser.error("--reset-password requires --serve")
    if args.serve:
        if args.source or args.release or args.output:
            parser.error("--serve cannot be combined with source/release/output")
        root = args.serve.resolve()
    else:
        if not args.source or not args.release:
            parser.error("Use --source and --release, or --serve")
        root = (args.output or args.source.with_name(args.source.name + ".deployments") / args.release).resolve()
        password = getpass.getpass("Application access password (12+ characters): ") if args.ask_password else secrets.token_urlsafe(24)
        create_deployment(args.source, root, args.release, password=password,
                          allow_external_workspaces=args.allow_external_workspaces, secure_cookie=args.secure_cookie)
        if not args.ask_password:
            print("Application access password (save it now): " + password, flush=True)
        print("Deployment data: " + str(root), flush=True)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    validate_manifest(manifest)
    if args.serve and (args.reset_password or args.secure_cookie):
        with file_lock(root / LOCK):
            if args.reset_password:
                from backend.deployment_runtime import password_record
                password = getpass.getpass("New application access password (12+ characters): ") if args.ask_password else secrets.token_urlsafe(24)
                if len(password) < 12:
                    raise ValueError("Use an access password of at least 12 characters")
                manifest["password"] = password_record(password)
                if not args.ask_password:
                    print("New application access password (save it now): " + password, flush=True)
            if args.secure_cookie:
                manifest["secure_cookie"] = True
            _write(root / MANIFEST, manifest)
    if args.prepare_only:
        return
    from backend.launcher import configure_logs, serve
    selected = Settings.for_data_root(root)
    runtime = manifest["runtime_settings"]
    selected = replace(selected, agent_runtime=runtime["agent_runtime"], sandbox_runtime=runtime["sandbox_runtime"],
                       plugin_directories=tuple(Path(p) for p in runtime["plugin_directories"]))
    configure_logs(selected)
    frontend = Path(__file__).resolve().parents[1] / "frontend/dist"
    if not (frontend / "index.html").is_file():
        parser.error("Frontend build missing; run setup or npm --prefix frontend run build")
    listener = socket.socket()
    if os.name == "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.host, args.port))
    listener.listen(128)
    listener.setblocking(False)
    print(f"Runtime listener: {args.host}:{args.port} · release {manifest['id']}", flush=True)
    try:
        asyncio.run(serve(selected, listener, frontend=frontend, open_browser=args.open))
    finally:
        listener.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, DomainError) as error:
        raise SystemExit(str(error)) from None
