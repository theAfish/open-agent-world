"""Content Packs use the ordinary installer, with host-owned template registration."""
from __future__ import annotations

import hashlib
import io
import json
import re
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field

from backend import __version__
from backend.errors import PluginCompatibilityError
from backend.legions.models import LegionBlueprintPreset, LegionRecord
from backend.legions.validation import compatibility_issues
from backend.packs.manifest import Content, CreatorMetadata, Dependencies, PackDependency, PackManifest
from backend.plugins.registry import PLUGIN_API_VERSION, PackDefinition, PluginDefinition, PluginDescriptor

MAX_TEMPLATE_BYTES = 64 * 1024 * 1024


def read_presets(manifest: PackManifest, files: dict[str, bytes]) -> list[LegionBlueprintPreset]:
    from backend.packs.archive import json_object
    assert manifest.content is not None
    presets = []
    for path in manifest.content.legions:
        if path not in files:
            raise ValueError(f"Missing Pack artifact: {path}")
        if len(files[path]) > MAX_TEMPLATE_BYTES:
            raise ValueError("Content template exceeds the 64 MiB limit")
        preset = LegionBlueprintPreset.model_validate(json_object(files[path]))
        if not preset.id.startswith(manifest.id + "."):
            raise ValueError("Content Legion IDs must use their Pack namespace")
        if preset.blueprint.format_version != 1 or not 2 <= len(preset.blueprint.nodes) <= 101:
            raise ValueError("Content Legions require format 1 and 2–101 nodes")
        if len(preset.blueprint.edges) > 10000:
            raise ValueError("Content Legion has too many connections")
        presets.append(preset)
    if len({p.id for p in presets}) != len(presets):
        raise ValueError("Content Legion IDs must be unique")
    return presets


def content_plugin(manifest: PackManifest, files: dict[str, bytes], registry):
    """Validate against loaded dependencies; never load executable content."""
    from backend.legions.presets import plugin_preset_record
    presets = read_presets(manifest, files)
    owners = set()
    descriptors = {p.id: p for p in registry.plugins()}
    for dependency in manifest.dependencies.packs:
        try:
            owner = registry.owner_id("pack", dependency.id)
        except ValueError as exc:
            raise ValueError(f"Install and restart OAW with required Pack {dependency.id}{dependency.version} first") from exc
        if Version(descriptors[owner].version) not in SpecifierSet(dependency.version):
            raise ValueError(f"Restart OAW with required Pack {dependency.id}{dependency.version} first")
        owners.add(owner)
    for preset in presets:
        registry.validate_identifier(preset.id, "Legion preset")
        try:
            existing_owner = registry.owner_id("legion_preset", preset.id)
        except ValueError:
            existing_owner = None
        if existing_owner is not None and existing_owner != manifest.id:
            raise ValueError(f"Content Legion ID is already owned by {existing_owner}")
        record = plugin_preset_record(preset, registry)
        issues = compatibility_issues(record, registry)
        referenced = {node.plugin_id for node in record.blueprint.nodes}
        referenced.update(dep.plugin_id for node in record.blueprint.nodes for dep in node.dependencies)
        referenced.update(edge.plugin_id for edge in record.blueprint.edges)
        if referenced - owners:
            issues.append("Undeclared content dependencies: " + ", ".join(sorted(referenced - owners)))
        if issues:
            raise ValueError(f"Content Legion {preset.name}: " + "; ".join(issues))
    creator = manifest.creator
    assert creator is not None

    def register(registration):
        registration.register_pack(PackDefinition(id=manifest.id, name=manifest.name,
            description=creator.description, cards=(), accent_color=creator.accent_color))
        for preset in presets:
            registration.register_legion_preset(preset)

    return PluginDefinition(PluginDescriptor(id=manifest.id, name=manifest.name,
        description=creator.description, version=manifest.version,
        plugin_api_version=manifest.compatibility.plugin_api, requires_plugins=tuple(sorted(owners))), register)


class CreatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    legion_id: str = Field(min_length=1, max_length=128)
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    version: str = "0.1.0"
    creator: CreatorMetadata = Field(default_factory=CreatorMetadata)
    include_state_nodes: list[str] = Field(default_factory=list, max_length=101)


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _scan(value, path, issues):
    """Best-effort review of structured fields, not a secret-detection guarantee."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if child and normalized in {"apikey", "apikeys", "password", "passwd", "token", "accesstoken", "refreshtoken",
                                       "privatekey", "clientsecret", "secret", "secrets", "credentials", "authorization", "secretenv", "secretrefs"}:
                issues.append({"severity": "error", "path": f"{path}.{key}",
                    "message": "Remove this credential or private binding before sharing."})
            else:
                _scan(child, f"{path}.{key}", issues)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _scan(child, f"{path}[{i}]", issues)
    elif isinstance(value, str):
        if re.search(r"(?:^|\s)(?:/[A-Za-z][^\s]*/|[A-Za-z]:[\\/]|~/)", value):
            issues.append({"severity": "warning", "path": path,
                "message": "Review this machine-local path; the recipient may need to replace it."})
        if re.search(r"https?://", value):
            issues.append({"severity": "warning", "path": path,
                "message": "Review this service address or link before sharing."})
            # A standalone credential-bearing URL is never a portable binding.
            try:
                parsed = urlsplit(value)
                if parsed.password or re.search(r"[?&](?:token|api_key|key|secret)=", "?" + parsed.query, re.I):
                    issues.append({"severity": "error", "path": path,
                        "message": "Remove credentials from this address before sharing."})
            except ValueError:
                pass


def prepare_export(record: LegionRecord, request: CreatorRequest, registry) -> tuple[dict, dict[str, bytes]]:
    """Project a saved Legion, requiring explicit opt-in for initial sidecar data."""
    blueprint = record.blueprint.model_copy(deep=True)
    selected = set(request.include_state_nodes)
    if selected - {node.key for node in blueprint.nodes}:
        raise ValueError("Unknown initial-content selection; inspect the saved Legion again")
    issues = []
    content_nodes = []
    for node in blueprint.nodes:
        definition = registry.node_type(node.type)
        handler = definition.template_handler
        content_nodes.append({"key": node.key, "name": node.name, "type": node.type,
            "has_state": bool(node.initial_document is not None or node.initial_shared_state or node.payload),
            "included": node.key in selected})
        # The saved blueprint already passed the card's capture projection.
        # Do not recapture a synthetic Card: plugin projections may need live
        # ownership/resource fields that are intentionally absent here.
        defaults = definition.config_model().model_dump(mode="json")
        if "core.agent" in definition.traits and "model" in defaults:
            if node.config.get("model") != defaults["model"]:
                issues.append({"severity": "info", "path": node.name, "message": "Model selection reset to the recipient's default."})
            node.config["model"] = defaults["model"]
        if node.type == "legion":
            node.config["model_override"] = ""
            node.config["paused"] = False
        if node.type == "sandbox":
            node.config["runtime"] = defaults["runtime"]
        node.config = registry.validate_config(node.type, node.config)
        if node.key not in selected:
            node.initial_document = None
            node.initial_shared_state = None
            if handler and node.payload:
                empty = {"content": ""} if node.type == "text" else {"resource": None} if node.type == "image" else {}
                try:
                    handler.validate_payload(empty, node.payload_version)
                    node.payload = empty
                except (PluginCompatibilityError, ValueError):
                    issues.append({"severity": "error", "path": node.name,
                        "message": "This card cannot omit its initial content. Select it explicitly or remove the card from the saved Legion."})
        _scan(node.config, f"{node.name}.config", issues)
        if node.key in selected:
            _scan(node.initial_document, f"{node.name}.document", issues)
            _scan(node.initial_shared_state, f"{node.name}.shared_state", issues)
            _scan(node.payload, f"{node.name}.content", issues)
    portable = record.model_copy(update={"blueprint": blueprint})
    issues.extend({"severity": "error", "path": record.name, "message": message}
                  for message in compatibility_issues(portable, registry))
    owners = {n.plugin_id for n in blueprint.nodes}
    owners.update(d.plugin_id for n in blueprint.nodes for d in n.dependencies)
    owners.update(e.plugin_id for e in blueprint.edges)
    catalog = registry.catalog()
    descriptors = {p.id: p for p in catalog.plugins}
    dependencies = []
    for owner in sorted(owners):
        packs = sorted((p for p in catalog.packs if p.plugin_id == owner), key=lambda p: p.id)
        if not packs:
            issues.append({"severity": "error", "path": owner, "message": "This dependency has no distributable Pack."})
            continue
        # A Pack version loads its owning plugin's full contribution set.
        dependencies.append(PackDependency(id=packs[0].id, version="==" + descriptors[owner].version))
    manifest = PackManifest(schema_version=2, kind="content", id=request.id, name=request.name,
        version=request.version, compatibility={"oaw": f">={__version__},<1", "plugin_api": PLUGIN_API_VERSION, "frontend_api": 1},
        dependencies=Dependencies(packs=tuple(dependencies)), content=Content(legions=("content/legion.json",)), creator=request.creator)
    preset = LegionBlueprintPreset(id=manifest.id + ".legion", name=record.name,
        description=record.description, revision=record.revision, blueprint=blueprint)
    files = {"manifest.json": _json(manifest.model_dump(mode="json", exclude_none=True)),
             "content/legion.json": _json(preset.model_dump(mode="json"))}
    guide = request.creator
    files["README.md"] = (f"# {manifest.name}\n\n{guide.description}\n\n"
        f"Author (self-declared): {guide.author}\n\n## Preparation\n\n{guide.preparation}\n\n"
        f"## Example task\n\n{guide.example}\n\n## Expected result\n\n{guide.expected_result}\n\n"
        "## Required Packs\n\n" + "\n".join(f"- {d.id} {d.version}" for d in dependencies) +
        "\n\nInstall required Packs first, restart OAW, then install this file through the Library. "
        "After restarting, open the Pack and add its Legion to a deck. Configure your model and local tools before running.\n").encode("utf-8")
    return {"manifest": manifest.model_dump(mode="json"), "nodes": content_nodes,
            "issues": issues, "can_export": not any(i["severity"] == "error" for i in issues)}, files


def export_archive(files: dict[str, bytes]) -> bytes:
    from backend.packs.archive import inspect_archive
    files = {**files, "checksums.json": _json({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})}
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    data = buffer.getvalue()
    inspect_archive(data)
    return data
