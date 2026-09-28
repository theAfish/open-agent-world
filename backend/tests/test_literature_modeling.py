import base64
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pymupdf
import pytest

from backend.node_documents import read_document, write_document
from backend.tests.conftest import create_node
from backend.tests.test_library_intake import attach, read, scope


def fixture(client, *, vision=True, rotation=0):
    catalog = {"revision": 0, "connections": [{"id": "visual", "name": "Visual fixture",
        "adapter": "openai", "base_url": "https://example.invalid/v1", "auth_mode": "none",
        "models": [{"id": "visual", "name": "Visual fixture", "model_id": "fixture",
                    "supports_images": vision}]}], "default_model": "oaw:model:visual"}
    saved = client.put("/api/settings/models", json=catalog)
    assert saved.status_code == 200, saved.text
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=200)
    page.draw_rect(pymupdf.Rect(0, 0, 200, 200), color=(1, 0, 0), fill=(1, 0, 0))
    page.draw_rect(pymupdf.Rect(200, 0, 400, 200), color=(0, 0, 1), fill=(0, 0, 1))
    page.insert_text((20, 30), "Synthetic figure: labelled Ta-O coordination")
    page.set_rotation(rotation)
    content = base64.b64encode(pdf.tobytes()).decode()
    pdf.close()
    paper = create_node(client, "library.paper", name="Synthetic local source", position={"x": 120, "y": 80})
    assert attach(client, paper["id"], content).status_code == 200
    crop = [0.1, 0.25, 0.3, 0.5] if rotation == 0 else [0.25, 0.1, 0.5, 0.3]
    selected = client.post(f"/api/nodes/{paper['id']}/actions/annotate", json={
        "expected_revision": read(client, paper["id"])["revision"], "arguments": {"page": 1,
        "annotation": {"id": "figure-1", "text": "", "rects": [crop],
                       # Deliberately unrelated browser image: server must ignore it.
                       "image": "data:image/png;base64,aW52YWxpZA=="}}})
    assert selected.status_code == 200, selected.text
    return paper["id"]


def model(client, paper_id, **overrides):
    current = read(client, paper_id)
    arguments = {"annotation_id": "figure-1", "document_version_id": current["value"]["current_document_version_id"],
                 "model": "oaw:model:visual", **overrides}
    revision = arguments.pop("expected_revision", current["revision"])
    return client.post(f"/api/literature/papers/{paper_id}/model_figure", json={
        "expected_revision": revision, "arguments": arguments})


def test_figure_workspace_is_source_bound_idempotent_and_does_not_run_agent(client):
    paper = fixture(client)
    source_before = read(client, paper)
    before = len(client.get("/api/nodes").json())
    response = model(client, paper)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(client.get("/api/nodes").json()) == before + 3
    assert read(client, paper) == source_before
    assert not result["replay"]
    current = read(client, result["structure_id"])
    assert current["value"]["atoms"] == []
    provenance = current["value"]["source_metadata"]
    assert provenance["paper_id"] == paper
    assert provenance["document_sha256"] == source_before["value"]["current_document_version_id"]
    assert provenance["annotation_id"] == "figure-1" and provenance["page"] == 1
    assert provenance["reconstruction_status"] == "schematic"
    assert "observe_atom_structure" in result["run_prompt"]
    services = client.app.state.services
    record = services.resources.get_record(result["image_id"])
    image_bytes = services.resources.resolve_relative_path(record.relative_path).read_bytes()
    assert sha256(image_bytes).hexdigest() == provenance["image_sha256"]
    assert image_bytes.startswith(b"\x89PNG")
    pixels = pymupdf.Pixmap(image_bytes)
    center = pixels.pixel(pixels.width // 2, pixels.height // 2)
    assert center[0] > 240 and center[1] < 10 and center[2] < 10
    assert len(services.run_manager.store.list()) == 0
    edges = client.get("/api/edges").json()
    assert {(item["source"], item["target"], item["relationship"]) for item in edges} >= {
        (result["agent_id"], result["image_id"], "view"),
        (result["agent_id"], paper, "library.read"),
        (result["agent_id"], result["structure_id"], "atomsculptor.structure.modify"),
        (paper, result["structure_id"], "atomsculptor.figure_model")}
    # An agent result and a user-moved canvas remain untouched on replay.
    changed = {**current["value"], "atoms": [{"id": 4, "symbol": "Ta", "x": 0, "y": 0, "z": 0}]}
    write_document(services, result["structure_id"], changed, current["revision"])
    node = client.get(f"/api/nodes/{result['structure_id']}").json()
    assert client.patch(f"/api/nodes/{result['structure_id']}", json={
        "expected_revision": node["revision"], "position": {"x": 7000, "y": 8000}}).status_code == 200
    again = model(client, paper)
    assert again.status_code == 200 and again.json()["replay"]
    assert again.json()["structure_id"] == result["structure_id"]
    assert len(read(client, result["structure_id"])["value"]["atoms"]) == 1
    assert client.get(f"/api/nodes/{result['structure_id']}").json()["position"] == {"x": 7000, "y": 8000}


def test_requires_explicit_vision_model_before_creating_any_cards(client):
    paper = fixture(client, vision=False)
    before = client.get("/api/nodes").json()
    response = model(client, paper)
    assert response.status_code == 422 and "Vision" in response.text
    assert client.get("/api/nodes").json() == before


def test_scope_membership_is_checked_and_kept_in_projection_markers(client):
    paper = fixture(client)
    owner = scope(client)
    before = client.get("/api/nodes").json()
    assert model(client, paper, scope_id=owner).status_code == 422
    assert client.get("/api/nodes").json() == before
    linked = client.post(f"/api/literature/scopes/{owner}/link_paper", json={
        "expected_revision": read(client, owner)["revision"], "arguments": {"paper_id": paper}})
    assert linked.status_code == 200, linked.text
    result = model(client, paper, scope_id=owner)
    assert result.status_code == 200, result.text
    for key in ("image_id", "agent_id", "structure_id"):
        config = client.get(f"/api/nodes/{result.json()[key]}").json()["config"]
        assert config["scope_id"] == owner and config["paper_id"] == paper
        assert config["research_projection"] == "paper_modeling"


def test_stale_revision_pdf_version_and_invalid_selection_do_not_mutate(client):
    paper = fixture(client)
    before = client.get("/api/nodes").json()
    assert model(client, paper, expected_revision=0).status_code == 409
    assert model(client, paper, document_version_id="f" * 64).status_code == 422
    assert model(client, paper, annotation_id="missing").status_code == 422
    assert model(client, paper, model="oaw:model:absent").status_code == 422
    assert client.get("/api/nodes").json() == before
    old = read(client, paper)["value"]["current_document_version_id"]
    pdf = pymupdf.open()
    pdf.new_page().insert_text((50, 50), "Replacement document")
    assert attach(client, paper, base64.b64encode(pdf.tobytes()).decode()).status_code == 200
    pdf.close()
    assert model(client, paper, document_version_id=old).status_code == 422


def test_no_client_image_or_forged_geometry_can_replace_the_source(client):
    paper = fixture(client)
    assert model(client, paper, image="forged").status_code == 422
    services = client.app.state.services
    current = read_document(services, paper)
    # A saved annotation whose crop disagrees with the immutable anchor must fail.
    current["value"]["annotations"][0]["rects"] = [[0, 0, 0.5, 0.5]]
    write_document(services, paper, current["value"], current["revision"])
    assert model(client, paper).status_code == 422


def test_rotated_pdf_uses_the_displayed_page_coordinate_system(client):
    paper = fixture(client, rotation=90)
    response = model(client, paper)
    assert response.status_code == 200, response.text
    result = response.json()
    record = client.app.state.services.resources.get_record(result["image_id"])
    raw = client.app.state.services.resources.resolve_relative_path(record.relative_path).read_bytes()
    pixels = pymupdf.Pixmap(raw)
    color = pixels.pixel(pixels.width // 2, pixels.height // 2)
    assert color[0] > 240 and color[1] < 10 and color[2] < 10


def test_partial_creation_compensates_resources_runtime_and_nodes(client, monkeypatch):
    paper = fixture(client)
    services = client.app.state.services
    before_nodes = client.get("/api/nodes").json()
    before_files = set(services.resources.image_root.iterdir())
    original = type(services).create_edge
    count = 0

    async def fail_edge(owner, *args, **kwargs):
        nonlocal count
        count += 1
        if count == 3:
            raise RuntimeError("injected modelling edge failure")
        return await original(owner, *args, **kwargs)

    monkeypatch.setattr(type(services), "create_edge", fail_edge)
    with pytest.raises(RuntimeError, match="injected modelling"):
        model(client, paper)
    assert client.get("/api/nodes").json() == before_nodes
    assert client.get("/api/edges").json() == []
    assert set(services.resources.image_root.iterdir()) == before_files
    runtime = services.run_manager._providers.get("atomsculptor.adk-team")
    assert runtime is not None and not runtime.records


def test_partial_user_deleted_workspace_is_not_recreated_silently(client):
    paper = fixture(client)
    response = model(client, paper)
    assert response.status_code == 200, response.text
    result = response.json()
    assert client.delete(f"/api/nodes/{result['agent_id']}").status_code == 200
    before = client.get("/api/nodes").json()
    assert model(client, paper).status_code == 409
    assert client.get("/api/nodes").json() == before


def second_figure(client, paper):
    response = client.post(f"/api/nodes/{paper}/actions/annotate", json={
        "expected_revision": read(client, paper)["revision"], "arguments": {"page": 1,
        "annotation": {"id": "figure-2", "text": "", "rects": [[.6, .2, .3, .5]]}}})
    assert response.status_code == 200, response.text


def test_different_figures_reuse_paper_workspace_without_overwriting_structure(client):
    paper = fixture(client)
    first = model(client, paper).json()
    current = read(client, first["structure_id"])
    write_document(client.app.state.services, first["structure_id"],
                   {**current["value"], "atoms": [{"id": 4, "symbol": "Ta", "x": 0, "y": 0, "z": 0}]}, current["revision"])
    preserved = read(client, first["structure_id"])
    second_figure(client, paper)
    before = len(client.get("/api/nodes").json())
    response = model(client, paper, annotation_id="figure-2")
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["workspace_reused"] and not second["replay"]
    assert second["agent_id"] == first["agent_id"] and second["structure_id"] == first["structure_id"]
    assert second["image_id"] != first["image_id"]
    assert len(client.get("/api/nodes").json()) == before + 1
    assert read(client, first["structure_id"]) == preserved
    assert second["image_id"] in second["run_prompt"] and "separate layer" in second["run_prompt"]
    repeat = model(client, paper, annotation_id="figure-2").json()
    assert repeat["replay"] and repeat["image_id"] == second["image_id"]
    assert len(client.get("/api/nodes").json()) == before + 1
    edges = client.get("/api/edges").json()
    assert {(e["source"], e["target"]) for e in edges if e["relationship"] == "view"} >= {
        (first["agent_id"], first["image_id"]), (first["agent_id"], second["image_id"])}


def test_reuse_failure_does_not_remove_shared_agent_or_saved_structure(client, monkeypatch):
    paper = fixture(client)
    first = model(client, paper).json()
    second_figure(client, paper)
    before = client.get("/api/nodes").json()
    structure = read(client, first["structure_id"])
    async def fail_edge(*args, **kwargs):
        raise RuntimeError("injected shared edge failure")
    monkeypatch.setattr(type(client.app.state.services), "create_edge", fail_edge)
    with pytest.raises(RuntimeError, match="shared edge"):
        model(client, paper, annotation_id="figure-2")
    assert client.get("/api/nodes").json() == before
    assert read(client, first["structure_id"]) == structure


def observation_workspace(client, *, vision=True):
    fixture(client, vision=vision)
    agent = create_node(client, "agent", config={"model": "oaw:default"})
    structure = create_node(client, "atomsculptor.structure")
    edge = client.post("/api/edges", json={"source": agent["id"], "target": structure["id"],
        "relationship": "atomsculptor.structure.inspect"})
    assert edge.status_code == 201, edge.text
    return agent["id"], structure["id"], edge.json()["id"]


def invoke_observation(client, agent_id, structure_id):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.tests.test_minister import call
    return call(client, WorldAgentCapabilityProvider(client.app.state.services).invoke_tool,
                agent_id, "atomsculptor.structure.observe:" + structure_id, {"view": "iso"})


def capture_response(request):
    from backend.tests.test_visual_tools import PNG
    return {**request, "data_base64": base64.b64encode(PNG).decode(), "metadata": {"camera_view": "iso"}}


def test_ordinary_agent_cannot_bypass_vision_gate_with_direct_capability(client):
    from backend.errors import PermissionDeniedError
    agent, structure, _ = observation_workspace(client, vision=False)
    with pytest.raises(PermissionDeniedError, match="Vision"):
        invoke_observation(client, agent, structure)
    assert not client.app.state.services.visual_observers.peers


def test_ordinary_agent_tool_list_exposes_observe_only_for_vision(client):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.tests.test_minister import call
    agent, _, _ = observation_workspace(client, vision=False)
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    names = {tool.name for tool in call(client, provider.list_tools, agent)}
    assert "inspect_atom_structure" in names and "observe_atom_structure" not in names
    catalog = client.get("/api/settings/models").json()
    catalog["connections"][0]["models"][0]["supports_images"] = True
    assert client.put("/api/settings/models", json=catalog).status_code == 200
    names = {tool.name for tool in call(client, provider.list_tools, agent)}
    assert "observe_atom_structure" in names


@pytest.mark.parametrize("change", ["disable_vision", "change_model", "revoke_edge", "change_document"])
def test_plugin_capture_rechecks_model_grants_and_revision(client, change):
    from backend.errors import ConflictError, PermissionDeniedError
    agent, structure, edge_id = observation_workspace(client)
    with client.websocket_connect("/ws/visual") as socket, ThreadPoolExecutor() as pool:
        pending = pool.submit(invoke_observation, client, agent, structure)
        request = socket.receive_json()
        assert request["kind"] == "plugin_capture" and request["node_id"] == structure
        if change in {"disable_vision", "change_model"}:
            catalog = client.get("/api/settings/models").json()
            target = catalog["connections"][0]["models"][0]
            if change == "disable_vision":
                target["supports_images"] = False
            else:
                target["model_id"] = "a-different-provider-model"
            assert client.put("/api/settings/models", json=catalog).status_code == 200
        elif change == "revoke_edge":
            assert client.delete("/api/edges/" + edge_id).status_code == 200
        else:
            current = read(client, structure)
            write_document(client.app.state.services, structure,
                {**current["value"], "source_name": "updated"}, current["revision"])
        socket.send_json(capture_response(request))
        with pytest.raises(PermissionDeniedError if change in {"disable_vision", "revoke_edge"} else ConflictError):
            pending.result(timeout=5)


def test_plugin_capture_returns_real_image_for_default_vision_model(client):
    from backend.agents.media import VisualToolResult
    from backend.tests.test_visual_tools import PNG
    agent, structure, _ = observation_workspace(client)
    with client.websocket_connect("/ws/visual") as socket, ThreadPoolExecutor() as pool:
        pending = pool.submit(invoke_observation, client, agent, structure)
        request = socket.receive_json()
        socket.send_json(capture_response(request))
        result = pending.result(timeout=5)
    assert isinstance(result, VisualToolResult) and result.images[0].data == PNG
    assert result.metadata["capture_metadata"]["camera_view"] == "iso"


def test_plugin_visual_gate_honors_inherited_legion_model(client):
    from backend.errors import PermissionDeniedError
    from backend.visual_observation import plugin_visual_model
    fixture(client)
    catalog = client.get("/api/settings/models").json()
    catalog["connections"][0]["models"].append({"id": "text-only", "name": "Text", "model_id": "text", "supports_images": False})
    assert client.put("/api/settings/models", json=catalog).status_code == 200
    from backend.tests.test_minister import call
    from backend.world.models import CardCreate
    legion = call(client, client.app.state.services.restore_card, CardCreate(id="vision-team", type="legion",
        config={"mode": "team", "model_override": "oaw:model:text-only"})).model_dump(mode="json")
    agent = create_node(client, "agent", parent_id=legion["id"], config={"model": "oaw:model:visual"})
    with pytest.raises(PermissionDeniedError, match="Vision"):
        plugin_visual_model(client.app.state.services, agent["id"])
