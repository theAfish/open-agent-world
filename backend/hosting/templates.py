"""Provision private runtime data from portable Legion definitions, never profile copies."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, UTC
import json
from pathlib import Path
from uuid import UUID, uuid4

from backend.config import Settings
from backend.deployments import PREFIX, PublishRequest, configuration_digest, release_spec
from backend.legions.models import LegionBlueprint, LegionCapture, LegionInstantiate, LegionRecord
from backend.storage_location import LOCK, file_lock
from .configuration import write_private_json
from .worker import WorkerConfig, create_worker_services, hosted_scope, prepare_hosted_binding
from .policy import unsafe_host_configuration


def _portable(value):
    """Reject known embedded credential/local-machine fields in portable config."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"api_key", "api_key_encrypted", "password", "access_token", "refresh_token",
                               "client_secret", "private_key", "access_key_secret", "authorization"} and item:
                raise ValueError("Remove embedded credentials before exporting a hosted template")
            if key in {"runtime_provider_id", "provider_id"} and item == "openai.codex":
                raise ValueError("Local Codex cannot be included in a hosted template")
            _portable(item)
    elif isinstance(value, list):
        for item in value:
            _portable(item)


def validate_template(template) -> tuple[dict, LegionBlueprint]:
    """Validate the complete import before creating any user database or files."""
    from backend.security.model_connections import CatalogEdit, ModelCatalog
    fields = {"version", "name", "source_release_id", "root_key", "created_at",
              "allow_terminal", "blueprint", "model_connections"}
    required = fields - {"model_connections", "allow_terminal"}
    if (not isinstance(template, dict) or set(template) - fields or not required <= set(template)
            or type(template.get("version")) is not int or template["version"] != 1):
        raise ValueError("Unsupported or incomplete hosted template")
    if (not isinstance(template["name"], str) or not template["name"].strip() or len(template["name"]) > 120
            or type(template.get("allow_terminal", False)) is not bool
            or not isinstance(template["root_key"], str)):
        raise ValueError("Invalid hosted template metadata")
    try:
        if str(UUID(template["source_release_id"])) != template["source_release_id"]:
            raise ValueError("Invalid source release")
        datetime.fromisoformat(template["created_at"])
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Invalid hosted template release metadata") from error
    blueprint = LegionBlueprint.model_validate(template["blueprint"])
    if type(blueprint.format_version) is not int or blueprint.format_version != 1 or not 1 <= len(blueprint.nodes) <= 101:
        raise ValueError("Unsupported hosted blueprint version or size")
    nodes = {node.key: node for node in blueprint.nodes}
    root_key = template["root_key"]
    if root_key not in nodes or nodes[root_key].type != "legion" or nodes[root_key].parent_key or nodes[root_key].owner_key:
        raise ValueError("Template must identify its published Legion root")
    for node in blueprint.nodes:
        parent = node
        while parent.key != root_key:
            parent = nodes.get(parent.owner_key or parent.parent_key)
            if parent is None:
                raise ValueError("Hosted template contains nodes outside the published Legion")
        if node.initial_shared_state is not None:
            raise ValueError("Hosted templates cannot import private shared state")
        value = node.model_dump(mode="json")
        _portable(value)
        if unsafe_host_configuration(value):
            raise ValueError("Hosted templates must not contain host filesystem bindings or local Codex agents")
    if (len(blueprint.edges) > 10000 or len({edge.key for edge in blueprint.edges}) != len(blueprint.edges)
            or any(edge.source not in nodes or edge.target not in nodes for edge in blueprint.edges)):
        raise ValueError("Hosted template edges must stay inside the published Legion")
    if template.get("model_connections") is not None:
        catalog = ModelCatalog.model_validate(template["model_connections"])
        CatalogEdit.model_validate(catalog.model_dump())
        _portable(template["model_connections"])
    return template, blueprint


async def export_template(source: Path, release_id: str, target: Path) -> dict:
    """Export the published Legion's selected current content and portable config.

    Conversation sessions, runs, attachments, application secrets, profile files
    and unrelated cards never cross this boundary. Plugin document.capture owns
    which current document fields are intended as reusable initial content.
    """
    from backend.services import create_services
    from backend.security.model_connections import ModelConnectionStore
    source, target = source.resolve(), target.resolve()
    if target.exists():
        raise ValueError("Template destination already exists")
    if not (source / "database/world.sqlite3").is_file() or (source / "deployment.json").exists():
        raise ValueError("Choose a stopped engineering profile containing the published workspace")
    with file_lock(source / LOCK):
        settings = replace(Settings.for_data_root(source), agent_runtime=None, sandbox_runtime=None)
        services = create_services(settings)
        try:
            with services.database.locked() as db:
                row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (PREFIX + release_id,)).fetchone()
                if not row:
                    raise ValueError("Publish the workspace before exporting a hosted template")
                release = json.loads(row[0])
                if release["configuration_digest"] != configuration_digest(db):
                    raise ValueError("Configuration changed; publish a new release before exporting")
                if db.execute("SELECT 1 FROM runs WHERE status IN ('created','running','waiting') LIMIT 1").fetchone():
                    raise ValueError("Finish or stop active runs before exporting")
            group = services.world.get_card(release["legion_id"])
            nodes = [group, *services.world.owned_descendants(group.id)]
            ids = {node.id for node in nodes}
            if any((edge.source in ids) != (edge.target in ids) for edge in services.world.list_edges()):
                raise ValueError("Hosted templates require a self-contained Legion; include external dependencies in the Legion")
            request = LegionCapture(name=release["name"], node_ids=[node.id for node in nodes])
            async with services._portable_state_gate.capture():
                async with services._node_mutation():
                    blueprint, node_keys = await services._capture_subgraph_locked(request)
            for node in blueprint.nodes:
                node.initial_shared_state = None
            models = ModelConnectionStore(services.llm_settings).read().model_dump(mode="json")
            models["revision"] = 0
            for connection in models["connections"]:
                connection.pop("api_key_configured", None)
            value = {"version": 1, "name": release["name"], "source_release_id": release_id,
                     "root_key": node_keys[group.id], "created_at": datetime.now(UTC).isoformat(),
                     "allow_terminal": any("terminal" in operations for operations in release["permissions"].values()),
                     "blueprint": blueprint.model_dump(mode="json"), "model_connections": models}
            validate_template(value)
            write_private_json(target, value)
            return value
        finally:
            services.close()


async def initialize_runtime(config: WorkerConfig, template_path: Path) -> dict:
    """Instantiate into a NEW profile; restarting never copies or resets user data."""
    if template_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Hosted template exceeds 64 MiB")
    template, blueprint = validate_template(json.loads(template_path.read_text(encoding="utf-8")))
    root = config.data_root
    with file_lock(root / LOCK):
        if (root / "database/world.sqlite3").exists():
            raise ValueError("Profile already contains data; provisioning never overwrites an existing profile")
        prepare_hosted_binding(root, profile_id=config.profile_id, user_id=config.user_id, application_id=config.application_id)
        settings = replace(Settings.for_data_root(root), agent_runtime=config.agent_runtime, sandbox_runtime=None,
                           tenant_scope=hosted_scope(config.user_id, config.application_id, config.profile_id))
        services = create_worker_services(settings)
        try:
            now = datetime.now(UTC)
            record = LegionRecord(id=str(uuid4()), name=template["name"], description="Hosted application template",
                                  blueprint=blueprint, created_at=now, updated_at=now, revision=1)
            instance = await services.instantiate_legion(record.id, LegionInstantiate(), record=record)
            # Public model definitions are portable; credentials are explicitly
            # configured per worker by the operator after provisioning.
            if template.get("model_connections"):
                from backend.application import write_setting
                from backend.security.model_connections import ModelCatalog
                catalog = ModelCatalog.model_validate(template["model_connections"])
                clean = catalog.model_dump(mode="json")
                for connection in clean["connections"]:
                    connection.pop("api_key_configured", None)
                with services.database.transaction(immediate=True) as db:
                    write_setting(db, "model_connections", clean)
            manifest = release_spec(services, PublishRequest(
                legion_id=instance.node_ids[template["root_key"]], name=template["name"],
                allow_terminal=template.get("allow_terminal", False)))
            manifest.update(schema_version=2, auth_mode="hosted", source_release_id=template["source_release_id"])
            manifest["runtime_settings"] = {"agent_runtime": config.agent_runtime, "sandbox_runtime": None, "plugin_directories": []}
            from backend.deploy import validate_manifest
            validate_manifest(manifest)
            write_private_json(root / "deployment.json", manifest)
            return manifest
        finally:
            services.close()
