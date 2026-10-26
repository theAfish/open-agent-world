"""The service boundaries: the token, the admin gate, and the collection pin.

The routing itself is generic — one handler over the operations table — so these
tests are about what the HTTP layer adds on top of the pipeline rather than about
the pipeline, which ``test_pipeline.py`` already covers end to end.
"""
import base64
import json

import pytest

pytest.importorskip("mkb", reason="Install mat-know-base into this environment")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from oaw_knowledge_base.service.app import create_app  # noqa: E402
from oaw_knowledge_base.service.store import Store  # noqa: E402

TOKEN = "service-token"
ADMIN = "admin-token"
SCHEMA = {"type": "object", "required": ["entities"],
          "properties": {"entities": {"type": "array", "items": {"type": "object"}},
                         "relations": {"type": "array", "items": {"type": "object"}}}}
DOCUMENT = "# Sintering\n\nSi3N4 powder was sintered at 1750 C.\n"


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "store")
    yield instance
    instance.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store, token=TOKEN)) as test_client:
        test_client.headers["authorization"] = f"Bearer {TOKEN}"
        yield test_client


@pytest.fixture
def admin(store):
    """A second front door onto the same store, the one an operator would run."""
    with TestClient(create_app(store, token=TOKEN, admin_token=ADMIN)) as test_client:
        test_client.headers["authorization"] = f"Bearer {ADMIN}"
        yield test_client


def call(test_client, action, collection="default", **arguments):
    return test_client.post(f"/v1/collections/{collection}/{action}", json=arguments)


def names(test_client):
    return {item["name"] for item in test_client.get("/v1/collections").json()["collections"]}


def ingest(test_client, store, text=DOCUMENT, filename="sintering.md", collection="default"):
    response = call(test_client, "ingest", collection=collection, filename=filename,
                    content_base64=base64.b64encode(text.encode()).decode(),
                    media_type="text/markdown")
    assert response.status_code == 200, response.text
    source_id = response.json()["source"]["id"]
    processed = call(test_client, "process", collection=collection, source_ids=[source_id])
    assert processed.status_code == 200, processed.text
    job = store.open().jobs.wait(processed.json()["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return source_id


def test_health_is_open_but_everything_else_needs_the_token(store):
    with TestClient(create_app(store, token=TOKEN)) as anonymous:
        assert anonymous.get("/v1/health").json()["status"] == "ok"
        assert anonymous.post("/v1/collections/default/overview", json={}).status_code == 401
        assert anonymous.get("/v1/tools").status_code == 401

        anonymous.headers["authorization"] = "Bearer wrong"
        assert anonymous.post("/v1/collections/default/overview", json={}).status_code == 401


def test_the_manifest_lists_the_agent_operations_only(client):
    tools = {tool["name"]: tool for tool in client.get("/v1/tools").json()["tools"]}
    names = set(tools)
    assert "knowledge_overview" in names
    assert {"knowledge_process", "knowledge_schema_manage", "knowledge_draft_open",
            "knowledge_experiment_bundle_prepare", "knowledge_graph_index_build"} <= names
    assert tools["knowledge_schemas"]["input_schema"]["properties"]["operation"]["enum"] == ["list", "get"]
    # Raw-byte upload, configuring and reviewing stay human acts on every transport.
    assert not {name for name in names if name.endswith(("_ingest", "_settings", "_review"))}


def test_mcp_enforces_the_read_contract_even_without_an_actor_label(store):
    from oaw_knowledge_base.service import mcp

    create = {"operation": "create", "name": "Sample", "definition": SCHEMA, "system_prompt": "Read samples."}
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_schemas", create, actor=None)
    created = json.loads(mcp.call(store, "knowledge_schema_manage", create))["schema"]
    assert json.loads(mcp.call(store, "knowledge_schemas", {"operation": "get", "schema_id": created["id"]}))["schema"] == created
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_graph_index", {"operation": "build"}, actor=None)
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_process", {})


def test_an_unknown_operation_is_a_404_not_a_crash(client):
    assert call(client, "drop_everything").status_code == 404


def test_a_bad_argument_is_reported_as_a_422(client):
    response = call(client, "ingest", filename="../escape.md", content_base64="aGk=")
    assert response.status_code == 422
    assert response.json()["detail"]


def test_mkb_refusals_keep_their_own_status_and_message(client):
    body = {"operation": "create", "name": "Process graph", "definition": SCHEMA,
            "system_prompt": "Extract entities."}
    assert call(client, "schemas", **body).status_code == 200
    clash = call(client, "schemas", **body)
    assert clash.status_code == 409  # a conflict, not a 500 with a stack trace
    assert "Process graph" in clash.json()["detail"]

    missing = call(client, "schemas", operation="get",
                   schema_id="00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_settings_cannot_move_the_caller_to_another_collection(client):
    # The collection comes from the path; a settings write must not override it.
    response = call(client, "settings", collection="alloys", collection_name="elsewhere")
    assert response.status_code == 200
    assert response.json()["settings"]["collection_name"] == "alloys"
    assert call(client, "overview", collection="alloys").json()["collection"]["name"] == "alloys"
    assert "elsewhere" not in names(client)


def test_the_loop_runs_over_http_and_only_an_admin_publishes(client, admin, store):
    source_id = ingest(client, store)
    listing = call(client, "sources").json()["sources"]
    assert listing[0]["markdown"]["engine"] == "text"

    draft = call(client, "knowledge_draft_manage", operation="open",
                 source_ids=[source_id]).json()["draft"]
    prompt = call(client, "knowledge_extract_prompt", source_id=source_id,
                  draft_id=draft["id"]).json()
    assert "Sintering" in prompt["markdown"]
    saved = call(client, "knowledge_save", draft_id=draft["id"], source_id=source_id,
                 entities=[{"type": "entity", "name": "Si3N4"}]).json()
    assert saved["added"] == {"entities": 1, "relations": 0}

    # The service token reaches everything except the publish.
    refused = call(client, "knowledge_review", operation="publish", draft_id=draft["id"])
    assert refused.status_code == 403
    assert "admin token" in refused.json()["detail"]
    assert call(client, "graph").json()["entities"] == []

    # Even the admin has to confirm: the first call is a deliberate speed bump.
    pending = call(admin, "knowledge_review", operation="publish", draft_id=draft["id"])
    assert pending.status_code == 409
    assert pending.json()["status"] == "confirmation_required"
    assert call(client, "graph").json()["entities"] == []

    published = admin.post("/v1/collections/default/knowledge_review?confirm=true",
                           json={"operation": "publish", "draft_id": draft["id"]})
    assert published.status_code == 200, published.text
    assert published.json()["draft"]["status"] == "published"
    assert [item["name"] for item in call(client, "graph").json()["entities"]] == ["Si3N4"]


def test_confirm_is_ignored_without_the_admin_token(client, store):
    # ``?confirm=true`` must not become a way around the publish gate.
    response = client.post("/v1/collections/default/knowledge_review?confirm=true",
                           json={"operation": "publish",
                                 "draft_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 403


def test_rewriting_the_published_graph_needs_the_admin_token_too(client, store):
    response = client.post("/v1/collections/default/knowledge_graph_audit_apply?confirm=true",
                           json={"operations": [{"op": "flag", "message": "x"}]})
    assert response.status_code == 403


def test_collections_stay_separate_in_one_store(client, store):
    ingest(client, store, filename="a.md")
    response = client.post("/v1/collections/other/ingest",
                           json={"filename": "b.md",
                                 "content_base64": base64.b64encode(b"# B\n\ntext").decode(),
                                 "media_type": "text/markdown"})
    assert response.status_code == 200
    processed = client.post("/v1/collections/other/process",
                            json={"source_ids": [response.json()["source"]["id"]]})
    store.open().jobs.wait(processed.json()["jobs"][0]["job"]["id"], timeout=60)

    assert [item["filename"] for item in call(client, "sources").json()["sources"]] == ["a.md"]
    assert [item["filename"] for item in
            call(client, "sources", collection="other").json()["sources"]] == ["b.md"]
    assert names(client) >= {"default", "other"}


def test_insights_publish_over_http_only_with_the_admin_token(client, admin, store):
    tools = {tool["name"] for tool in client.get("/v1/tools").json()["tools"]}
    assert {"knowledge_insights", "knowledge_insight_prompt", "knowledge_insight_save",
            "knowledge_insight_update"} <= tools
    assert "knowledge_insight_review" not in tools

    schema = call(client, "schemas", operation="create", name="Batch", kind="experiment",
                  definition={"type": "object", "properties": {"m": {"type": "number"}}},
                  system_prompt="x").json()["schema"]
    bundle = call(client, "experiment_bundle_manage", operation="create",
                  schema_id=schema["id"], name="July").json()["bundle"]
    uploaded = call(client, "experiment_ingest", bundle_id=bundle["id"], filename="log.md",
                    content_base64=base64.b64encode(b"log").decode(), media_type="text/markdown")
    assert uploaded.status_code == 200, uploaded.text
    job = store.open().jobs.wait(uploaded.json()["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    records = call(client, "experiment_save", bundle_id=bundle["id"],
                   records=[{"name": "B1", "data": {"m": 1}}]).json()["records"]
    published = client.post("/v1/collections/default/experiment_review?confirm=true",
                            json={"operation": "publish", "record_id": records[0]["id"]})
    # ``confirm`` only counts with the admin token: a plain token gets the speed bump.
    assert published.status_code == 409
    published = admin.post("/v1/collections/default/experiment_review?confirm=true",
                           json={"operation": "publish", "record_id": records[0]["id"]})
    assert published.status_code == 200, published.text

    prompt = call(client, "insight_prompt", schema_id=schema["id"])
    assert prompt.status_code == 200 and prompt.json()["record_count"] == 1
    saved = call(client, "insight_save", schema_id=schema["id"], insights=[
        {"kind": "note", "title": "one", "statement": "s", "evidence": [{"record_id": records[0]["id"]}]}])
    assert saved.status_code == 200, saved.text
    insight_id = saved.json()["insights"][0]["id"]

    refused = call(client, "insight_review", operation="publish", insight_id=insight_id)
    assert refused.status_code == 403 and "admin token" in refused.json()["detail"]
    pending = call(admin, "insight_review", operation="publish", insight_id=insight_id)
    assert pending.status_code == 409 and pending.json()["status"] == "confirmation_required"
    done = admin.post("/v1/collections/default/insight_review?confirm=true",
                      json={"operation": "publish", "insight_id": insight_id})
    assert done.status_code == 200, done.text
    assert done.json()["insights"][0]["status"] == "published"
    layer = call(client, "graph", layer="experiment").json()
    assert {e["type"] for e in layer["entities"]} == {"note", "experiment_record"}
    # Unpublishing and deleting are not graph writes; a plain token may.
    assert call(client, "insight_review", operation="unpublish", insight_id=insight_id).status_code == 200
    assert call(client, "graph", layer="experiment").json()["entities"] == []
