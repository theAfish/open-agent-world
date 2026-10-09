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
from oaw_knowledge_base.operations import BY_NAME, EXTRACT_ACTIONS, OPERATIONS, READ_ACTIONS  # noqa: E402

NODE_TYPE = "knowledge.base"
# Uploading a file, converting it, managing groups, changing settings, approving a
# draft and choosing which schema builds the graph are acts a person takes on the
# canvas. They must never gain a capability kind, on any transport.
DESKTOP_ONLY = {"ingest", "process", "groups", "settings", "review", "graph_schema"}


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


def test_mineru_secret_is_encrypted_survives_restart_and_is_scoped(data_root, monkeypatch):
    from backend.security.resource_credentials import resource_secret_resolver

    monkeypatch.setenv("OAW_MINERU_TOKEN", "external-token")
    settings = Settings.for_data_root(data_root)
    app = create_app(settings)
    with TestClient(app) as client:
        node = create_node(client, NODE_TYPE)
        other = create_node(client, NODE_TYPE)
        url = f"/api/knowledge/{node['id']}/mineru-token"
        assert client.get(url).json() == {"configured": True, "source": "external"}
        saved = client.put(url, json={"value": "private-card-token"})
        assert saved.json() == {"configured": True, "source": "card"}
        host = app.state.services
        resolve = resource_secret_resolver(host, host.world.get_card(node["id"]))
        assert resolve("OAW_MINERU_TOKEN") == "private-card-token"
        assert resource_secret_resolver(host, host.world.get_card(other["id"]))("OAW_MINERU_TOKEN") == "external-token"
        with host.database.locked() as db:
            raw = json.dumps([dict(row) for row in db.execute("SELECT * FROM application_settings")])
        assert "private-card-token" not in raw
        assert "private-card-token" not in client.get(f"/api/nodes/{node['id']}").text
        assert "private-card-token" not in json.dumps(resource(client, node["id"], "overview"))
        # Secret updates are not ordinary card resource actions available to Agents.
        assert client.post(f"/api/nodes/{node['id']}/resource/settings",
                           json={"arguments": {"mineru_token": "private-card-token"}}).status_code == 422
        for bad in ("", {"secret": "sensitive-value"}, "sensitive-value\0"):
            response = client.put(url, json={"value": bad})
            assert response.status_code == 422
            assert "sensitive-value" not in response.text

    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get(url).json() == {"configured": True, "source": "card"}
        assert client.put(url, json={"value": "replacement-token"}).status_code == 200
        host = app.state.services
        assert resource_secret_resolver(host, host.world.get_card(node["id"]))("OAW_MINERU_TOKEN") == "replacement-token"
        assert client.put(url, json={"value": None}).json() == {"configured": True, "source": "external"}
        monkeypatch.delenv("OAW_MINERU_TOKEN")
        assert client.get(url).json() == {"configured": False, "source": None}
        ordinary = create_node(client, "text")
        assert client.put(f"/api/knowledge/{ordinary['id']}/mineru-token", json={"value": "wrong-node"}).status_code == 422


def test_mineru_reads_global_settings_without_changing_process_environment(client, monkeypatch):
    from backend.sandbox.settings import SandboxSettings, SandboxSettingsStore
    from backend.security.resource_credentials import resource_secret_resolver

    monkeypatch.setenv("OAW_MINERU_TOKEN", "process-token")
    node = create_node(client, NODE_TYPE)
    services = client.app.state.services
    SandboxSettingsStore(services.database, services.settings.data_root).save(
        SandboxSettings(environment_variables={"OAW_MINERU_TOKEN": "global-token"}))
    resolve = resource_secret_resolver(services, services.world.get_card(node["id"]))
    assert resolve("OAW_MINERU_TOKEN") == "global-token"
    resource(client, node["id"], "settings", mineru_base_url="https://mineru.example")
    assert "mineru" in resource(client, node["id"], "overview")["engines"]
    import os
    assert os.environ["OAW_MINERU_TOKEN"] == "process-token"
    from backend.execution_config import effective_variables
    from backend.sandbox.environment import validate_command_environment
    from backend.sandbox.models import SandboxValidationError
    sandbox = create_node(client, "sandbox")
    assert not effective_variables(services, sandbox["id"])[1]
    with pytest.raises(SandboxValidationError):
        validate_command_environment({"OAW_MINERU_TOKEN": "must-not-be-injected"})
    with pytest.raises(ValueError):
        SandboxSettings(environment_variables={"OAW_CONTROL_TOKEN": "still-reserved"})


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
    expected_tool_names = {BY_NAME[name].tool_name for name in READ_ACTIONS}
    assert expected_tool_names <= tools
    # Extraction is a button, not a tool: nothing the Librarian holds can write.
    assert not tools & {"knowledge_projection_prompt", "knowledge_save_projection",
                        "knowledge_draft", "knowledge_ingest", "knowledge_review"}

    layout = json.dumps(nodes[a["group"]]["config"]["workspace_layout"])
    assert all(a[key] in layout for key in ("conversation", "knowledge"))
    assert all(section in layout for section in ("sessions", "conversation"))
    # The knowledge card is placed whole (no section_id): its own frontend renders
    # the Literature/Experiment rail and each workflow's tabs itself, so no
    # individual section (sources, markdown, graph, review, ...) needs its own
    # place in the generic layout to be reachable.
    assert '"card_id": "' + a["knowledge"] + '"}' in layout

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


def test_the_research_formation_publishes_and_serves_as_a_deployment(data_root):
    """The whole "deploy mode" loop: publish, copy, serve, and drive the pipeline
    from the locked runtime — settings stay out, everything else stays in."""
    from backend.config import Settings as ConfigSettings
    from backend.deploy import create_deployment

    with TestClient(create_app(ConfigSettings.for_data_root(data_root)),
                    client=("127.0.0.1", 50000)) as engineering:
        deployed = engineering.post(f"/api/legions/presets/{PRESET}/instances", json={}).json()
        keys = deployed["node_ids"]
        resource(engineering, keys["knowledge"], "ingest", filename="note.md", media_type="text/markdown",
                content_base64=base64.b64encode(b"# Note\n\nSome text.").decode())
        release = engineering.post("/api/deployments",
                                   json={"legion_id": keys["group"], "name": "Knowledge demo"})
        assert release.status_code == 201, release.text
        release_id = release.json()["id"]

    runtime_root = data_root.with_name(data_root.name + "-runtime")
    create_deployment(data_root, runtime_root, release_id, password="a-fine-long-password")
    manifest = json.loads((runtime_root / "deployment.json").read_text("utf-8"))
    # Section names match the card's own WorkspaceSection ids: Sources absorbs
    # search, markdown and the single-document projection convenience; Graph
    # absorbs the graph-schema pipeline (project, draft, review, publish) that used
    # to be split across three separate tabs.
    assert set(manifest["permissions"][keys["knowledge"]]) == {
        "settings", "sources", "schemas", "projections", "experiments", "graph"}
    access = manifest["plugin_access"][keys["knowledge"]]
    # Settings never appears among the granted resource actions, so a business
    # release can never rewrite the collection name, PDF engine or MinerU URL,
    # regardless of whether its pane stays visible in the published layout.
    assert "settings" not in access["resource_actions"]
    assert access["config_fields"] == ["default_model"]
    # Search, markdown and choosing the graph schema are still real, callable
    # resource actions — just grouped under the sections above, not section names
    # of their own.
    assert {"search", "markdown", "graph_schema"} <= set(access["resource_actions"])

    with TestClient(create_app(ConfigSettings.for_data_root(runtime_root)),
                    client=("127.0.0.1", 50000)) as deployed_client:
        login = deployed_client.post("/api/deployment/session", json={"password": "a-fine-long-password"})
        assert login.status_code == 200, login.text
        node_url = f"/api/runtime-app/workspace/nodes/{keys['knowledge']}/resource/"

        sources = deployed_client.post(node_url + "sources", json={"arguments": {}})
        assert sources.status_code == 200, sources.text
        assert sources.json()["sources"][0]["filename"] == "note.md"

        blocked = deployed_client.post(node_url + "settings", json={"arguments": {}})
        assert blocked.status_code == 404, blocked.text

        # Bootstrap projects only the declared config field, never the internal database.
        bootstrap = deployed_client.get("/api/runtime-app").json()
        card = next(item for item in bootstrap["cards"] if item["id"] == keys["knowledge"])
        assert card["config"] == {"default_model": ""}
