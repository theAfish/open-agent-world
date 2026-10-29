"""Release specifications. A stopped-profile copy becomes the isolated runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.dependencies import get_services
from backend.application import write_setting
from backend.errors import DomainError, ResourceValidationError
from backend.legion_workspace import WorkspaceLayout
from backend.plugins.deployment import merged_surfaces, resolve_surface

PREFIX = "deployment.release."
MANIFEST = "deployment.json"


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legion_id: str | None = Field(default=None, min_length=1, max_length=100)
    legion_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    allow_terminal: bool = False

    @model_validator(mode="after")
    def selected_workspaces(self):
        if (self.legion_id is None) == (self.legion_ids is None):
            raise ValueError("Choose one or more Legion workspaces")
        if self.legion_ids is not None and (len(set(self.legion_ids)) != len(self.legion_ids)
                or any(not node_id or len(node_id) > 100 for node_id in self.legion_ids)):
            raise ValueError("Workspace IDs must be nonempty and unique")
        return self

    @property
    def workspace_ids(self):
        return self.legion_ids if self.legion_ids is not None else [self.legion_id]


def published_workspaces(manifest):
    """Existing releases contain their single workspace at the manifest root."""
    return manifest.get("workspaces", [manifest])


def views(node):
    if not node:
        return []
    if node["kind"] == "split":
        return views(node["first"]) + views(node["second"])
    return [node["view"]] if node["kind"] == "pane" else node["views"]


def configuration_digest(db):
    """Detect edits between publication and the offline snapshot (ignore live status)."""
    cards = []
    for row in db.execute("SELECT id,type,plugin_id,name,config_json,parent_id,equipment_json,minister_json,state_scope FROM cards ORDER BY id"):
        values = list(row)
        config = json.loads(values[4])
        config.pop("status", None)
        values[4] = config
        cards.append(values)
    edges = [list(row) for row in db.execute("SELECT id,source_id,target_id,relationship,plugin_id,direction FROM edges ORDER BY id")]
    settings = [list(row) for row in db.execute("SELECT key,value_json FROM application_settings ORDER BY key")
                if not row[0].startswith((PREFIX, "ui_preferences.", "application_identity."))]
    documents = [list(row) for row in db.execute("SELECT v.scope_id,v.key,v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id WHERE s.scope_kind='node_document' ORDER BY v.scope_id,v.key")]
    return hashlib.sha256(json.dumps([cards, edges, settings, documents], sort_keys=True).encode()).hexdigest()


def panel_kind(services, card, section):
    definition = services.plugins.node_type(card.type)
    if definition.deployment is not None:
        contract = definition.deployment
        if ((section is None or contract.shared_sections) and contract.surface is not None) or section in contract.sections:
            return "plugin"
        raise ResourceValidationError(f"{card.name}: this plugin has not published this surface")
    if card.type == "conversation" and section in (None, "sessions", "conversation", "participants"):
        return "conversation"
    if card.type == "sandbox" and section in (None, "files", "preview", "terminal"):
        return "sandbox"
    if definition.frontend.get("workspace") or definition.frontend.get("body"):
        raise ResourceValidationError(f"{card.name}: declare NodeDeploymentDefinition to publish this plugin view")
    if section is None:
        if card.type in ("core.execution-graph", "core.jobs", "core.artifact-collection"):
            return {"core.execution-graph": "execution_graph", "core.jobs": "jobs",
                    "core.artifact-collection": "artifacts"}[card.type]
        if card.type in ("text", "image"):
            return card.type
        if services.plugins.has_trait(card.type, "ui.task-board.v1"):
            return "tasks"
        if services.plugins.has_trait(card.type, "core.agent"):
            return "agent"
    raise ResourceValidationError(
        f"{card.name}: this card/section does not yet have a public runtime surface. "
        "Remove it from the published layout; it can continue working in the background."
    )


def inspect_workspace(services, request: PublishRequest, legion_id: str):
    legion = services.world.get_card(legion_id)
    if legion.type != "legion":
        raise ResourceValidationError("Choose a Legion workspace")
    layout = WorkspaceLayout.model_validate(legion.config.get("workspace_layout") or {}).model_dump(mode="json", exclude_none=True)
    placed = views(layout.get("root"))
    if not placed:
        raise ResourceValidationError("Save a nonempty workspace layout before publishing")
    panels = []
    issues = []
    for view in placed:
        try:
            card = services.world.get_card(view["card_id"])
            if card.parent_id != legion.id:
                raise ResourceValidationError("Published panes must belong to this Legion")
            section = view.get("section_id")
            kind = panel_kind(services, card, section)
            if kind == "sandbox" and section == "terminal" and not request.allow_terminal:
                raise ResourceValidationError("The layout includes Terminal. Enable terminal access or remove that section before publishing.")
            panels.append({**view, "name": card.name, "kind": kind})
        except DomainError as error:
            issues.append({**view, "message": str(error)})
    return legion, layout, panels, issues


def workspace_spec(services, request: PublishRequest, legion_id: str):
    legion, layout, panels, issues = inspect_workspace(services, request, legion_id)
    if issues:
        raise ResourceValidationError("; ".join(issue["message"] for issue in issues))
    # Explicit section contracts grant only visible access. Whole-card shared
    # sections keep their presentation choices without changing business access.
    hidden = {tuple((v["card_id"], v.get("section_id"))) for v in layout.get("hidden_sections", [])}
    extracted = {(p["card_id"], p["section_id"]) for p in panels if p.get("section_id")}
    permissions = {}
    plugin_access = {}
    plugin_surfaces = {}
    for panel in panels:
        node_id, kind, section = panel["card_id"], panel["kind"], panel.get("section_id")
        grants = permissions.setdefault(node_id, [])
        panel["sections"] = []
        if kind == "plugin":
            definition = services.plugins.node_type(services.world.get_card(node_id).type)
            contract = definition.deployment
            selected = plugin_surfaces.setdefault(node_id, [])
            if section is None or contract.shared_sections:
                selected.append(resolve_surface(contract.surface, definition))
            if contract.shared_sections and section is not None and section not in grants:
                grants.append(section)
                panel["sections"].append(section)
            for name, surface in contract.sections.items():
                if (section is not None and name != section) or (node_id, name) in hidden:
                    continue
                if section is None and (node_id, name) in extracted:
                    continue
                selected.append(resolve_surface(surface, definition))
                if name not in grants:
                    grants.append(name)
                panel["sections"].append(name)
            plugin_access[node_id] = merged_surfaces(selected)
            plugin_access[node_id]["shared_sections"] = contract.shared_sections
            continue
        operations = {"conversation": ["sessions", "conversation", "participants"], "sandbox": ["files", "preview", "terminal"],
                      "tasks": ["tasks"], "text": ["text"], "image": ["image"], "agent": ["status"],
                      "execution_graph": ["execution_graph"], "jobs": ["jobs"], "artifacts": ["artifacts"]}[kind]
        for operation in operations:
            if kind in {"sandbox", "conversation"} and section and operation != section:
                continue
            if section is None and (node_id, operation) in extracted:
                continue
            if (node_id, operation) in hidden or operation == "terminal" and not request.allow_terminal:
                continue
            if operation not in grants:
                grants.append(operation)
            panel["sections"].append(operation)
    layout["hidden_sections"] = [
        view for view in layout.get("hidden_sections", [])
        if plugin_access.get(view["card_id"], {}).get("shared_sections")
    ]
    return {"legion_id": legion.id, "name": legion.name,
            "layout": layout, "panels": panels, "permissions": permissions, "plugin_access": plugin_access}


def release_spec(services, request: PublishRequest):
    workspaces = [workspace_spec(services, request, node_id) for node_id in request.workspace_ids]
    return {**workspaces[0], "schema_version": 1, "id": str(uuid4()), "name": request.name.strip() or workspaces[0]["name"],
            "created_at": datetime.now(UTC).isoformat(), "workspaces": workspaces,
            "panels": [panel for workspace in workspaces for panel in workspace["panels"]],
            "permissions": {key: value for workspace in workspaces for key, value in workspace["permissions"].items()},
            "plugin_access": {key: value for workspace in workspaces for key, value in workspace["plugin_access"].items()},
            "runtime_settings": {"agent_runtime": services.settings.agent_runtime,
                                 "sandbox_runtime": services.settings.sandbox_runtime,
                                 "plugin_directories": [str(path) for path in services.settings.plugin_directories]},
            "plugin_versions": {p.id: p.version for p in services.plugins.catalog().plugins}}


router = APIRouter(tags=["deployments"])


@router.get("/api/deployment")
async def builder_mode():
    return {"mode": "builder"}


@router.get("/api/deployments")
async def list_releases(services=Depends(get_services)):
    with services.database.locked() as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT value_json FROM application_settings WHERE key LIKE ? ORDER BY key", (PREFIX + "%",))]


@router.post("/api/deployments", status_code=201)
async def publish(request: PublishRequest, services=Depends(get_services)):
    async with services._node_mutation(read_only=True):
        spec = release_spec(services, request)
        with services.database.transaction(immediate=True) as db:
            spec["configuration_digest"] = configuration_digest(db)
            # This is a host-local deployment recipe, never part of the public bootstrap.
            spec["source_path"] = str(services.settings.data_root)
            write_setting(db, PREFIX + spec["id"], spec)
    return spec


@router.post("/api/deployments/preview")
async def preview(request: PublishRequest, services=Depends(get_services)):
    """Read-only readiness report; publication revalidates under the same lock."""
    async with services._node_mutation(read_only=True):
        workspaces = []
        for node_id in request.workspace_ids:
            try:
                legion, _, panels, issues = inspect_workspace(services, request, node_id)
                workspaces.append({"legion_id": legion.id, "name": legion.name, "panels": panels, "issues": issues})
            except (DomainError, ValueError) as error:
                workspaces.append({"legion_id": node_id, "panels": [], "issues": [{"message": str(error)}]})
        issues = [{**issue, "legion_id": workspace["legion_id"]} for workspace in workspaces for issue in workspace["issues"]]
        return {"ready": not issues, "workspaces": workspaces,
                "panels": [panel for workspace in workspaces for panel in workspace["panels"]], "issues": issues}
