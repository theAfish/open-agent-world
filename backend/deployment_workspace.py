"""Scoped access to the existing Workspace APIs; no editor or graph API is mounted."""
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException, Request
from backend.api import artifacts, conversations, file_preview, job_board, node_documents, node_resources, resources, runtime
from backend.api.dependencies import get_services
from backend.api.knowledge_bridge import (
    ExperimentAssembleRequest, ProjectionRequest, run_experiment_assemble, run_projection,
)
from backend.node_documents import DocumentActionRequest, read_document, invoke_document_action
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.plugins.deployment import project_document


def frontend_catalog(manifest, services, catalog=None):
    """Only published owners and their declared service providers reach the UI.

    Dependencies may supply frontend services without publishing any cards. Their
    identity and module location are needed by the shared loader, but their card
    definitions, configuration and management metadata remain private.
    """
    if catalog is None:
        catalog = services.plugins.catalog().model_dump(mode="json", by_alias=True)
    types = {services.world.get_card(node_id).type for node_id in manifest["permissions"]}
    descriptors = {plugin["id"]: plugin for plugin in catalog["plugins"]}
    enabled = set(catalog.get("enabled_plugin_ids", []))
    pending = [node["plugin_id"] for node in catalog["node_types"] if node["id"] in types]
    owners = set()
    while pending:
        owner = pending.pop()
        if owner in owners or owner not in enabled or owner not in descriptors:
            continue
        owners.add(owner)
        pending.extend(descriptors[owner].get("requires_plugins", []))
    identity_fields = {"id", "version", "plugin_api_version", "name", "description"}
    module_fields = {"version", "api_version", "url", "distribution_id"}
    return {
        "plugins": [{key: value for key, value in plugin.items() if key in identity_fields}
                    for plugin in catalog["plugins"] if plugin["id"] in owners],
        "enabled_plugin_ids": sorted(owners),
        "frontend_modules": {
            owner: {key: value for key, value in module.items() if key in module_fields}
            for owner, module in catalog.get("frontend_modules", {}).items() if owner in owners
        },
    }


def workspace_snapshot(manifest, services):
    cards = []
    types = set()
    hidden = []
    plugin_types = set()
    for node_id, grants in manifest["permissions"].items():
        card = services.world.get_card(node_id)
        types.add(card.type)
        # Only resource presentation metadata crosses the boundary, never config.
        config = {key: card.config[key] for key in
                  ("filename", "revision", "mime_type", "bytes", "image_width", "image_height")
                  if key in card.config and card.type in {"text", "image"}}
        access = manifest.get("plugin_access", {}).get(node_id)
        if access is not None:
            plugin_types.add(card.type)
            config = {key: value for key, value in card.config.items() if key in access["config_fields"]}
        cards.append({"id": card.id, "type": card.type, "name": card.name, "status": card.status,
                      "parent_id": manifest["legion_id"], "config": config, "state_scope": card.state_scope,
                      "finish": card.finish})
        sections = {"conversation": ("sessions", "conversation", "participants"),
                    "sandbox": ("files", "preview", "terminal")}.get(card.type, ())
        if access is not None:
            sections = services.plugins.node_type(card.type).deployment.sections
        hidden.extend({"card_id": node_id, "section_id": section} for section in sections if section not in grants)
    catalog = services.plugins.catalog().model_dump(mode="json", by_alias=True)
    presentation_keys = {"id", "plugin_id", "label", "icon", "color", "traits", "surfaces", "presentation", "has_execution", "state"}
    definitions = [{key: value for key, value in node.items() if key in presentation_keys}
                   for node in catalog["node_types"] if node["id"] in types]
    for definition in definitions:
        if definition["id"] in plugin_types:
            node = services.plugins.node_type(definition["id"])
            definition["frontend"] = {key: value for key, value in node.frontend.items() if key in {"body", "workspace"}}
    layout = deepcopy(manifest["layout"])
    layout["hidden_sections"] = hidden + layout.get("hidden_sections", [])
    # File following needs its actual connection, not an inferred relationship.
    # No hidden node or unrelated engineering edge enters the browser world.
    edges = []
    for node_id, access in manifest.get("plugin_access", {}).items():
        if not access.get("file_preview"):
            continue
        for edge in services.world.list_edges_from(node_id):
            if (not edge.missing_plugin and edge.relationship == "core.file-preview"
                    and edge.direction in {"forward", "bidirectional"}
                    and {"files", "preview", "conversation"}.intersection(manifest["permissions"].get(edge.target, []))):
                edges.append({key: getattr(edge, key) for key in ("id", "source", "target", "relationship", "direction")})
    return {"cards": cards, "edges": edges, "plugin_access": manifest.get("plugin_access", {}), "legion": {"id": manifest["legion_id"], "name": manifest["name"],
            "type": "legion", "finish": services.world.get_card(manifest["legion_id"]).finish,
            "config": {"workspace_layout": layout}},
            "catalog": {"node_types": definitions, "relationships": [], "packs": [],
                        **frontend_catalog(manifest, services, catalog)}}


def workspace_router(manifest):
    async def state_access(request: Request, services=Depends(get_services)):
        session_id = request.headers.get("X-OAW-State-Session") or request.query_params.get("state_session")
        node_id = request.path_params.get("node_id") or request.path_params.get("viewer_id")
        if not session_id or session_id == "default" or not node_id:
            return
        card = services.world.get_card(node_id)
        if card.state_scope != "session":
            return
        with services.database.locked() as db:
            session = db.execute("SELECT conversation_id FROM conversation_sessions WHERE id=?", (session_id,)).fetchone()
        if session is None or not {"conversation", "sessions"}.intersection(manifest["permissions"].get(session["conversation_id"], [])):
            raise HTTPException(404, "This conversation session is not published")

    router = APIRouter(prefix="/workspace", dependencies=[Depends(state_access)])

    def require(node_id, *operations):
        if node_id in manifest.get("plugin_access", {}) or not set(operations).intersection(manifest["permissions"].get(node_id, [])):
            raise HTTPException(404, "This operation is not published")

    def mount(source, names, parameter, operation):
        """Reuse the exact editor handler, with a host-owned publication gate."""
        def scope(request: Request):
            require(request.path_params[parameter], operation)
        for route in source.routes:
            if route.endpoint.__name__ in names:
                router.add_api_route(route.path, route.endpoint, methods=list(route.methods),
                                     response_model=route.response_model, status_code=route.status_code,
                                     dependencies=[Depends(scope)])

    # These handlers already enforce connected source / job ownership, selected
    # session, optimistic revisions, pagination and artifact version membership.
    mount(node_resources.router, {"execution_sources", "execution_snapshot"}, "node_id", "execution_graph")
    mount(job_board.router, {"jobs", "control"}, "node_id", "jobs")
    mount(artifacts.router, {"listing", "preview", "content"}, "collection_id", "artifacts")

    @router.post("/nodes/{viewer_id}/file-preview")
    async def preview_file(viewer_id: str, reference: file_preview.FileReference, services=Depends(get_services)):
        if not manifest.get("plugin_access", {}).get(viewer_id, {}).get("file_preview"):
            raise HTTPException(404, "This operation is not published")
        if reference.kind == "sandbox":
            require(reference.source_id, "files", "preview")
            if reference.root != "workspace":
                raise HTTPException(404, "This root is not published")
        else:
            require(reference.source_id, "conversation")
        # The ordinary handler also requires the viewer's direct file.preview
        # grant and validates the path / attachment's conversation membership.
        return await file_preview.file_preview(viewer_id, reference, services)

    def conversation_scope(request: Request):
        name = request.scope["endpoint"].__name__
        if name in {"create_conversation_session", "delete_conversation_session", "rename_conversation_session", "rename_conversation_group", "delete_conversation_group"}:
            require(request.path_params["conversation_id"], "conversation", "sessions")
        elif name in {"add_conversation_session_participants", "remove_conversation_session_participant"}:
            require(request.path_params["conversation_id"], "conversation", "participants")
        else:
            require(request.path_params["conversation_id"], "conversation")

    # Reuse business handlers and their validation, pagination and response schemas.
    # This explicit list excludes agent history and all engineering endpoints.
    handlers = {
        "upload_attachment", "attachment_content", "add_conversation_session_participants",
        "remove_conversation_session_participant", "delete_conversation_session",
        "rename_conversation_group", "delete_conversation_group", "rename_conversation_session",
        "conversation_timeline", "list_conversation_messages",
        "create_conversation_session", "post_conversation_message",
    }
    for route in conversations.router.routes:
        if route.endpoint.__name__ in handlers:
            router.add_api_route(route.path, route.endpoint, methods=list(route.methods),
                                 response_model=route.response_model, status_code=route.status_code,
                                 dependencies=[Depends(conversation_scope)])

    def run_scope(request: Request, services=Depends(get_services)):
        # Conversation UI reads public activity, tool bodies and reasoning.
        # Raw model requests contain private instructions and stay unpublished.
        if request.path_params.get("kind", "tools") != "tools":
            raise HTTPException(404, "This operation is not published")
        run_id = request.path_params["run_id"]
        visited = set()
        while run_id and run_id not in visited:
            visited.add(run_id)
            run = services._require_run_manager().get_run(run_id)
            with services.database.locked() as db:
                visible = db.execute("""
                    SELECT DISTINCT s.conversation_id FROM conversation_sessions s
                    WHERE (s.id=? AND s.conversation_id=? AND ?='conversation')
                       OR EXISTS (SELECT 1 FROM conversation_messages m
                                  WHERE m.session_id=s.id AND m.conversation_id=s.conversation_id
                                  AND m.run_id=?)
                """, (run.context_id, run.caller_id, run.caller_kind, run_id)).fetchall()
            if any("conversation" in manifest["permissions"].get(row["conversation_id"], []) for row in visible):
                return
            # Only recorded parentage carries a published conversation's
            # visibility to its delegated work; shared Agent/context IDs do not.
            run_id = run.parent_run_id
        raise HTTPException(404, "This Run is not part of a published conversation")

    for route in runtime.router.routes:
        if route.endpoint.__name__ in {
            "run_activity", "run_activity_detail", "run_reasoning", "run_reasoning_text",
            "run_invocations", "run_invocation",
        }:
            router.add_api_route(route.path, route.endpoint, methods=list(route.methods),
                                 dependencies=[Depends(run_scope)])

    @router.get("/conversations/{conversation_id}")
    def summary(conversation_id: str, services=Depends(get_services)):
        require(conversation_id, "conversation", "sessions", "participants")
        value = services.conversation_summary(conversation_id).model_dump(mode="json")
        # Participants are business recipients; model identifiers and context internals are private.
        value["agents"] = [{**agent, "model": ""} for agent in value["agents"] if agent["connected"]]
        value["context_statuses"] = {}
        if not set(manifest["permissions"][conversation_id]) & {"conversation", "sessions"}:
            value["sessions"] = []
        return value

    @router.get("/resources/{resource_id}/text")
    async def text(resource_id: str, services=Depends(get_services)):
        require(resource_id, "text")
        document = services.resources.read_text(resource_id)
        return {"content": document.content, "revision": document.revision, "history": []}

    @router.get("/resources/{resource_id}/history")
    def history(resource_id: str):
        require(resource_id, "text", "image")
        return []

    @router.get("/resources/{resource_id}/content")
    async def image(resource_id: str, services=Depends(get_services)):
        require(resource_id, "image")
        return await resources.resource_content(resource_id, services)

    @router.get("/nodes/{node_id}/document")
    async def document(node_id: str, services=Depends(get_services), summary_only: bool = False):
        access = manifest.get("plugin_access", {}).get(node_id)
        if access is not None:
            if not access["document_fields"] and not access["summary_fields"] and access.get("document", "none") == "none":
                raise HTTPException(404, "This operation is not published")
            projected = project_document(await node_documents.get_document(node_id, services), access)
            return {"revision": projected["revision"], "summary": projected["summary"]} if summary_only else projected
        require(node_id, "tasks")
        return await node_documents.get_document(node_id, services, summary_only)

    @router.post("/nodes/{node_id}/actions/{action}")
    async def action(node_id: str, action: str, request: DocumentActionRequest, services=Depends(get_services)):
        access = manifest.get("plugin_access", {}).get(node_id)
        if access is not None:
            if action not in access["document_actions"]:
                raise HTTPException(404, "This operation is not published")
            return project_document(await invoke_document_action(services, node_id, action, request), access)
        require(node_id, "tasks")
        if action not in {"upsert", "progress", "remove"}:
            raise HTTPException(403, "Execution configuration is locked")
        if action == "upsert":
            existing = {task["id"]: task for task in read_document(services, node_id)["value"]["tasks"]}
            tasks = request.arguments.get("tasks", [])
            if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
                raise HTTPException(422, "Tasks must be a list of objects")
            for task in tasks:
                previous = existing.get(task.get("id"), {})
                for key in ("executor_id", "execution_prompt"):
                    if task.get(key) != previous.get(key):
                        raise HTTPException(403, "Execution configuration is locked")
        return await invoke_document_action(services, node_id, action, request)

    def tasks_scope(request: Request):
        access = manifest.get("plugin_access", {}).get(request.path_params["node_id"])
        if access is not None and access["execution"]:
            return
        require(request.path_params["node_id"], "tasks")

    @router.get("/nodes/{node_id}/document/downloads/{name}")
    async def download(node_id: str, name: str, services=Depends(get_services)):
        access = manifest.get("plugin_access", {}).get(node_id, {})
        if name not in access.get("downloads", []):
            raise HTTPException(404, "This operation is not published")
        return await node_documents.download_document(node_id, name, services)

    @router.post("/nodes/{node_id}/resource/{action}")
    async def resource_action(node_id: str, action: str, request: ResourceActionRequest, services=Depends(get_services)):
        actions = manifest.get("plugin_access", {}).get(node_id, {}).get("resource_actions", {})
        if action not in actions:
            raise HTTPException(404, "This operation is not published")
        result = await invoke_resource_action(services, node_id, action, request)
        return {key: value for key, value in result.items() if key in actions[action]}

    @router.post("/knowledge/{node_id}/project")
    async def knowledge_project(node_id: str, request: ProjectionRequest, services=Depends(get_services)):
        # The bridge itself never checks capability; a release only reaches it once
        # both halves of the pipeline it drives are published for this node.
        actions = manifest.get("plugin_access", {}).get(node_id, {}).get("resource_actions", {})
        if not {"projection_prompt", "save_projection"} <= set(actions):
            raise HTTPException(404, "This operation is not published")
        return await run_projection(node_id, request, services)

    @router.post("/knowledge/{node_id}/assemble")
    async def knowledge_assemble(node_id: str, request: ExperimentAssembleRequest, services=Depends(get_services)):
        actions = manifest.get("plugin_access", {}).get(node_id, {}).get("resource_actions", {})
        if not {"experiment_assemble_prompt", "experiment_save"} <= set(actions):
            raise HTTPException(404, "This operation is not published")
        return await run_experiment_assemble(node_id, request, services)

    for route in node_documents.router.routes:
        if route.endpoint.__name__ in {"execution", "start_execution", "stop_execution"}:
            router.add_api_route(route.path, route.endpoint, methods=list(route.methods), dependencies=[Depends(tasks_scope)])

    @router.get("/sandboxes/{sandbox_id}")
    async def sandbox(sandbox_id: str, services=Depends(get_services)):
        require(sandbox_id, "files", "preview", "terminal")
        info = await services.get_sandbox(sandbox_id)
        return {"id": sandbox_id, "state": info.state, "available": info.available, "workspace_access": info.workspace_access}

    @router.post("/sandboxes/{sandbox_id}/start")
    async def start(sandbox_id: str, services=Depends(get_services)):
        require(sandbox_id, "files", "preview", "terminal")
        await services.start_sandbox(sandbox_id)
        return await sandbox(sandbox_id, services)

    @router.post("/sandboxes/{sandbox_id}/stop")
    async def stop(sandbox_id: str, services=Depends(get_services)):
        require(sandbox_id, "files", "preview", "terminal")
        await services.stop_sandbox(sandbox_id)
        return await sandbox(sandbox_id, services)

    @router.get("/sandboxes/{sandbox_id}/files")
    async def files(sandbox_id: str, operation: str = "roots", root: str = "workspace", path: str = "", services=Depends(get_services), cursor: str = '', query: str = ''):
        require(sandbox_id, "files", "preview")
        if root != "workspace":
            raise HTTPException(404, "This root is not published")
        result = await runtime.sandbox_files(sandbox_id, operation, root, path, services, cursor=cursor, query=query)
        if operation == "roots" and isinstance(result, list):
            return [{"id": "workspace", "label": "Workspace", "directory": True, "access": item.get("access", "read_only")}
                    for item in result if item["id"] == "workspace"]
        return result

    @router.post("/sandboxes/{sandbox_id}/execute")
    async def execute(sandbox_id: str, request: runtime.SandboxExecuteRequest, services=Depends(get_services)):
        require(sandbox_id, "terminal")
        if request.environment_id is not None or request.target_id is not None:
            raise HTTPException(403, "Execution configuration is locked")
        await services.start_sandbox(sandbox_id)
        return await runtime.execute_sandbox(sandbox_id, request, services)

    def terminal_scope(request: Request):
        require(request.path_params["sandbox_id"], "terminal")

    for route in runtime.router.routes:
        if route.endpoint.__name__ in {"sandbox_history", "sandbox_cancel"}:
            router.add_api_route(route.path, route.endpoint, methods=list(route.methods), dependencies=[Depends(terminal_scope)])
    return router
