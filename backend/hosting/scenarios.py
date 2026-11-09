"""Git-managed application definitions, consumed by the existing hosted publisher.

Only portable JSON is shared. Compilation and card lifecycle validation run in
the ordinary nonroot worker, never in the privileged provisioner/controller.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.legions.models import LegionBlueprintPreset
from backend.packs.content import MAX_TEMPLATE_BYTES
from .templates import validate_template

SCENARIOS = Path(__file__).resolve().parents[2] / "examples/poc"


def template_from_definition(definition: LegionBlueprintPreset, *, packs=(), created_at=None):
    roots = [node for node in definition.blueprint.nodes if node.type == "legion" and node.parent_key is None]
    if len(roots) != 1:
        raise ValueError("A shared POC must contain one root Legion with a saved Workspace layout")
    namespace = uuid5(NAMESPACE_URL, "https://open-agent-world.org/scenarios/" + definition.id)
    content = json.dumps(definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    value = {"version": 1, "name": definition.name, "source_release_id": str(uuid5(namespace, content)),
             "root_key": roots[0].key, "root_keys": [roots[0].key],
             "created_at": (created_at or datetime.now(UTC)).isoformat(),
             "blueprint": definition.blueprint.model_dump(mode="json"), "packs": list(packs),
             "source_identity": {"profile_id": str(namespace), "organization_id": None,
                                 "workspace_id": None, "world_id": None},
             "source_nodes": {node.key: str(uuid5(namespace, node.key)) for node in definition.blueprint.nodes}}
    # The ordinary publication contract must explicitly opt into Terminal access.
    # Git scenarios currently use the safer default, just like Publish application.
    validate_template(value)
    return value


def definitions():
    for path in sorted(SCENARIOS.glob("*.json")):
        with path.open("rb") as source:
            data = source.read(MAX_TEMPLATE_BYTES + 1)
        if len(data) > MAX_TEMPLATE_BYTES:
            raise ValueError("Shared POC definition exceeds the portable-state limit")
        yield LegionBlueprintPreset.model_validate_json(data), datetime.fromtimestamp(path.stat().st_mtime, UTC)


def catalog():
    result = []
    for definition, modified in definitions():
        template = template_from_definition(definition, created_at=modified)
        result.append({"id": template["source_release_id"], "name": "Git: " + template["name"],
                       "created_at": template["created_at"],
                       "legion_id": template["source_nodes"][template["root_key"]]})
    return result


def resolve(release_id, services):
    from .packs import selected_locks
    from backend.legions.validation import compatibility_issues
    from backend.legions.presets import plugin_preset_record
    for definition, modified in definitions():
        value = template_from_definition(definition, created_at=modified)
        if value["source_release_id"] != release_id:
            continue
        issues = compatibility_issues(plugin_preset_record(definition, services.plugins), services.plugins)
        if issues:
            raise ValueError("Shared POC requires compatible installed Packs: " + "; ".join(issues))
        # Preserve the existing administrator-approved Pack/version policy.
        value["packs"] = selected_locks(services)
        validate_template(value)
        return value
    return None
