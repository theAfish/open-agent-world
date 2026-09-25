"""OAW wiring for the knowledge base card: registration, errors, lifecycle, formation.

The pipeline itself is covered by ``plugins/knowledge_base/tests``, which runs with
no host at all. What is left for the backend to prove is the seam: that the loader
registers the card, that the three human-only operations stay unreachable by agents,
that a ``KnowledgeError`` out of a handler becomes a ``ResourceValidationError``
without any adapter in between, that create/delete own the storage directory, and
that the ``Knowledge research`` formation deploys — and copies — a working card.
"""
import base64
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PLUGIN_SRC = Path(__file__).resolve().parents[2] / "plugins" / "knowledge_base" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

pytest.importorskip("mkb", reason="Install mat-know-base into the backend environment")
pytest.importorskip("sqlalchemy")

from backend.capabilities.provider import WorldAgentCapabilityProvider  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.errors import ResourceValidationError  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.node_resources import ResourceActionRequest, invoke_resource_action  # noqa: E402
from backend.plugins.loader import load_plugin_registry  # noqa: E402
from backend.services import create_services  # noqa: E402
from backend.tests.conftest import create_node  # noqa: E402
from backend.world.models import CardCreate  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402
from oaw_knowledge_base.operations import EXTRACT_ACTIONS, OPERATIONS, READ_ACTIONS  # noqa: E402

NODE_TYPE = "knowledge.base"
# Uploading a file, changing settings and approving a draft are acts a person takes
# on the canvas. They must never gain a capability kind, on any transport.
DESKTOP_ONLY = {"ingest", "settings", "review"}


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


async def action(services, node_id, operation, **arguments):
    return await invoke_resource_action(services, node_id, operation,
                                        ResourceActionRequest(arguments=arguments))


def test_the_loader_registers_the_card_and_its_grants():
    registry = load_plugin_registry()
    definition = registry.node_type(NODE_TYPE)
    assert definition.frontend["workspace"] == "workspace"
    assert definition.deletion_warning  # deleting a card destroys knowledge; warn first
    assert set(definition.resource_actions) == {operation.name for operation in OPERATIONS}

    for name, resource_action in definition.resource_actions.items():
        if name in DESKTOP_ONLY:
            assert resource_action.capability_kind is None, f"{name} is reachable by agents"
            continue
        capability = registry.capability_definition(resource_action.capability_kind)
        assert capability.tool_name and capability.input_schema["type"] == "object"

    read = {grant.kind for grant in registry.relationship(f"{NODE_TYPE}.read").capabilities}
    extract = {grant.kind for grant in registry.relationship(f"{NODE_TYPE}.extract").capabilities}
    assert read == {f"{NODE_TYPE}.{name}" for name in READ_ACTIONS}
    assert extract == {f"{NODE_TYPE}.{name}" for name in EXTRACT_ACTIONS}
    assert read < extract  # extract is read plus the write half, never less
    assert not {kind for kind in extract if kind.rsplit(".", 1)[-1] in DESKTOP_ONLY}


@pytest.mark.asyncio
async def test_a_knowledge_error_surfaces_as_a_validation_error(services):
    node = await services.create_card(CardCreate(type=NODE_TYPE))
    # The conversion needs no adapter: the plugin raises its own KnowledgeError, the
    # host turns any ValueError out of a handler into a ResourceValidationError.
    assert issubclass(KnowledgeError, ValueError)
    with pytest.raises(ResourceValidationError) as failure:
        await action(services, node.id, "markdown", source_id="not-a-uuid")
    assert "UUID" in str(failure.value)


@pytest.mark.asyncio
async def test_an_mkb_refusal_does_not_reach_the_browser_as_a_crash(services):
    node = await services.create_card(CardCreate(type=NODE_TYPE))
    # MKB's exceptions are not ValueErrors, so without the plugin's guard asking for a
    # well-formed id that does not exist would leave the handler as a 500.
    with pytest.raises(ResourceValidationError) as failure:
        await action(services, node.id, "projections",
                     projection_id="00000000-0000-0000-0000-000000000000")
    assert "not found" in str(failure.value).lower()


@pytest.mark.asyncio
async def test_create_and_delete_own_the_storage_directory(services):
    node = await services.create_card(CardCreate(type=NODE_TYPE))
    storage = services.resources.node_storage_path(node.id)
    assert (storage / "knowledge.db").exists()  # created eagerly, not on first action

    await action(services, node.id, "ingest", filename="note.md",
                 content_base64=base64.b64encode(b"# Note\n\ntext").decode(),
                 media_type="text/markdown")
    await services.delete_card(node.id)
    assert not storage.exists()


def test_the_card_survives_a_restart(data_root):
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        assert NODE_TYPE in {item["id"] for item in client.get("/api/catalog").json()["node_types"]}
        node = create_node(client, NODE_TYPE)
        url = f"/api/nodes/{node['id']}/resource/"
        assert client.post(url + "settings", json={"arguments": {"collection_name": "Alloys"}}).status_code == 200
        upload = client.post(url + "ingest", json={"arguments": {
            "filename": "note.md", "media_type": "text/markdown",
            "content_base64": base64.b64encode(b"# Note\n\nSome text.").decode()}})
        assert upload.status_code == 200, upload.text

    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/nodes/{node['id']}").json()["status"] == "available"
        overview = client.post(url + "overview", json={"arguments": {}})
        assert overview.status_code == 200, overview.text
        assert overview.json()["collection"]["name"] == "Alloys"
        assert overview.json()["counts"]["sources"] == 1
        # A bad argument is a 422 with the plugin's own message, not a 500.
        bad = client.post(url + "markdown", json={"arguments": {"source_id": "nope"}})
        assert bad.status_code == 422, bad.text
        assert client.delete(f"/api/nodes/{node['id']}").status_code == 200


PRESET = "knowledge.base.research"


def resource(client, node_id, operation, **arguments):
    response = client.post(f"/api/nodes/{node_id}/resource/{operation}",
                           json={"arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_research_formation_deploys_wired_independent_bases(client):
    preset = next(item for item in client.get("/api/legions/presets").json()
                  if item["id"] == PRESET)
    assert preset["preset"] and not preset["starter"] and preset["compatible"], preset

    instances = []
    for _ in range(2):
        response = client.post(f"/api/legions/presets/{PRESET}/instances", json={})
        assert response.status_code == 201, response.text
        instances.append(response.json())
    first, second = instances
    a, b = first["node_ids"], second["node_ids"]
    assert set(a) == {"group", "agent", "conversation", "knowledge"}
    assert set(a.values()).isdisjoint(b.values())

    nodes = {node["id"]: node for node in first["nodes"]}
    assert all(nodes[a[key]]["parent_id"] == a["group"] for key in a if key != "group")
    assert nodes[a["knowledge"]]["type"] == NODE_TYPE
    assert "knowledge_graph" in nodes[a["agent"]]["config"]["system_instruction"]
    # The person drives the pipeline from the workspace; the Librarian only reads.
    assert {(edge["source"], edge["target"], edge["relationship"]) for edge in first["edges"]} == {
        (a["agent"], a["conversation"], "participate"),
        (a["agent"], a["knowledge"], f"{NODE_TYPE}.read")}

    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = {tool.name for tool in client.portal.call(provider.list_tools, a["agent"])}
    assert {f"knowledge_{name}" for name in READ_ACTIONS} <= tools
    # Extraction is a button, not a tool: nothing the Librarian holds can write.
    assert not tools & {"knowledge_projection_prompt", "knowledge_save_projection",
                        "knowledge_draft", "knowledge_ingest", "knowledge_review"}

    layout = json.dumps(nodes[a["group"]]["config"]["workspace_layout"])
    assert all(a[key] in layout for key in ("conversation", "knowledge"))
    assert all(section in layout for section in
               ("sessions", "sources", "conversation", "markdown", "graph", "review"))

    # Each deployment owns its database: a document in one is invisible in the other.
    resource(client, a["knowledge"], "ingest", filename="note.md", media_type="text/markdown",
             content_base64=base64.b64encode(b"# Note\n\nSome text.").decode())
    assert resource(client, a["knowledge"], "overview")["counts"]["sources"] == 1
    assert resource(client, b["knowledge"], "overview")["counts"]["sources"] == 0


def test_a_saved_knowledge_legion_deploys_an_empty_working_base(client):
    deployed = client.post(f"/api/legions/presets/{PRESET}/instances", json={}).json()
    keys = deployed["node_ids"]
    resource(client, keys["knowledge"], "settings", collection_name="Alloys")
    resource(client, keys["knowledge"], "ingest", filename="note.md", media_type="text/markdown",
             content_base64=base64.b64encode(b"# Note\n\nSome text.").decode())

    saved = client.post("/api/legions", json={"name": "Alloy research",
                                              "node_ids": list(keys.values())})
    assert saved.status_code == 201, saved.text
    copy = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
    assert copy.status_code == 201, copy.text

    copied = next(node for node in copy.json()["nodes"] if node["type"] == NODE_TYPE)
    assert copied["id"] != keys["knowledge"]
    assert copied["status"] == "available"
    # Capture never carries the files: a copied base is a fresh, usable, empty one,
    # settings included — they live in the card's document, which is not captured.
    overview = resource(client, copied["id"], "overview")
    assert overview["counts"]["sources"] == 0
    assert overview["settings"] == {"collection_name": "Knowledge base",
                                    "pdf_engine": "auto", "mineru_base_url": ""}
    assert resource(client, keys["knowledge"], "overview")["counts"]["sources"] == 1

    group = next(node for node in copy.json()["nodes"] if node["type"] == "legion")
    layout = json.dumps(group["config"]["workspace_layout"])
    assert copied["id"] in layout
    assert not any(node_id in layout for node_id in keys.values())
