"""The service boundaries: the token, the admin gate, and the collection pin.

The routing itself is generic — one handler over the operations table — so these
tests are about what the HTTP layer adds on top of the pipeline rather than about
the pipeline, which ``test_pipeline.py`` already covers end to end.
"""
import base64

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


def ingest(test_client, store, text=DOCUMENT, filename="sintering.md"):
    response = call(test_client, "ingest", filename=filename,
                    content_base64=base64.b64encode(text.encode()).decode(),
                    media_type="text/markdown")
    assert response.status_code == 200, response.text
    job = store.open().jobs.wait(response.json()["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return response.json()["source"]["id"]


def test_health_is_open_but_everything_else_needs_the_token(store):
    with TestClient(create_app(store, token=TOKEN)) as anonymous:
        assert anonymous.get("/v1/health").json()["status"] == "ok"
        assert anonymous.post("/v1/collections/default/overview", json={}).status_code == 401
        assert anonymous.get("/v1/tools").status_code == 401

        anonymous.headers["authorization"] = "Bearer wrong"
        assert anonymous.post("/v1/collections/default/overview", json={}).status_code == 401


def test_the_manifest_lists_the_agent_operations_only(client):
    names = {tool["name"] for tool in client.get("/v1/tools").json()["tools"]}
    assert "knowledge_overview" in names
    # Uploading, configuring and reviewing stay human acts on every transport.
    assert not {name for name in names if name.endswith(("_ingest", "_settings", "_review"))}


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

    missing = call(client, "projections",
                   projection_id="00000000-0000-0000-0000-000000000000")
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

    schema = call(client, "schemas", operation="create", name="Process graph",
                  definition=SCHEMA, system_prompt="Extract entities.").json()["schema"]
    prompt = call(client, "projection_prompt", schema_id=schema["id"],
                  source_id=source_id).json()
    assert "Sintering" in prompt["markdown"]

    saved = call(client, "save_projection", schema_id=schema["id"],
                 record_id=prompt["record_id"], model="test-model",
                 data={"entities": [{"type": "material", "name": "Si3N4"}],
                       "relations": []}).json()["projection"]
    built = call(client, "draft", operation="create",
                 projection_ids=[saved["id"]]).json()["draft"]
    assert call(client, "review", operation="submit", draft_id=built["id"],
                expected_revision=1).status_code == 200

    # The service token reaches everything except the publish.
    refused = call(client, "review", operation="approve", draft_id=built["id"],
                   expected_revision=1)
    assert refused.status_code == 403
    assert "admin token" in refused.json()["detail"]
    assert call(client, "graph").json()["entities"] == []

    # Even the admin has to confirm: the first call is a deliberate speed bump.
    pending = call(admin, "review", operation="approve", draft_id=built["id"],
                   expected_revision=1)
    assert pending.status_code == 409
    assert pending.json()["status"] == "confirmation_required"
    assert call(client, "graph").json()["entities"] == []

    published = admin.post("/v1/collections/default/review?confirm=true",
                           json={"operation": "approve", "draft_id": built["id"],
                                 "expected_revision": 1})
    assert published.status_code == 200, published.text
    assert published.json()["decision"] == "APPROVED"
    assert [item["name"] for item in call(client, "graph").json()["entities"]] == ["Si3N4"]


def test_confirm_is_ignored_without_the_admin_token(client, store):
    # ``?confirm=true`` must not become a way around the approval gate.
    response = client.post("/v1/collections/default/review?confirm=true",
                           json={"operation": "approve",
                                 "draft_id": "00000000-0000-0000-0000-000000000000",
                                 "expected_revision": 1})
    assert response.status_code == 403


def test_collections_stay_separate_in_one_store(client, store):
    ingest(client, store, filename="a.md")
    response = client.post("/v1/collections/other/ingest",
                           json={"filename": "b.md",
                                 "content_base64": base64.b64encode(b"# B\n\ntext").decode(),
                                 "media_type": "text/markdown"})
    assert response.status_code == 200
    store.open().jobs.wait(response.json()["job"]["id"], timeout=60)

    assert [item["filename"] for item in call(client, "sources").json()["sources"]] == ["a.md"]
    assert [item["filename"] for item in
            call(client, "sources", collection="other").json()["sources"]] == ["b.md"]
    assert names(client) >= {"default", "other"}
