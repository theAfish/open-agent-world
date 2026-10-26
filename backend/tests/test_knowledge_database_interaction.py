"""A research formation can use an independently connected database safely."""

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.tests.conftest import create_node


def test_research_assistant_reads_connected_database_without_expanding_its_scope(client):
    response = client.post("/api/legions/presets/knowledge.base.research/instances", json={})
    assert response.status_code == 201, response.text
    formation = response.json()
    agent_id = formation["node_ids"]["agent"]
    knowledge_id = formation["node_ids"]["knowledge"]
    assert any(edge["source"] == agent_id and edge["target"] == knowledge_id
               and edge["relationship"] == "knowledge.base.research" for edge in formation["edges"])

    database = create_node(client, "data.sqlite", name="Measured samples")
    private_database = create_node(client, "data.sqlite", name="Unconnected samples")
    url = f"/api/nodes/{database['id']}/resource/"
    for sql, schema_version in (
        ("CREATE TABLE samples (name TEXT, conductivity REAL)", 0),
        ("INSERT INTO samples VALUES ('A', 1.2), ('B', 0.8), ('C', 0.4)", 1),
    ):
        seeded = client.post(url + "write", json={"arguments": {
            "sql": sql, "schema_version": schema_version,
        }})
        assert seeded.status_code == 200, seeded.text
    response = client.post("/api/edges", json={
        "source": agent_id, "target": database["id"], "relationship": "data.sqlite.read",
    })
    assert response.status_code == 201, response.text
    edge_id = response.json()["id"]

    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = {tool.name: tool for tool in client.portal.call(provider.list_tools, agent_id)}
    assert {"knowledge_overview", "inspect_database", "query_database"} <= tools.keys()
    assert not {"write_database", "modify_database"} & tools.keys()

    def invoke(name, **arguments):
        return client.portal.call(provider.invoke_tool, agent_id, tools[name].capability_id, arguments)

    # The assistant can discover and inspect the connected database by its readable
    # selector, without asking a novice for SQL or a local database file.
    selector = tools["inspect_database"].input_schema["properties"]["target"]
    assert selector["enum"] == ["measured_samples"]
    schema = invoke("inspect_database", target="measured_samples")
    assert any(item["name"] == "samples" for item in schema["objects"])
    table = invoke("inspect_database", target="measured_samples", table="samples")
    assert [column["name"] for column in table["columns"]] == ["name", "conductivity"]
    result = invoke("query_database", target="measured_samples",
                    sql="SELECT name, conductivity FROM samples WHERE conductivity >= ? ORDER BY name LIMIT 3",
                    parameters=[0.4], max_rows=2)
    assert result["rows"] == [["A", 1.2], ["B", 0.8]]
    assert result["truncated"] is True

    # A Knowledge research grant neither selects arbitrary SQL cards nor makes
    # the knowledge base's internal storage an SQL database target.
    for target in (private_database["id"], knowledge_id):
        with pytest.raises(PermissionDeniedError):
            invoke("query_database", target=target, sql="SELECT 1")
    assert client.delete(f"/api/edges/{edge_id}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        invoke("query_database", target=database["id"], sql="SELECT 1")
    overview = invoke("knowledge_overview", knowledge=knowledge_id)
    assert overview["counts"]["sources"] == 0
    remaining = {tool.name for tool in client.portal.call(provider.list_tools, agent_id)}
    assert "knowledge_overview" in remaining
    assert not {"inspect_database", "query_database"} & remaining
