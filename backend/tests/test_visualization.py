"""Real HTTP dataset authorization, schema selection, limits and durable config."""
from threading import Event
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.tests.conftest import create_node
from open_agent_world.plugin_api import NodeResourceContext


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings.for_data_root(tmp_path / "profile"))) as value:
        yield value


def connect(client, chart, source):
    response = client.post("/api/edges", json={"source": chart["id"], "target": source["id"],
                                             "relationship": "data.visualization.source"})
    assert response.status_code == 201, response.text
    return response.json()


def read(client, chart, source, operation="read", **arguments):
    return client.post(f"/api/nodes/{chart['id']}/data-sources/{source['id']}/{operation}",
                       json={"arguments": arguments})


def sql(client, source, statement, version=0):
    response = client.post(f"/api/nodes/{source['id']}/resource/write",
                           json={"arguments": {"sql": statement, "schema_version": version}})
    assert response.status_code == 200, response.text


def test_sql_schema_aggregates_revocation_and_config_restart(tmp_path):
    settings = Settings.for_data_root(tmp_path / "profile")
    with TestClient(create_app(settings)) as client:
        source = create_node(client, "data.sqlite")
        chart = create_node(client, "data.visualization.bar")
        other = create_node(client, "data.sqlite")
        sql(client, source, 'CREATE TABLE "readings" (category TEXT, value REAL)')
        sql(client, source, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3500) INSERT INTO readings SELECT 'sample', x FROM n", 1)
        assert read(client, chart, source, "schemas").status_code == 403
        edge = connect(client, chart, source)
        assert client.get(f"/api/nodes/{chart['id']}/data-sources").json()["sources"][0]["id"] == source["id"]
        schema = read(client, chart, source, "schemas").json()["schemas"][0]
        assert schema["id"] == "readings" and schema["fields"][1]["type"] == "number"
        result = read(client, chart, source, schema_id="readings", columns=["value"], limit=10000)
        assert result.status_code == 200, result.text
        assert len(result.json()["rows"]) == 3500
        assert not result.json()["truncated"]
        result = read(client, chart, source, schema_id="readings", columns=["category", "value"], limit=2)
        assert len(result.json()["rows"]) == 2 and result.json()["truncated"]
        aggregate = read(client, chart, source, schema_id="readings", group_by="category", aggregate="count", limit=1)
        assert aggregate.json()["rows"] == [["sample", 3500]]
        assert not aggregate.json()["truncated"]
        assert aggregate.json()["scope"] == "full"
        same_name = read(client, chart, source, schema_id="readings", group_by="value", aggregate="count", limit=1).json()
        assert same_name["columns"] == ["value", "__aggregate_value"]
        assert same_name["value_column"] == "__aggregate_value"
        assert read(client, chart, other, "schemas").status_code == 403
        assert read(client, chart, source, schema_id='readings"; DROP TABLE readings; --').status_code == 422
        assert read(client, chart, source, schema_id="readings", columns=['value" FROM readings; --']).status_code == 422
        assert read(client, chart, source, schema_id="readings", sql="DELETE FROM readings").status_code == 422
        assert read(client, chart, source, "write", sql="DELETE FROM readings").status_code == 422
        assert read(client, chart, source, schema_id="readings", limit=10001).status_code == 422
        response = client.patch(f"/api/nodes/{chart['id']}", json={"config": {
            "source_id": source["id"], "schema_id": "readings", "x": "category", "y": "value", "aggregate": "mean"}})
        assert response.status_code == 200, response.text
        client.delete(f"/api/edges/{edge['id']}")
        assert read(client, chart, source, schema_id="readings").status_code == 403
        connect(client, chart, source)
    with TestClient(create_app(settings)) as client:
        saved = client.get(f"/api/nodes/{chart['id']}").json()["config"]
        assert saved["aggregate"] == "mean" and saved["source_id"] == source["id"]
        result = read(client, chart, source, schema_id="readings", group_by="category", value="value", aggregate="mean")
        assert result.json()["rows"] == [["sample", 1750.5]]


def test_catalog_default_details_and_pack(client):
    catalog = client.get("/api/catalog").json()
    charts = [item for item in catalog["node_types"] if item["plugin_id"] == "data.visualization"]
    assert len(charts) == 5
    assert all(item["presentation"]["initial"] == "inspector" for item in charts)
    assert all(item["presentation"]["sizes"]["inspector"]["width"] >= 680 for item in charts)
    assert any(pack["id"] == "data.visualization.default" for pack in catalog["packs"])


def test_schema_preview_handles_nullable_type_arrays_without_granting_data_access(client):
    source = create_node(client, "knowledge.base")
    chart = create_node(client, "data.visualization.line")
    response = client.post(f"/api/nodes/{source['id']}/resource/schemas", json={"arguments": {
        "operation": "create", "name": "Nullable readings", "kind": "experiment",
        "definition": {"type": "object", "properties": {
            "samples": {"type": ["array", "null"], "items": {"type": ["object", "null"], "properties": {
                "step": {"type": ["integer", "null"]}, "value": {"type": ["number", "null"]},
                "name": {"type": ["string", "null"]}, "extra": True,
                "nested": {"type": ["object", "null"], "properties": {"value": {"type": ["number", "null"]}}}
            }}}}}, "system_prompt": "Extract readings."}})
    assert response.status_code == 200, response.text
    preview_url = f"/api/nodes/{chart['id']}/data-source-schemas"
    response = client.post(preview_url, json={"source_id": source['id'], "relationship": "data.visualization.source"})
    assert response.status_code == 200, response.text
    dataset = next(item for item in response.json()["schemas"] if item["id"].endswith(":samples"))
    assert dataset['fields'][:3] == [{"name": "step", "type": "number"}, {"name": "value", "type": "number"}, {"name": "name", "type": "string"}]
    assert {"name": "nested.value", "type": "number"} in dataset['fields']
    assert client.get(f"/api/nodes/{chart['id']}/data-sources").json() == {"sources": []}
    assert read(client, chart, source, schema_id=dataset['id']).status_code == 403
    denied = client.post(preview_url, json={"source_id": source['id'], "relationship": "knowledge.base.read"})
    assert denied.status_code == 403
    agent = create_node(client, "agent")
    denied = client.post(f"/api/nodes/{agent['id']}/data-source-schemas", json={"source_id": source['id'], "relationship": "data.visualization.source"})
    assert denied.status_code in (403, 422)


def test_knowledge_datasets_use_real_schema_and_published_graph(client):
    from oaw_knowledge_base import actions
    from mkb.models import Entity, Relation
    source = create_node(client, "knowledge.base")
    chart = create_node(client, "data.visualization.graph")
    connect(client, chart, source)
    services = client.app.state.services
    context = NodeResourceContext(source["id"], services.resources.node_storage_path(source["id"]), Event(),
                                  state=services.card_state.bind(source["id"]))
    kb = actions._client(context)
    dates = {"created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
    a = Entity(id=uuid4(), type="Material", name="Copper", **dates)
    b = Entity(id=uuid4(), type="Property", name="Conductivity", **dates)
    kb.oaw_graph_store.upsert_entity(a)
    kb.oaw_graph_store.upsert_entity(b)
    kb.oaw_graph_store.upsert_relation(Relation(id=uuid4(), source_id=a.id, target_id=b.id, type="has", **dates))
    schema = actions.schemas(context, {"operation": "create", "name": "Measurements", "kind": "experiment",
        "definition": {"type": "object", "properties": {"sample": {"type": "string"}, "reading": {"type": "number"}}},
        "system_prompt": "Extract sample and reading."})["schema"]
    result = read(client, chart, source, "schemas")
    assert result.status_code == 200, result.text
    dataset = next(item for item in result.json()["schemas"] if item["id"] == f"experiments:{schema['id']}")
    assert dataset["fields"] == [{"name": "sample", "type": "string"}, {"name": "reading", "type": "number"}]
    graph = read(client, chart, source, schema_id="graph")
    assert graph.status_code == 200, graph.text
    assert len(graph.json()["nodes"]) == 2 and len(graph.json()["edges"]) == 1
    limited = read(client, chart, source, schema_id="graph", limit=1).json()
    assert limited["truncated"] and limited["edges"] == []
    kb.oaw_experiments.create(group_id=uuid4(), schema_id=schema['id'], name="Copper",
                              data={"sample": "Copper", "reading": 12.5})
    records = read(client, chart, source, schema_id=f"experiments:{schema['id']}", columns=["sample", "reading"])
    assert records.status_code == 200, records.text
    assert records.json()["columns"] == ["sample", "reading"]
    assert records.json()["rows"] == [["Copper", 12.5]]
    assert read(client, chart, source, schema_id="experiments:00000000-0000-0000-0000-000000000000").status_code == 422


def test_knowledge_schema_references_and_nested_array_records(client):
    from oaw_knowledge_base import actions
    from oaw_knowledge_base.data_source import fields
    source = create_node(client, "knowledge.base")
    chart = create_node(client, "data.visualization.line")
    connect(client, chart, source)
    services = client.app.state.services
    context = NodeResourceContext(source["id"], services.resources.node_storage_path(source["id"]), Event(),
                                  state=services.card_state.bind(source["id"]))
    definition = {"type": "object", "$defs": {"Reading": {"type": "object", "properties": {
        "step": {"type": "integer"}, "value": {"anyOf": [{"type": "number"}, {"type": "null"}]}}}},
        "properties": {"samples": {"type": "array", "items": {"$ref": "#/$defs/Reading"}}}}
    schema = actions.schemas(context, {"operation": "create", "name": "Series", "kind": "experiment",
        "definition": definition, "system_prompt": "Extract the readings."})["schema"]
    kb = actions._client(context)
    kb.oaw_experiments.create(group_id=uuid4(), schema_id=schema['id'], name="Run",
                              data={"samples": [{"step": 1, "value": 2.5}, {"step": 2, "value": None}]})
    schemas = read(client, chart, source, "schemas").json()["schemas"]
    item = next(item for item in schemas if item["id"].endswith(":samples"))
    assert item["fields"] == [{"name": "step", "type": "number"}, {"name": "value", "type": "number"}]
    result = read(client, chart, source, schema_id=item["id"], columns=["step", "value"]).json()
    assert result["rows"] == [[1, 2.5], [2, None]]
    assert not result["truncated"]
    recursive = {"$defs": {"Node": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]}},
                 "properties": {"child": {"$ref": "#/$defs/Node"}}}
    assert fields(recursive) == [{"name": "child", "type": "string"}]
