from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.tests.conftest import create_node


def test_events_discover_actual_sandbox_tools_and_revoke_with_the_connection(client):
    agent = create_node(client, "agent", name="Worker")
    sandbox = create_node(client, "sandbox", name="Workspace")
    other = create_node(client, "sandbox", name="Unconnected")
    path = f"/api/state-machines/events?agent_id={agent['id']}"
    assert client.get(path).json()["operations"] == []
    edge = client.post("/api/edges", json={"source": agent["id"], "target": sandbox["id"], "relationship": "execute"})
    assert edge.status_code == 201, edge.text
    before = client.get("/api/world").json()
    operations = client.get(path).json()["operations"]
    kinds = {op["kind"] for op in operations}
    assert {"sandbox.execute", "sandbox.inspect", "sandbox.wait", "sandbox.cancel_command", "sandbox.install_python_packages"} <= kinds
    assert {"sandbox.start", "sandbox.stop", "sandbox.run_skill_script"}.isdisjoint(kinds)
    assert {op["target_card_id"] for op in operations} == {sandbox["id"]}
    assert next(op for op in operations if op["kind"] == "sandbox.execute")["label"] == "Run a command"
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = client.portal.call(provider.list_tools, agent["id"])
    assert {op["tool_name"] for op in operations} == {tool.name for tool in tools}
    assert client.get("/api/world").json() == before
    assert client.delete(f"/api/edges/{edge.json()['id']}").status_code == 200
    assert client.get(path).json()["operations"] == []


def test_operation_catalog_distinguishes_targets_and_manage_permissions(client):
    agent = create_node(client, "agent")
    sandboxes = [create_node(client, "sandbox", name=name) for name in ("Build", "Review")]
    for sandbox, relationship in zip(sandboxes, ("execute", "execute_manage")):
        response = client.post("/api/edges", json={"source": agent["id"], "target": sandbox["id"], "relationship": relationship})
        assert response.status_code == 201, response.text
    operations = client.get(f"/api/state-machines/events?agent_id={agent['id']}").json()["operations"]
    commands = [op for op in operations if op["kind"] == "sandbox.execute"]
    assert {op["target_card_id"] for op in commands} == {sandbox["id"] for sandbox in sandboxes}
    assert {op["target_name"] for op in commands} == {"Build", "Review"}
    assert {op["target_card_id"] for op in operations if op["kind"] == "sandbox.stop"} == {sandboxes[1]["id"]}
    assert client.get("/api/state-machines/events").json()["operations"] == []
    assert client.get("/api/state-machines/events?agent_id=missing").status_code == 404
