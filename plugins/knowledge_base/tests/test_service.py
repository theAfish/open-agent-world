"""The service boundaries: the token, the admin gate, and project boundaries.

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


def call(test_client, action, **arguments):
    return test_client.post(f"/v1/{action}", json=arguments)


def ingest(test_client, store, text=DOCUMENT, filename="sintering.md"):
    response = call(test_client, "project_import_preview", files=[{
        "filename": filename, "content_base64": base64.b64encode(text.encode()).decode(),
        "media_type": "text/markdown"}], grouping="files")
    assert response.status_code == 200, response.text
    imported = call(test_client, "project_import_commit", token=response.json()["token"], grouping="files")
    assert imported.status_code == 200, imported.text
    result = imported.json()
    job = store.open().jobs.wait(result["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return result["projects"][0]["id"], result["source_ids"][0]


def test_health_is_open_but_everything_else_needs_the_token(store):
    with TestClient(create_app(store, token=TOKEN)) as anonymous:
        assert anonymous.get("/v1/health").json()["status"] == "ok"
        assert anonymous.post("/v1/overview", json={}).status_code == 401
        assert anonymous.get("/v1/tools").status_code == 401
        assert anonymous.get("/v1/projects").status_code == 401
        anonymous.headers["authorization"] = "Bearer wrong"
        assert anonymous.post("/v1/overview", json={}).status_code == 401


def test_the_manifest_lists_the_agent_operations_only(client):
    tools = {tool["name"]: tool for tool in client.get("/v1/tools").json()["tools"]}
    assert {"knowledge_overview", "knowledge_projects", "knowledge_project_process", "knowledge_spaces",
            "knowledge_space_manage", "knowledge_projection_prepare", "knowledge_projection_save",
            "knowledge_experiment_bundle_prepare", "knowledge_graph_index_build"} <= tools.keys()
    assert tools["knowledge_schemas"]["input_schema"]["properties"]["operation"]["enum"] == ["list", "get"]
    assert tools["knowledge_spaces"]["input_schema"]["properties"]["operation"]["enum"] == ["list", "get"]
    assert not {name for name in tools if name.endswith(("_ingest", "_settings", "_review"))}
    assert "knowledge_draft_open" not in tools
    assert "knowledge_project_import_preview" not in tools  # raw bytes stay off the agent surface


def test_mcp_enforces_the_read_contract_even_without_an_actor_label(store):
    from oaw_knowledge_base.service import mcp

    create = {"operation": "create", "name": "Sample", "definition": SCHEMA, "system_prompt": "Read samples."}
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_spaces", create, actor=None)
    created = json.loads(mcp.call(store, "knowledge_space_manage", create))["space"]
    assert json.loads(mcp.call(store, "knowledge_spaces", {"operation": "get", "space_id": created["id"]}))["space"] == created
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_graph_index", {"operation": "build"}, actor=None)
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_project_process", {})
    with pytest.raises(ValueError):
        mcp.call(store, "knowledge_project_import_preview", {})


def test_http_schema_read_action_cannot_write_without_actor_label(client):
    response = call(client, "schemas", operation="create", name="Forbidden",
                    definition=SCHEMA, system_prompt="Read samples.")
    assert response.status_code == 422
    assert "read-only" in response.json()["detail"]
    assert call(client, "schemas").json()["schemas"] == []


def test_an_unknown_or_retired_operation_is_a_404_not_a_crash(client):
    for operation in ("drop_everything", "knowledge_draft_manage", "record_extract_prompt"):
        assert call(client, operation).status_code == 404
    assert client.post("/v1/collections/default/overview", json={}).status_code == 404


def test_a_bad_argument_is_reported_as_a_422(client):
    response = call(client, "project_import_preview", files=[{"filename": "../escape.md", "content_base64": "aGk="}])
    assert response.status_code == 422
    assert response.json()["detail"]


def test_mkb_refusals_keep_their_own_status_and_message(client):
    body = {"operation": "create", "name": "Process graph", "definition": SCHEMA,
            "system_prompt": "Extract entities."}
    assert call(client, "space_manage", **body).status_code == 200
    clash = call(client, "space_manage", **body)
    assert clash.status_code == 409
    assert "Process graph" in clash.json()["detail"]
    missing = call(client, "spaces", operation="get", space_id="00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_settings_and_reads_do_not_create_a_phantom_project(client):
    response = call(client, "settings", pdf_engine="text")
    assert response.status_code == 200
    assert response.json()["settings"]["pdf_engine"] == "text"
    assert call(client, "overview").status_code == 200
    assert client.get("/v1/projects").json()["projects"] == []


def test_direct_markdown_projection_is_usable_without_review_and_keeps_revisions(client, store):
    project_id, source_id = ingest(client, store)
    project = call(client, "projects", project_id=project_id).json()["project"]
    assert project["files"][0]["markdown"]["engine"] == "text"
    space = call(client, "space_manage", operation="create", name="Temperature",
                 definition={"type": "object", "required": ["temperature"], "properties": {"temperature": {"type": "number"}}},
                 system_prompt="Read the stated temperature.").json()["space"]
    prepared = call(client, "projection_prepare", project_ids=[project_id], space_id=space["id"])
    projection = prepared.json()["projections"][0]
    prompt = call(client, "projection_prompt", projection_id=projection["id"])
    assert "Sintering" in prompt.json()["markdown"]
    assert prompt.json()["sources"][0]["source_id"] == source_id
    saved = call(client, "projection_save", projection_id=projection["id"], data={"temperature": 1750})
    assert saved.status_code == 200, saved.text
    current = saved.json()["projection"]
    assert current["status"] == "completed"
    assert call(client, "projections", operation="get", projection_id=current["id"]).json()["projection"]["data"] == {"temperature": 1750}
    again = call(client, "projection_prepare", project_ids=[project_id], space_id=space["id"]).json()["projections"][0]
    assert again["id"] == current["id"] and again["disposition"] == "reused"
    revised = call(client, "projection_revise", projection_id=current["id"], expected_revision=current["revision"],
                   data={"temperature": 1750}, note="Verified against the report")
    assert revised.status_code == 200, revised.text
    assert revised.json()["projection"]["parent_id"] == current["id"]
    assert len(call(client, "projections", project_id=project_id, include_history=True).json()["projections"]) == 2


def test_confirm_cannot_bypass_publication_authority(client):
    for operation, key in (("experiment_review", "record_id"), ("insight_review", "insight_id")):
        response = client.post(f"/v1/{operation}?confirm=true", json={"operation": "publish", key: "00000000-0000-0000-0000-000000000000"})
        assert response.status_code == 403
        assert "admin token" in response.json()["detail"]


def test_projects_keep_their_files_separate_in_one_store(client, store):
    a, _ = ingest(client, store, filename="a.md")
    b, _ = ingest(client, store, filename="b.md")
    assert [file["filename"] for file in call(client, "projects", project_id=a).json()["project"]["files"]] == ["a.md"]
    assert [file["filename"] for file in call(client, "projects", project_id=b).json()["project"]["files"]] == ["b.md"]
    assert len(client.get("/v1/projects").json()["projects"]) == 2


def test_insights_publish_over_http_only_with_the_admin_token(client, admin, store):
    tools = {tool["name"] for tool in client.get("/v1/tools").json()["tools"]}
    assert {"knowledge_insights", "knowledge_insight_prompt", "knowledge_insight_save",
            "knowledge_insight_update"} <= tools
    assert "knowledge_insight_review" not in tools

    schema = call(client, "schema_manage", operation="create", name="Batch", kind="experiment",
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
    published = client.post("/v1/experiment_review?confirm=true",
                            json={"operation": "publish", "record_id": records[0]["id"]})
    # A caller cannot bypass publication authority with confirm=true.
    assert published.status_code == 403
    published = admin.post("/v1/experiment_review?confirm=true",
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
    done = admin.post("/v1/insight_review?confirm=true",
                      json={"operation": "publish", "insight_id": insight_id})
    assert done.status_code == 200, done.text
    assert done.json()["insights"][0]["status"] == "published"
    layer = call(client, "graph", layer="experiment").json()
    assert {e["type"] for e in layer["entities"]} == {"note", "experiment_record"}
    # Unpublishing and deleting are not graph writes; a plain token may.
    assert call(client, "insight_review", operation="unpublish", insight_id=insight_id).status_code == 200
    assert call(client, "graph", layer="experiment").json()["entities"] == []
