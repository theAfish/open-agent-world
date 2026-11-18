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
import subprocess
import sys
import tempfile

from backend.config import Settings
from backend.development import prepare_profile, profile_path, validate_profile
from backend.errors import DomainError
from backend.legions.models import LegionBlueprintPreset, LegionCapture, LegionInstantiate, LegionRecord
from backend.legions.presets import plugin_preset_record, preset_record
from backend.packs.content import CreatorRequest, MAX_TEMPLATE_BYTES, prepare_export
from backend.services import create_services
from backend.storage_location import file_lock

ROOT = Path(__file__).resolve().parents[1]


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
    # Reuse card-owned capture/remapping and the public content export checks.
    async with services._portable_state_gate.capture(), services._node_mutation():
        blueprint, _ = await services._capture_subgraph_locked(
            LegionCapture(name=group.name, node_ids=[node.id for node in nodes]))
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
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"status": "saved", "path": str(target), "revision": definition.revision, "issues": report["issues"]}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "apply", "save", "list", "start"):
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
        elif name == "start":
            command.add_argument("--backend-port", type=int, default=8000)
            command.add_argument("--frontend-port", type=int, default=5173)
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
        if args.command == "start":
            root = profile_path(args.profile)
            validate_profile(root)
            if (root / "deployment.json").exists():
                raise ValueError("Use an editable development profile")
            return subprocess.call([sys.executable, str(ROOT / "scripts/dev.py"), "--profile", args.profile,
                "--backend-port", str(args.backend_port), "--frontend-port", str(args.frontend_port)], cwd=ROOT)
        print(json.dumps(asyncio.run(edit(args)), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, DomainError) as exc:
        print(f"POC: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
