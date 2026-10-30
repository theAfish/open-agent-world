"""Compatible, offline definition updates for an existing hosted profile.

Only authored graph metadata changes here. Documents, resource payloads, user
files, credentials and state are never restored from a template during updates.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from backend.application import write_setting
from backend.config import Settings
from backend.deploy import validate_manifest
from backend.deployments import PublishRequest, published_workspaces, release_spec
from backend.legions.models import LegionRecord
from backend.legions.validation import compatibility_issues
from backend.storage_location import LOCK, file_lock
from backend.world.models import CardBatchPatch, CardPatch

from .configuration import write_private_json
from .worker import WorkerConfig, create_worker_services, hosted_scope, prepare_hosted_binding

PROVENANCE = "hosted_template_provenance.v1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_MISSING = object()


def validate_source_nodes(template, blueprint, *, required=False):
    """Validate optional export lineage; legacy imports remain initializable."""
    source = template.get("source_nodes")
    identity = template.get("source_identity")
    if source is None and identity is None and not required:
        return None
    if (not isinstance(source, dict) or set(source) != {node.key for node in blueprint.nodes}
            or any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in source.values())
            or len(set(source.values())) != len(source)):
        raise ValueError("Template requires a complete, unique source-node lineage map")
    if not isinstance(identity, dict) or set(identity) != {
            "profile_id", "organization_id", "workspace_id", "world_id"}:
        raise ValueError("Template requires its source profile and owner lineage")
    try:
        if str(UUID(identity["profile_id"])) != identity["profile_id"]:
            raise ValueError()
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Invalid template source profile lineage") from error
    scope = [identity[key] for key in ("organization_id", "workspace_id", "world_id")]
    if not (all(value is None for value in scope) or all(
            isinstance(value, str) and 0 < len(value) <= 128 for value in scope)):
        raise ValueError("Invalid template source owner lineage")
    return source


def _roots(template):
    return template.get("root_keys", [template["root_key"]])


def _references(value, mapping):
    if isinstance(value, dict):
        return {key: _references(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_references(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def _structure(template):
    """Compare source identities, never capture ordering or display names."""
    source = template["source_nodes"]
    nodes = {}
    for node in template["blueprint"]["nodes"]:
        nodes[source[node["key"]]] = {
            key: _references(node.get(key), source) for key in (
                "type", "plugin_id", "parent_key", "owner_key", "equipment_relationship",
                "state_scope", "payload_version", "payload", "plugin_defaults", "finish",
            )
        }
    edges = [{key: _references(edge[key], source) for key in (
        "source", "target", "relationship", "plugin_id", "direction")}
        for edge in template["blueprint"]["edges"]]
    return {"nodes": nodes, "edges": sorted(edges, key=lambda edge: json.dumps(edge, sort_keys=True)),
            "roots": [source[key] for key in _roots(template)]}


def _editable(card):
    config = {key: value for key, value in card.config.items() if key != "status"}
    return {"name": card.name, "position": card.position.model_dump(), "size": card.size.model_dump(),
            "expanded": card.expanded, "config": config}


def record_template_provenance(services, template, node_ids):
    """Record authoritative instance IDs only at NEW-profile initialization."""
    from .templates import validate_template
    template, blueprint = validate_template(template)
    template = {**template, "blueprint": blueprint.model_dump(mode="json")}
    source = validate_source_nodes(template, blueprint)
    if source is None:
        return
    if set(node_ids) != set(source) or len(set(node_ids.values())) != len(node_ids):
        raise ValueError("Instantiated node IDs do not match the template lineage")
    value = {"version": 1, "source_identity": template["source_identity"],
             "source_release_id": template["source_release_id"], "structure": _structure(template),
             "node_ids": {source[key]: node_ids[key] for key in source},
             "baseline": {source[key]: _editable(services.world.get_card(node_ids[key])) for key in source}}
    with services.database.transaction(immediate=True) as db:
        if db.execute("SELECT 1 FROM application_settings WHERE key=?", (PROVENANCE,)).fetchone():
            raise ValueError("Existing template provenance requires explicit operator adoption")
        write_setting(db, PROVENANCE, value)


def recover_template_manifest(root: Path):
    """Complete a committed DB-to-manifest handoff under the profile lock.

    The caller must first verify hosted-profile.json against the worker owner.
    A failed/aborted DB transaction never publishes a pending manifest.
    """
    database = root / "database/world.sqlite3"
    if not database.is_file():
        return
    with closing(sqlite3.connect(database)) as db:
        row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (PROVENANCE,)).fetchone()
        if row is None:
            return
        value = json.loads(row[0])
        manifest = value.get("pending_manifest")
        if manifest is None:
            return
        validate_manifest(manifest)
        if manifest.get("auth_mode") != "hosted":
            raise ValueError("Pending template update requires hosted authentication")
        write_private_json(root / "deployment.json", manifest)
        del value["pending_manifest"]
        write_setting(db, PROVENANCE, value)
        db.commit()


def _merge(old, current, authored, path):
    """Three-way merge keeps user edits and refuses overlapping changes."""
    if authored == old or current == authored:
        return current
    if current == old:
        return authored
    if isinstance(old, dict) and isinstance(current, dict) and isinstance(authored, dict):
        result = {}
        for key in old.keys() | current.keys() | authored.keys():
            value = _merge(old.get(key, _MISSING), current.get(key, _MISSING),
                           authored.get(key, _MISSING), path + "." + key)
            if value is not _MISSING:
                result[key] = value
        return result
    raise ValueError(f"Template update conflicts with an existing user edit at {path}; explicit migration required")


def _require_runtime_structure(services, provenance):
    mapping = provenance["node_ids"]
    for source, expected in provenance["structure"]["nodes"].items():
        card = services.world.get_card(mapping[source])
        if (card.missing_plugin or card.type != expected["type"]
                or services.plugins.node_type_owner_id(card.type) != expected["plugin_id"]
                or card.parent_id != mapping.get(expected["parent_key"])
                or (card.equipment.owner_id if card.equipment else None) != mapping.get(expected["owner_key"])
                or (card.equipment.relationship if card.equipment else None) != expected["equipment_relationship"]
                or card.state_scope_override != expected["state_scope"]):
            raise ValueError("Runtime node structure changed; explicit workspace migration required")
    # Changing authored connections can revoke mounts or affect execution. The
    # compatible path keeps them stable rather than bypassing their lifecycle.
    reverse = {value: key for key, value in mapping.items()}
    actual = [{"source": reverse[edge.source], "target": reverse[edge.target],
               "relationship": edge.relationship, "plugin_id": services.plugins.relationship_owner_id(edge.relationship),
               "direction": edge.direction}
              for edge in services.world.list_edges()
              if edge.source in reverse and edge.target in reverse
              and not services.plugins.relationship(edge.relationship).generated]
    actual.sort(key=lambda edge: json.dumps(edge, sort_keys=True))
    if actual != provenance["structure"]["edges"]:
        raise ValueError("Runtime connections changed; explicit workspace migration required")


async def update_runtime(config: WorkerConfig, template_path: Path, *, services_factory=None):
    """Apply a compatible release to a stopped runtime without changing identity."""
    from .templates import validate_template
    if template_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Hosted template exceeds 64 MiB")
    template, blueprint = validate_template(json.loads(template_path.read_text(encoding="utf-8")))
    template = {**template, "blueprint": blueprint.model_dump(mode="json")}
    source = validate_source_nodes(template, blueprint, required=True)
    root = config.data_root
    database = root / "database/world.sqlite3"
    if config.mode != "runtime" or not database.is_file() or not (root / "hosted-profile.json").is_file():
        raise ValueError("Template update requires an existing bound runtime profile")
    with file_lock(root / LOCK):
        prepare_hosted_binding(root, profile_id=config.profile_id, user_id=config.user_id,
                               application_id=config.application_id)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (PROVENANCE,)).fetchone()
            if row is None:
                raise ValueError("Profile has no template provenance; explicit operator adoption required")
            provenance = json.loads(row[0])
            if provenance.get("version") != 1 or provenance.get("source_identity") != template["source_identity"]:
                raise ValueError("Template source owner or profile lineage differs from this runtime")
            if provenance["structure"] != _structure(template):
                raise ValueError("Template node, connection, payload or workspace scope changed; explicit migration required")
            if db.execute("SELECT 1 FROM runs WHERE status IN ('created','running','waiting') LIMIT 1").fetchone():
                raise ValueError("Finish or stop active runs before updating the template")
        recover_template_manifest(root)
        old_manifest = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
        validate_manifest(old_manifest)
        if old_manifest.get("auth_mode") != "hosted":
            raise ValueError("Template update requires hosted deployment authentication")
        expected_roots = [provenance["node_ids"][key] for key in provenance["structure"]["roots"]]
        if [workspace["legion_id"] for workspace in published_workspaces(old_manifest)] != expected_roots:
            raise ValueError("Published workspace scope or order changed; explicit migration required")
        settings = replace(Settings.for_data_root(root), agent_runtime=config.agent_runtime, sandbox_runtime=None,
                           marketplace_url=None, inherit_runtime_environment=False,
                           tenant_scope=hosted_scope(config.user_id, config.application_id, config.profile_id))
        services = (services_factory or create_worker_services)(settings)
        try:
            created_at = datetime.fromisoformat(template["created_at"])
            record = LegionRecord(id=template["source_release_id"], name=template["name"], description="Hosted template update",
                                  blueprint=blueprint, created_at=created_at, updated_at=created_at, revision=1)
            if issues := compatibility_issues(record, services.plugins):
                raise ValueError("Template is incompatible with the installed Packs: " + "; ".join(issues))
            _require_runtime_structure(services, provenance)
            for node_id in provenance["node_ids"].values():
                services.background_jobs.assert_idle(card_id=node_id)
                services.resources.artifacts.assert_source_idle(node_id)
                services.node_execution.assert_editable(node_id, all_states=True)
            node_ids = {key: provenance["node_ids"][source[key]] for key in source}
            updates, authored_baseline = [], {}
            for node in blueprint.nodes:
                card = services.world.get_card(node_ids[node.key])
                definition = services.plugins.node_type(card.type)
                authored_config = (definition.template_remap_config(deepcopy(node.config), node_ids)
                                   if definition.template_remap_config else deepcopy(node.config))
                authored_config = services.plugins.validate_config(card.type, authored_config)
                authored_config.pop("status", None)
                authored = {"name": node.name, "position": node.position.model_dump(),
                            "size": node.size.model_dump(), "expanded": node.expanded, "config": authored_config}
                merged = _merge(provenance["baseline"][source[node.key]], _editable(card), authored, node.key)
                if set(card.config) - {"status"} - set(merged["config"]):
                    raise ValueError("Removing configuration fields requires an explicit migration")
                if card.type in {"text", "image"} and merged["config"].get("filename") != card.config.get("filename"):
                    raise ValueError("Resource filename changes require an explicit migration")
                # Lifecycle-specific config transforms need an explicit plugin
                # migration. Core offline config is reconciled at worker startup.
                if (merged["config"] != _editable(card)["config"] and definition.lifecycle is not None
                        and type(definition.lifecycle).__module__ != "backend.plugins.builtin"):
                    from backend.plugins.lifecycle import NodeLifecycleHandler
                    if type(definition.lifecycle).prepare_update is not NodeLifecycleHandler.prepare_update:
                        raise ValueError("Pack configuration update requires an explicit lifecycle migration")
                authored_baseline[source[node.key]] = authored
                if merged != _editable(card):
                    updates.append(CardBatchPatch(node_id=card.id, patch=CardPatch(
                        **{**merged, "config": {**merged["config"], "status": card.status}},
                        expected_revision=card.revision)))
            with services.database.transaction(immediate=True) as db:
                services.world.update_cards(updates, _connection=db)
                manifest = release_spec(services, PublishRequest(
                    legion_ids=expected_roots, name=template["name"], allow_terminal=template.get("allow_terminal", False)))
                manifest.update(id=old_manifest["id"], created_at=old_manifest["created_at"], schema_version=2,
                                auth_mode="hosted", source_release_id=template["source_release_id"],
                                runtime_settings=old_manifest["runtime_settings"])
                validate_manifest(manifest)
                value = {**provenance, "source_release_id": template["source_release_id"],
                         "baseline": authored_baseline, "pending_manifest": manifest}
                write_setting(db, PROVENANCE, value)
        finally:
            services.close()
        recover_template_manifest(root)
        return {"profile": config.profile_id, "ready": True, "source_release_id": template["source_release_id"],
                "updated_nodes": [item.node_id for item in updates]}
