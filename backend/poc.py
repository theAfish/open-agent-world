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
            marketplace_url=Settings.from_environment().marketplace_url,
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


async def install_pack(name: str, path: Path):
    """Explicit setup only; reuse the ordinary Pack installer and profile lock."""
    from backend.packs.archive import MAX_ARCHIVE_BYTES, inspect_archive
    with path.open("rb") as stream:
        data = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Pack archive exceeds the installation limit")
    with open_profile(name, initialize=True) as services:
        try:
            pack = inspect_archive(data)
            for installed in services.pack_installations.status()["versions"]:
                if (installed["id"] == pack.manifest.id and installed["version"] == pack.manifest.version
                        and installed["digest"] == pack.digest and installed["selected"]):
                    return {"status": "reused", "id": installed["id"], "version": installed["version"]}
            return services.pack_installations.install(data)
        finally:
            await services.shutdown()


async def prepare_dependencies(name, requirements, *, install=False, archives=()):
    from backend.poc_dependencies import dependency_status, read_requirements, synchronize
    required = read_requirements(requirements)
    with open_profile(name, initialize=install) as services:
        try:
            status = (await synchronize(services, required, archives) if install else
                      dependency_status(services, required))
            ready = all(row["status"] == "ready" for row in status)
            return {"status": "ready" if ready else "missing",
                    "packs": status, "next_step": None if ready
                    else f"python scripts/poc.py setup --profile {name}"}
        finally:
            await services.shutdown()


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
    roots = [instance.node_ids[node.key] for node in record.blueprint.nodes
             if node.type == "legion" and node.parent_key is None and node.owner_key is None]
    for node_id in instance.node_ids.values():
        card = services.world.get_card(node_id)
        if card.type == "legion" and card.parent_id is None:
            with services.database.transaction(immediate=True) as db:
                write_setting(db, LINEAGE + node_id, {"id": record.id,
                    "keys": {value: key for key, value in instance.node_ids.items()}, "roots": roots})
    return {"status": "created", "name": record.name, "node_ids": instance.node_ids}


def select_group(services, selector: str):
    groups = services.world.list_legion_groups()
    matches = [card for card in groups if card.id == selector] or [card for card in groups if card.name == selector]
    if len(matches) != 1:
        raise ValueError("Choose a unique Legion name or ID from the list command")
    return matches[0]


def select_groups(services, selectors=None):
    if isinstance(selectors, str):
        selectors = [selectors]
    groups = ([select_group(services, selector) for selector in selectors] if selectors else
              [card for card in services.world.list_legion_groups() if card.parent_id is None])
    if not groups or len({group.id for group in groups}) != len(groups):
        raise ValueError("Choose at least one distinct Legion")
    if not selectors:
        selected = {group.id: group for group in groups}
        ordered = {}
        with services.database.locked() as db:
            for group in groups:
                row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (LINEAGE + group.id,)).fetchone()
                for identifier in json.loads(row[0]).get("roots", []) if row else []:
                    if identifier in selected:
                        ordered[identifier] = selected[identifier]
                ordered[group.id] = group
        groups = list(ordered.values())
    return groups


async def save_definition(services, selector, target: Path, *, include_state=(), identifier="local.poc.legion"):
    previous = read_template(target) if target.exists() else None
    groups = select_groups(services, selector)
    nodes = list({node.id: node for group in groups for node in
                  [group, *services.world.owned_descendants(group.id)]}.values())
    ids = {node.id for node in nodes}
    if any((edge.source in ids) != (edge.target in ids) for edge in services.world.list_edges()):
        raise ValueError("Include external connected cards in the selected Legions before saving")
    lineage_keys = {}
    with services.database.locked() as db:
        for group in groups:
            row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (LINEAGE + group.id,)).fetchone()
            if row:
                lineage_keys.update(json.loads(row[0])["keys"])
    keys = {}
    used = set()
    for node in nodes:
        key = lineage_keys.get(node.id)
        if not key or key in used:
            key = "node-" + uuid4().hex
        keys[node.id] = key
        used.add(key)
    name = previous.name if previous else " / ".join(group.name for group in groups)[:120]
    # Reuse card-owned capture/remapping and the public content export checks.
    async with services._portable_state_gate.capture(), services._node_mutation():
        blueprint, captured_keys = await services._capture_subgraph_locked(
            LegionCapture(name=name, node_ids=[node.id for node in nodes]), node_keys=keys)
    captured = {node.key: node for node in blueprint.nodes}
    for node in nodes:
        if node.type == "sandbox":
            # Like hosted template export, retain the authored execution policy,
            # while the normal capture still removes paths and host bindings.
            captured[captured_keys[node.id]].config.update({key: node.config[key] for key in (
                "network_enabled", "memory_bytes", "active_process_limit", "command_timeout",
            ) if key in node.config})
    if previous:
        # Newly instantiated cards/edges can share timestamps and get a different
        # database order. Preserve the reviewed definition order and edge keys.
        node_order = {node.key: index for index, node in enumerate(previous.blueprint.nodes)}
        blueprint.nodes.sort(key=lambda node: (node_order.get(node.key, len(node_order)), node.key))
        def edge_identity(edge):
            return (edge.source, edge.target, edge.relationship, edge.plugin_id, edge.direction)
        old_edges = {edge_identity(edge): edge.key for edge in previous.blueprint.edges}
        edge_order = {edge.key: index for index, edge in enumerate(previous.blueprint.edges)}
        for edge in blueprint.edges:
            edge.key = old_edges.get(edge_identity(edge), "edge-" + uuid4().hex)
        blueprint.edges.sort(key=lambda edge: (edge_order.get(edge.key, len(edge_order)), edge.key))
    selected = []
    for selected_name in include_state:
        matches = [node.key for node in blueprint.nodes if node.key == selected_name or node.name == selected_name]
        if len(matches) != 1:
            raise ValueError(f"Initial content must identify one card by name or template key: {selected_name}")
        selected.append(matches[0])
        # A container's portable content includes its owned skill/document cards.
        source = next(node for node in nodes if captured_keys[node.id] == matches[0])
        selected.extend(captured_keys[node.id] for node in services.world.owned_descendants(source.id))
    now = datetime.now(UTC)
    record = LegionRecord(id="poc", name=name,
        description=previous.description if previous else groups[0].config.get("description", ""),
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
            for group in groups:
                write_setting(db, LINEAGE + group.id, {"id": definition.id, "keys": captured_keys,
                                                       "roots": [group.id for group in groups]})
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"status": "saved", "path": str(target), "revision": definition.revision, "issues": report["issues"]}


async def prepare_preview(name: str, selector=None, *, allow_terminal=False, agent_runtime="google.adk"):
    """Publish through the real access contract, then snapshot only stopped local data."""
    from backend.deploy import create_deployment
    from backend.deployments import PublishRequest, publish
    with open_profile(name) as services:
        try:
            # Offline editing deliberately has no execution runtime. The snapshot
            # must instead use the same runtime defaults as the source launcher.
            services.settings = replace(services.settings, agent_runtime=agent_runtime,
                sandbox_runtime=os.environ.get("OPEN_AGENT_WORLD_SANDBOX_RUNTIME", "auto"))
            groups = select_groups(services, selector)
            release = await publish(PublishRequest(legion_ids=[group.id for group in groups], name=" / ".join(group.name for group in groups)[:120],
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
            release = release_spec(services, PublishRequest(
                legion_ids=[instance.node_ids[key] for key in template["root_keys"]], name=definition.name))
            return {"status": "ready", "id": definition.id, "release_id": template["source_release_id"],
                    "panels": sum(len(w["panels"]) for w in release["workspaces"]),
                    "workspaces": len(release["workspaces"]), "cards": len(instance.node_ids)}
        finally:
            await services.shutdown()
            services.close()
            await author.shutdown()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "apply", "save", "list", "start", "preview", "check", "install-pack", "deps"):
        command = commands.add_parser(name)
        command.add_argument("--profile", default="poc", help="Checkout-local development profile (default: poc)")
        if name in {"init", "apply"}:
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--preset", help="An installed Legion preset ID")
            source.add_argument("--template", type=Path, help="A saved LegionBlueprintPreset JSON file")
        elif name == "save":
            command.add_argument("target", type=Path)
            command.add_argument("--legion", action="append", help="Live Legion name or ID; repeat, or omit for all top-level Legions")
            command.add_argument("--id", default="local.poc.legion", help="ID for a new definition; existing IDs are retained")
            command.add_argument("--include-state", action="append", default=[], metavar="CARD",
                                 help="Share this card/container's initial content, including owned cards; repeatable")
        elif name == "install-pack":
            command.add_argument("path", type=Path)
        elif name == "deps":
            command.add_argument("--requirements", type=Path, default=ROOT / "examples/poc/packs.toml")
            command.add_argument("--install", action="store_true", help="Download/install missing pinned Packs")
            command.add_argument("--pack", type=Path, action="append", default=[], help="Exact-version local archive override")
        elif name == "check":
            command.add_argument("--template", type=Path, default=ROOT / "examples/poc/legion.json")
        elif name in {"start", "preview"}:
            command.add_argument("--backend-port", type=int, default=8000)
            command.add_argument("--frontend-port", type=int, default=5173)
            command.add_argument("--agent-runtime", default="google.adk")
            if name == "preview":
                command.add_argument("--legion", action="append", help="Repeat to select workspaces; omit for all top-level Legions")
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
        if args.command == "deps":
            if args.pack and not args.install:
                raise ValueError("--pack requires --install")
            result = asyncio.run(prepare_dependencies(args.profile, args.requirements,
                install=args.install, archives=args.pack))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "ready" else 1
        if args.command == "install-pack":
            print(json.dumps(asyncio.run(install_pack(args.profile, args.path)), ensure_ascii=False, indent=2))
            return 0
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
