"""Host integration contract: policy, durable namespaces, legacy data, lifecycle."""
from dataclasses import replace
from unittest.mock import patch

import pytest
from pydantic import BaseModel, ValidationError

from backend.card_state import state_session
from backend.errors import PermissionDeniedError, RevisionConflictError
from backend.plugins.registry import PluginDefinition, PluginDescriptor
from backend.plugins.state import ScopedStateSpec, StatelessStateSpec
from backend.tests.conftest import create_node


class EmptyConfig(BaseModel):
    pass


def counts(services):
    with services.database.locked() as db:
        return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("cards", "card_state_instances", "state_scopes"))


def sessions(client):
    conversation = create_node(client, "conversation")
    a = client.get(f"/api/conversations/{conversation['id']}").json()["sessions"][0]
    b = client.post(f"/api/conversations/{conversation['id']}/sessions", json={"title": "B"}).json()
    return conversation, a, b


def headers(session):
    return {"X-OAW-State-Session": session["id"]}


def install(client, state=None, *, plugin_state=None):
    services = client.app.state.services
    node = replace(services.plugins.node_type("oaw.tasks"), id="test.state", state=state,
                   document=None, execution=None, traits=frozenset(), frontend={}, config_model=EmptyConfig)
    services.plugins.install(PluginDefinition(
        descriptor=PluginDescriptor(id="test", version="1", plugin_api_version="1.23", state=plugin_state),
        configure=lambda registration: registration.register_node_type(node)))
    return create_node(client, "test.state")


def test_session_creation_is_lazy_and_board_switch_preserves_graph_config(client):
    services = client.app.state.services
    board = create_node(client, "oaw.tasks", config={"description": "Fixed card setting"})
    conversation, a, b = sessions(client)
    before = counts(services)
    assert before[1] == 0
    url = f"/api/nodes/{board['id']}"
    assert board["state_scope"] == "session"
    assert board["state_scope_override"] is None
    saved = client.post(url + "/actions/upsert", headers=headers(a), json={"expected_revision": 0,
        "arguments": {"tasks": [{"id": "one", "title": "A task"}]}})
    assert saved.status_code == 200, saved.text
    assert client.get(url + "/document", headers=headers(b)).json()["value"]["tasks"] == []
    assert client.get(url + "/document", headers=headers(a)).json() == saved.json()
    assert client.get(url).json()["config"] == board["config"]
    after = counts(services)
    assert after[0] == before[0] and after[1] == 2
    client.post(f"/api/conversations/{conversation['id']}/sessions", json={"title": "Unused"})
    assert counts(services)[:2] == after[:2]


def test_shared_state_and_atomic_updates_conflicts_and_delete(client):
    services = client.app.state.services
    node = install(client, ScopedStateSpec())
    _, a, b = sessions(client)
    with state_session(a["id"]):
        state_a = services.card_state.bind(node["id"])
        assert state_a.get() == {"value": {}, "revision": 0}
        state_a.set({"x": 1}, 0)
    with state_session(b["id"]):
        state_b = services.card_state.bind(node["id"])
        assert state_b.get()["value"] == {"x": 1}
        assert state_b.update({"y": 2}, 1)["value"] == {"x": 1, "y": 2}
        with pytest.raises(RevisionConflictError):
            state_a.set({"lost": True}, 1)
        assert state_b.delete(2) == {"value": {}, "revision": 3}
    assert counts(services)[1] == 1


def test_stateless_has_no_capability_or_namespace_resolution(client):
    services = client.app.state.services
    viewer = create_node(client, "science.structure-viewer")
    before = counts(services)
    with patch.object(services.card_state, "session_id", side_effect=AssertionError("must not resolve")):
        assert services.card_state.bind(viewer["id"]) is None
        for method in ("get", "put"):
            kwargs = {"json": {"value": {"x": 1}}} if method == "put" else {}
            response = getattr(client, method)(f"/api/nodes/{viewer['id']}/state", headers={"X-OAW-State-Session": "invalid"}, **kwargs)
            assert response.status_code == 403
    assert counts(services) == before
    assert viewer["state_scope"] is None


def test_legacy_document_namespace_and_config_are_unchanged(client):
    services = client.app.state.services
    node = create_node(client, "sandbox")
    # Legacy document access retains its existing owner ID and defaults.
    from backend.node_documents import read_document, write_document
    before = read_document(services, node["id"])
    with state_session("nonexistent-session-ignored-by-shared"):
        assert read_document(services, node["id"]) == before
    assert services.state.get_scope("node_document", node["id"])
    assert services.card_state.existing(node["id"]) == []
    catalog = client.get("/api/catalog").json()
    state = next(item for item in catalog["node_types"] if item["id"] == "sandbox")["state"]
    assert state == {"mode": "scoped", "supportedScopes": ["shared"], "defaultScope": "shared", "userConfigurable": False}


def test_developer_defaults_follow_updates_and_user_overrides_are_validated(client):
    policy = ScopedStateSpec(supportedScopes=("shared", "session"), userConfigurable=True)
    node = install(client, plugin_state=policy)
    services = client.app.state.services
    url = f"/api/nodes/{node['id']}"
    assert node["state_scope"] == "shared"
    definition = services.plugins.node_type(node["type"])
    services.plugins._nodes[node["type"]] = replace(definition, state=policy.model_copy(update={"default_scope": "session"}))
    assert client.get(url).json()["state_scope"] == "session"
    assert client.patch(url, json={"state_scope": "shared"}).status_code == 200
    assert client.get(url).json()["state_scope_override"] == "shared"
    assert client.patch(url, json={"state_scope": None}).json()["state_scope"] == "session"
    board = create_node(client, "oaw.tasks")
    assert client.patch(f"/api/nodes/{board['id']}", json={"state_scope": "shared"}).status_code == 403
    assert client.post("/api/nodes", json={"type": "oaw.tasks", "state_scope": "shared"}).status_code == 403
    assert client.post("/api/nodes/batch-update", json={"updates": [{"node_id": board["id"], "patch": {"state_scope": "shared"}}]}).status_code == 403


def test_old_board_is_adopted_once_into_default_session_not_first_visited_session(client):
    services = client.app.state.services
    from backend.world.models import CardCreate
    legion = services.world.create_card(CardCreate(type="legion")).model_dump(mode="json")
    conversation, a, b = sessions(client)
    client.patch(f"/api/nodes/{conversation['id']}", json={"parent_id": legion["id"]})
    board = create_node(client, "oaw.tasks", parent_id=legion["id"])
    scope = services.state.ensure_scope("node_document", board["id"], schema_id="core.node_document")
    services.state.set(scope, "document", {"tasks": [{"id": "old", "title": "Keep me"}]})
    url = f"/api/nodes/{board['id']}/document"
    assert client.get(url, headers=headers(b)).json()["value"]["tasks"] == []
    restored = client.get(url, headers=headers(a)).json()
    assert restored["value"]["tasks"][0]["title"] == "Keep me"
    assert restored["revision"] == 1
    assert counts(services)[1] == 2


def test_session_and_card_deletion_clean_namespaces_and_stale_handles_fail(client):
    services = client.app.state.services
    board = create_node(client, "oaw.tasks")
    conversation, a, b = sessions(client)
    with state_session(a["id"]):
        services.card_state.bind(board["id"]).set({"keep": True})
    with state_session(b["id"]):
        stale = services.card_state.bind(board["id"])
        stale.set({"remove": True})
    assert client.delete(f"/api/conversations/{conversation['id']}/sessions/{b['id']}").status_code == 204
    assert services.card_state.existing(board["id"]) == [("session", a["id"])]
    with pytest.raises(Exception, match="no longer exists"):
        stale.update({"late": True})
    assert client.delete(f"/api/nodes/{board['id']}").status_code == 200
    assert counts(services)[1] == 0
    with services.database.locked() as db:
        assert db.execute("SELECT count(*) FROM state_scopes WHERE scope_kind='node_document'").fetchone()[0] == 0


@pytest.mark.parametrize("value", [
    {"supportedScopes": []}, {"supportedScopes": ["shared", "shared"]},
    {"supportedScopes": ["shared"], "defaultScope": "session"}, {"defaultScope": "run"},
])
def test_invalid_policies_fail_early(value):
    with pytest.raises(ValidationError):
        ScopedStateSpec(**value)


def test_undo_snapshot_restores_all_sessions_and_never_execution_history(client):
    board = create_node(client, "oaw.tasks")
    _, a, b = sessions(client)
    url = f"/api/nodes/{board['id']}"
    for session in (a, b):
        response = client.post(url + "/actions/upsert", headers=headers(session), json={"expected_revision": 0,
            "arguments": {"tasks": [{"id": "task", "title": session["id"]}]}})
        assert response.status_code == 200
    snapshot = client.get(url + "/state-snapshot").json()
    assert len(snapshot["namespaces"]) == 2
    assert all("execution" not in entry["values"] for entry in snapshot["namespaces"])
    assert client.post(url + "/state-snapshot", json=snapshot).status_code == 409
    client.delete(url)
    create_node(client, "oaw.tasks", id=board["id"])
    assert client.post(url + "/state-snapshot", json=snapshot).status_code == 200
    for session in (a, b):
        assert client.get(url + "/document", headers=headers(session)).json()["value"]["tasks"][0]["title"] == session["id"]


def test_background_execution_and_agent_tools_are_pinned_to_origin_session(client):
    import asyncio
    from backend.tests.test_runs import RecordingProvider
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    services = client.app.state.services
    board = create_node(client, "oaw.tasks")
    conversation, a, b = sessions(client)
    agent = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    client.post("/api/edges", json={"source": board["id"], "target": agent["id"], "relationship": "oaw.tasks.executor"})
    client.post("/api/edges", json={"source": agent["id"], "target": board["id"], "relationship": "oaw.tasks.edit"})
    # Agent tools resolve the host's invocation context, with no session tool argument.
    provider = WorldAgentCapabilityProvider(services)
    capability = next(c for c in services.capabilities.derive(agent["id"]).capabilities if c.kind == "oaw.tasks.upsert")
    async def tool_write():
        with state_session(a["id"]):
            await provider.invoke_tool(agent["id"], capability.id, {"tasks": [{"id": "one", "title": "Origin task", "executor_id": agent["id"]}], "expected_revision": 0})
    client.portal.call(tool_write)
    runtime = RecordingProvider(mode="waiting")
    services.run_manager.install_provider("core.mock", runtime)
    url = f"/api/nodes/{board['id']}"
    response = client.post(url + "/execution/start", headers=headers(a), json={"expected_revision": 1})
    assert response.status_code == 200, response.text
    async def started():
        await asyncio.wait_for(runtime.started.wait(), 3)
    client.portal.call(started)
    assert runtime.contexts[0].context_id == a["id"]
    assert client.delete(f"/api/nodes/{board['id']}").status_code == 409
    assert client.delete(f"/api/nodes/{conversation['id']}").status_code == 409
    assert client.get(url + "/document", headers=headers(b)).json()["value"]["tasks"] == []

    assert client.get(url + "/execution", headers=headers(b)).json()["attempts"] == []
    # Releasing work while another namespace is active cannot redirect its output.
    async def finish():
        with state_session(b["id"]):
            runtime.mode = "success"
            runtime.release_turn.set()
            worker = services.node_execution.workers.get((board["id"], a["id"]))
            if worker:
                await asyncio.wait_for(asyncio.shield(worker), 5)
    client.portal.call(finish)
    assert client.get(url + "/document", headers=headers(a)).json()["value"]["tasks"][0]["status"] == "done"
    assert client.get(url + "/document", headers=headers(b)).json()["value"]["tasks"] == []


def test_agent_invocation_overrides_browser_context_and_revoked_grants_stay_revoked(client):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.runs import InvocationCaller, InvocationContext
    from backend.runs.manager import _current_invocation
    services = client.app.state.services
    board = create_node(client, "oaw.tasks")
    _, a, b = sessions(client)
    agent = create_node(client, "agent")
    edge = client.post("/api/edges", json={"source": agent["id"], "target": board["id"], "relationship": "oaw.tasks.edit"}).json()
    capability = next(c for c in services.capabilities.derive(agent["id"]).capabilities if c.kind == "oaw.tasks.upsert")
    provider = WorldAgentCapabilityProvider(services)
    async def invoke():
        context = InvocationContext(run_id="test-run", agent_id=agent["id"], parent_run_id=None, root_run_id="test-run",
            caller=InvocationCaller("conversation", "chat"), context_id=a["id"], task_id=None, runtime_provider_id="core.mock")
        token = _current_invocation.set(context)
        try:
            with state_session(b["id"]):
                return await provider.invoke_tool(agent["id"], capability.id, {"tasks": [{"id": "agent", "title": "Agent A"}], "expected_revision": 0})
        finally:
            _current_invocation.reset(token)
    client.portal.call(invoke)
    assert client.get(f"/api/nodes/{board['id']}/document", headers=headers(a)).json()["summary"]["total"] == 1
    assert client.get(f"/api/nodes/{board['id']}/document", headers=headers(b)).json()["summary"]["total"] == 0
    client.delete(f"/api/edges/{edge['id']}")
    with pytest.raises(PermissionDeniedError):
        client.portal.call(invoke)


def test_restart_preserves_sessions_and_does_not_initialize_unused_cards(tmp_path):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    settings = Settings.for_data_root(tmp_path / "restart")
    with TestClient(create_app(settings)) as client:
        board = create_node(client, "oaw.tasks")
        unused = create_node(client, "oaw.tasks")
        _, a, b = sessions(client)
        url = f"/api/nodes/{board['id']}"
        for session in (a, b):
            client.post(url + "/actions/upsert", headers=headers(session), json={"expected_revision": 0,
                "arguments": {"tasks": [{"id": "one", "title": session["id"]}]}})
        before = counts(client.app.state.services)
    with TestClient(create_app(settings)) as client:
        assert counts(client.app.state.services) == before
        assert client.app.state.services.card_state.existing(unused["id"]) == []
        for session in (a, b):
            assert client.get(url + "/document", headers=headers(session)).json()["value"]["tasks"][0]["title"] == session["id"]


def test_template_seeds_new_conversation_not_the_source_active_session(client):
    conversation, a, _ = sessions(client)
    board = create_node(client, "oaw.tasks")
    source_url = f"/api/nodes/{board['id']}"
    client.post(source_url + "/actions/upsert", headers=headers(a), json={"expected_revision": 0,
        "arguments": {"tasks": [{"id": "one", "title": "Template plan"}]}})
    saved = client.post("/api/legions", headers=headers(a), json={"name": "Plan", "node_ids": [board["id"], conversation["id"]]})
    assert saved.status_code == 201, saved.text
    restored = client.post(f"/api/legions/{saved.json()['id']}/instances", headers=headers(a), json={"as_group": True})
    assert restored.status_code == 201, restored.text
    copied = next(node for node in restored.json()["nodes"] if node["type"] == "oaw.tasks")
    assert client.get(f"/api/nodes/{copied['id']}/document").json()["value"]["tasks"][0]["title"] == "Template plan"
    namespaces = client.app.state.services.card_state.existing(copied["id"])
    assert len(namespaces) == 1 and namespaces[0][1] != a["id"]


def test_scope_choice_preserves_both_namespaces_and_reset_uses_default(client):
    node = install(client, ScopedStateSpec(supportedScopes=("shared", "session"), userConfigurable=True))
    _, a, b = sessions(client)
    url = f"/api/nodes/{node['id']}"
    client.put(url + "/state", json={"value": {"shared": True}}, headers=headers(a))
    assert client.patch(url, json={"state_scope": "session"}).status_code == 200
    assert client.get(url + "/state", headers=headers(a)).json()["value"] == {}
    client.put(url + "/state", json={"value": {"a": True}}, headers=headers(a))
    assert client.get(url + "/state", headers=headers(b)).json()["value"] == {}
    assert client.patch(url, json={"state_scope": None}).status_code == 200
    assert client.get(url + "/state", headers=headers(b)).json()["value"] == {"shared": True}
    client.patch(url, json={"state_scope": "session"})
    assert client.get(url + "/state", headers=headers(a)).json()["value"] == {"a": True}
