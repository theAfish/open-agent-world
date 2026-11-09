"""Edit shared Legion definitions using an isolated, persistent development profile."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from uuid import uuid4

from backend.config import Settings
from backend.development import prepare_profile, profile_path, validate_profile
from backend.errors import DomainError
from backend.legions.models import LegionBlueprintPreset, LegionCapture, LegionInstantiate, LegionRecord
from backend.legions.presets import plugin_preset_record, preset_record
from backend.packs.content import CreatorRequest, MAX_TEMPLATE_BYTES, prepare_export
from backend.services import create_services
from backend.storage_location import file_lock

ROOT = Path(__file__).resolve().parents[1]
LINEAGE = "poc.definition."


@contextmanager
def open_profile(name: str, *, initialize: bool = False):
    """Offline editing, with the same exclusive storage lock as the launcher.

    Like hosted template export, do not start services: startup would resume
    queued conversations and background jobs just to save a definition.
    """
    root = prepare_profile(name) if initialize else profile_path(name)
    validate_profile(root)
    if (root / "deployment.json").exists():
        raise ValueError("POC commands require an editable development profile, not a deployment")
    with file_lock(root / ".oaw-storage.lock"):
        settings = replace(Settings.for_data_root(root), application_mode="development",
            plugin_directories=tuple(Path(p).expanduser().resolve() for p in
                os.environ.get("OPEN_AGENT_WORLD_PLUGIN_DIRS", "").split(os.pathsep) if p))
        services = create_services(settings)
        try:
            with services.database.locked() as db:
                if db.execute("SELECT 1 FROM runs WHERE status IN ('created','running','waiting') LIMIT 1").fetchone():
                    raise ValueError("Finish or stop active runs in OAW before editing this profile offline")
            yield services
        finally:
            services.close()


def read_template(path: Path) -> LegionBlueprintPreset:
    with path.open("rb") as stream:
        data = stream.read(MAX_TEMPLATE_BYTES + 1)
    if len(data) > MAX_TEMPLATE_BYTES:
        raise ValueError("Legion definition exceeds the 64 MiB portable-state limit")
    return LegionBlueprintPreset.model_validate_json(data)


async def apply_definition(services, *, preset: str | None = None, template: Path | None = None,
                           initialize: bool = False):
    # No separate POC instance registry: the existing world is the source of truth.
    cards = services.world.list_cards()
    if initialize and cards:
        return {"status": "reused", "message": "Existing cards preserved; use apply to add another instance."}
    record = (plugin_preset_record(read_template(template), services.plugins) if template
              else preset_record(preset, services.plugins))
    # Put updated instances beside the current work, never on top of it.
    x = max((card.position.x + card.size.width for card in cards if not card.parent_id), default=-120) + 120
    instance = await services.instantiate_legion(record.id,
        LegionInstantiate(position={"x": x, "y": 100}, as_group=True), record=record)
    from backend.application import write_setting
    for node_id in instance.node_ids.values():
        card = services.world.get_card(node_id)
        if card.type == "legion" and card.parent_id is None:
            with services.database.transaction(immediate=True) as db:
                write_setting(db, LINEAGE + node_id, {"id": record.id,
                    "keys": {value: key for key, value in instance.node_ids.items()}})
    return {"status": "created", "name": record.name, "node_ids": instance.node_ids}


def select_group(services, selector: str):
    groups = services.world.list_legion_groups()
    matches = [card for card in groups if card.id == selector] or [card for card in groups if card.name == selector]
    if len(matches) != 1:
        raise ValueError("Choose a unique Legion name or ID from the list command")
    return matches[0]


async def save_definition(services, selector: str, target: Path, *, include_state=(), identifier="local.poc.legion"):
    previous = read_template(target) if target.exists() else None
    group = select_group(services, selector)
    nodes = [group, *services.world.owned_descendants(group.id)]
    ids = {node.id for node in nodes}
    if any((edge.source in ids) != (edge.target in ids) for edge in services.world.list_edges()):
        raise ValueError("Move external connected cards inside the Legion before saving it")
    with services.database.locked() as db:
        row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (LINEAGE + group.id,)).fetchone()
    lineage = json.loads(row[0]) if row else None
    keys = ({node.id: lineage["keys"].get(node.id, "node-" + uuid4().hex) for node in nodes} if lineage else None)
    # Reuse card-owned capture/remapping and the public content export checks.
    async with services._portable_state_gate.capture(), services._node_mutation():
        blueprint, captured_keys = await services._capture_subgraph_locked(
            LegionCapture(name=group.name, node_ids=[node.id for node in nodes]), node_keys=keys)
    selected = []
    for name in include_state:
        matches = [node.key for node in blueprint.nodes if node.key == name or node.name == name]
        if len(matches) != 1:
            raise ValueError(f"Initial content must identify one card by name or template key: {name}")
        selected.append(matches[0])
    now = datetime.now(UTC)
    record = LegionRecord(id="poc", name=group.name, description=group.config.get("description", ""),
        blueprint=blueprint, revision=previous.revision if previous else 1, created_at=now, updated_at=now)
    report, files = prepare_export(record, CreatorRequest(legion_id=record.id,
        id="local.poc", name=record.name, include_state_nodes=selected), services.plugins)
    if not report["can_export"]:
        raise ValueError("Definition cannot be shared: " + "; ".join(
            f"{issue['path']}: {issue['message']}" for issue in report["issues"] if issue["severity"] == "error"))
    definition = LegionBlueprintPreset.model_validate_json(files["content/legion.json"]).model_copy(
        update={"id": previous.id if previous else identifier})
    services.plugins.validate_identifier(definition.id, "Legion preset")
    if previous and definition != previous:
        definition = definition.model_copy(update={"revision": previous.revision + 1})
    target.parent.mkdir(parents=True, exist_ok=True)
    # A failed write must leave the previous Git-tracked definition intact.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                dir=target.parent, prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(definition.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        temporary.replace(target)
        from backend.application import write_setting
        with services.database.transaction(immediate=True) as db:
            write_setting(db, LINEAGE + group.id, {"id": definition.id, "keys": captured_keys})
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"status": "saved", "path": str(target), "revision": definition.revision, "issues": report["issues"]}


async def prepare_preview(name: str, selector: str, *, allow_terminal=False, agent_runtime="google.adk"):
    """Publish through the real access contract, then snapshot only stopped local data."""
    from backend.deploy import create_deployment
    from backend.deployments import PublishRequest, publish
    with open_profile(name) as services:
        try:
            # Offline editing deliberately has no execution runtime. The snapshot
            # must instead use the same runtime defaults as the source launcher.
            services.settings = replace(services.settings, agent_runtime=agent_runtime,
                sandbox_runtime=os.environ.get("OPEN_AGENT_WORLD_SANDBOX_RUNTIME", "auto"))
            group = select_group(services, selector)
            release = await publish(PublishRequest(legion_id=group.id, name=group.name,
                                                  allow_terminal=allow_terminal), services)
        finally:
            await services.shutdown()
    root = profile_path(name).parents[1] / "previews" / name / release["id"]
    password = secrets.token_urlsafe(24)
    create_deployment(profile_path(name), root, release["id"], password=password)
    return {"path": str(root), "password": password, "release_id": release["id"]}


async def check_definition(path: Path, profile="poc"):
    """Validate clean instantiation and the same publication contract used online."""
    from backend.deployments import PublishRequest, release_spec
    from backend.hosting.scenarios import template_from_definition
    definition = read_template(path)
    template = template_from_definition(definition)
    # No author world, model secrets, sessions or cloud bindings enter this check.
    with open_profile(profile) as author, tempfile.TemporaryDirectory(prefix="oaw-poc-check-") as temporary:
        settings = replace(Settings.for_data_root(Path(temporary)), agent_runtime=None, sandbox_runtime=None)
        services = create_services(settings, plugins=author.plugins)
        try:
            record = plugin_preset_record(definition, services.plugins)
            instance = await services.instantiate_legion(record.id, LegionInstantiate(), record=record)
            release = release_spec(services, PublishRequest(legion_id=instance.node_ids[template["root_key"]], name=definition.name))
            return {"status": "ready", "id": definition.id, "release_id": template["source_release_id"],
                    "panels": len(release["panels"]), "cards": len(instance.node_ids)}
        finally:
            await services.shutdown()
            services.close()
            await author.shutdown()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "apply", "save", "list", "start", "preview", "check"):
        command = commands.add_parser(name)
        command.add_argument("--profile", default="poc", help="Checkout-local development profile (default: poc)")
        if name in {"init", "apply"}:
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--preset", help="An installed Legion preset ID")
            source.add_argument("--template", type=Path, help="A saved LegionBlueprintPreset JSON file")
        elif name == "save":
            command.add_argument("target", type=Path)
            command.add_argument("--legion", required=True, help="Live Legion name or ID (see list)")
            command.add_argument("--id", default="local.poc.legion", help="ID for a new definition; existing IDs are retained")
            command.add_argument("--include-state", action="append", default=[], metavar="CARD",
                                 help="Explicitly share this card's initial content (name or template key); repeatable")
        elif name == "check":
            command.add_argument("--template", type=Path, default=ROOT / "examples/poc/legion.json")
        elif name in {"start", "preview"}:
            command.add_argument("--backend-port", type=int, default=8000)
            command.add_argument("--frontend-port", type=int, default=5173)
            command.add_argument("--agent-runtime", default="google.adk")
            if name == "preview":
                command.add_argument("--legion", required=True)
                command.add_argument("--allow-terminal", action="store_true")
                command.add_argument("--prepare-only", action="store_true")
    return result


async def edit(args):
    with open_profile(args.profile, initialize=args.command == "init") as services:
        try:
            if args.command in {"init", "apply"}:
                return await apply_definition(services, preset=args.preset, template=args.template, initialize=args.command == "init")
            if args.command == "save":
                return await save_definition(services, args.legion, args.target, include_state=args.include_state, identifier=args.id)
            from backend.legions.presets import PRESETS
            return {"legions": [{"id": card.id, "name": card.name} for card in services.world.list_legion_groups()],
                    "presets": [*PRESETS, *(preset.id for preset in services.plugins.legion_presets())]}
        finally:
            # Creation hooks may own clients/threads even without startup (KB).
            await services.shutdown()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            print(json.dumps(asyncio.run(check_definition(args.template, args.profile)), ensure_ascii=False, indent=2))
            return 0
        if args.command in {"start", "preview"}:
            root = profile_path(args.profile)
            validate_profile(root)
            if (root / "deployment.json").exists():
                raise ValueError("Use an editable development profile")
            command = [sys.executable, str(ROOT / "scripts/dev.py"), "--profile", args.profile,
                "--backend-port", str(args.backend_port), "--frontend-port", str(args.frontend_port)]
            if args.agent_runtime != "google.adk":
                command += ["--agent-runtime", args.agent_runtime]
            if args.command == "preview":
                result = asyncio.run(prepare_preview(args.profile, args.legion, allow_terminal=args.allow_terminal,
                                                    agent_runtime=args.agent_runtime))
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                if args.prepare_only:
                    return 0
                command += ["--deployment", result["path"]]
            return subprocess.call(command, cwd=ROOT)
        print(json.dumps(asyncio.run(edit(args)), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, DomainError) as exc:
        print(f"POC: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
