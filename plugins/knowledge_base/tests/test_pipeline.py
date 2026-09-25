"""End-to-end pipeline: upload, convert, project, review, graph — with no host at all.

The suite drives the same handlers the OAW card and the service call, through the
plain :class:`~oaw_knowledge_base.context.KnowledgeContext`, so it runs in any venv
that has ``mat-know-base`` installed. ``cd plugins/knowledge_base && pytest``.
"""
import base64
import json

import pytest

pytest.importorskip("mkb", reason="Install mat-know-base into this environment")
pytest.importorskip("sqlalchemy")

from oaw_knowledge_base import actions, client  # noqa: E402
from oaw_knowledge_base.context import KnowledgeContext, MemorySettings  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402

SCHEMA = {
    "type": "object",
    "required": ["entities"],
    "properties": {
        "entities": {"type": "array", "items": {"type": "object"}},
        "relations": {"type": "array", "items": {"type": "object"}},
    },
}
DOCUMENT = """# Sintering of Si3N4

## Method
The Si3N4 powder was sintered at 1750 C for 2 hours under nitrogen.

| Sample | Density |
|---|---|
| A1 | 3.21 |
"""


@pytest.fixture
def card(tmp_path):
    storage = tmp_path / "node-1"
    storage.mkdir()
    yield KnowledgeContext("node-1", storage, state=MemorySettings())
    client.close_client("node-1")


def run(context, handler, **arguments):
    return handler(context, arguments)


def ingest_document(context, text=DOCUMENT, filename="sintering.md"):
    result = run(context, actions.ingest, filename=filename,
                 content_base64=base64.b64encode(text.encode()).decode(),
                 media_type="text/markdown")
    kb = client.open_client(context.node_id, context.storage_path)
    job = kb.jobs.wait(result["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return result["source"]["id"], job


def test_ingest_converts_to_markdown_with_evidence(card):
    source_id, _ = ingest_document(card)

    listing = run(card, actions.sources)["sources"]
    assert len(listing) == 1
    assert listing[0]["markdown"]["engine"] == "text"
    record_id = listing[0]["record_id"]
    assert record_id

    document = run(card, actions.markdown, source_id=source_id)
    assert "Sintering of Si3N4" in document["markdown"]
    assert document["has_more"] is False

    # The markdown record must point back at the uploaded file (traceability).
    kb = client.open_client(card.node_id, card.storage_path)
    links = kb.evidence.list(output_id=record_id)
    assert [str(link.source_id) for link in links] == [source_id]


def test_conversion_job_is_idempotent(card):
    source_id, first = ingest_document(card)
    kb = client.open_client(card.node_id, card.storage_path)
    source = kb.sources.require(source_id)
    from oaw_knowledge_base.pipelines import submit_markdown_job

    second = submit_markdown_job(kb, source, kb.collections.list()[0].id, engine="auto")
    assert str(second.id) == str(first.id)
    assert len([a for a in kb.artifacts.list(source_id=source_id)
                if a.processing_type == "MARKDOWN"]) == 1


def test_projection_requires_a_schema_shaped_definition(card):
    with pytest.raises(KnowledgeError):
        run(card, actions.schemas, operation="create", name="bad",
            definition={"type": "string"}, system_prompt="x")


def test_full_loop_publishes_only_approved_facts(card):
    source_id, _ = ingest_document(card)
    record_id = run(card, actions.sources)["sources"][0]["record_id"]

    schema = run(card, actions.schemas, operation="create", name="Process graph",
                 definition=SCHEMA, system_prompt="Extract entities and relations.",
                 description="Samples and the processes applied to them")["schema"]

    prompt = run(card, actions.projection_prompt, schema_id=schema["id"], source_id=source_id)
    assert prompt["system_prompt"] == "Extract entities and relations."
    assert "Si3N4" in prompt["markdown"]
    assert prompt["record_id"] == record_id
    assert prompt["artifact_id"]

    extracted = {
        "entities": [
            {"type": "material", "name": "Si3N4", "form": "powder"},
            {"type": "process", "name": "Sintering", "temperature_c": 1750},
        ],
        "relations": [{"source": "Si3N4", "target": "Sintering", "type": "processed_by"}],
    }
    saved = run(card, actions.save_projection, schema_id=schema["id"], record_id=record_id,
                data=extracted, model="test-model")["projection"]
    assert saved["validation"]["valid"] is True

    stored = run(card, actions.projections, projection_id=saved["id"])["projection"]
    assert stored["data"] == extracted
    assert stored["evidence"] and stored["evidence"][0]["source_id"] == source_id

    # Saving a projection copies the record's evidence, so the source listing must still
    # resolve the record itself rather than the projection that cites the same artifact.
    assert run(card, actions.sources)["sources"][0]["record_id"] == record_id

    built = run(card, actions.draft, operation="create", projection_ids=[saved["id"]])["draft"]
    assert built["entities"] == 2 and built["relations"] == 1

    # An unapproved draft must not reach the graph.
    assert run(card, actions.graph)["entities"] == []

    run(card, actions.review, operation="submit", draft_id=built["id"], expected_revision=1)
    pending = run(card, actions.review, operation="approve", draft_id=built["id"],
                  expected_revision=1)
    assert pending["status"] == "confirmation_required"
    assert run(card, actions.graph)["entities"] == []

    confirmed = KnowledgeContext(card.node_id, card.storage_path, state=card.state,
                                 confirmed=True)
    published = run(confirmed, actions.review, operation="approve", draft_id=built["id"],
                    expected_revision=1)
    assert published["decision"] == "APPROVED"
    assert published["event"]["type"] == "fact.revision.published"
    assert published["graph"] == {"entities": 2, "relations": 1}

    result = run(card, actions.graph)
    assert sorted(item["name"] for item in result["entities"]) == ["Si3N4", "Sintering"]
    assert result["relations"][0]["type"] == "processed_by"

    material = next(item for item in result["entities"] if item["name"] == "Si3N4")
    assert material["properties"]["form"] == "powder"
    neighbours = run(card, actions.graph, operation="traverse", entity_id=material["id"])
    assert {item["name"] for item in neighbours["entities"]} == {"Si3N4", "Sintering"}

    summary = run(card, actions.overview)
    assert summary["counts"]["facts"] == 1
    assert summary["counts"]["entities"] == 2
    assert summary["counts"]["pending_review"] == 0


def test_graph_survives_a_client_restart(card):
    kb = client.open_client(card.node_id, card.storage_path)
    group = client.collection(kb)
    kb.graph.extract({"entities": [{"type": "material", "name": "Cu"}]},
                     extractor=lambda payload: payload)
    assert len(kb.graph.query().entities) == 1

    client.close_client(card.node_id)
    reopened = client.open_client(card.node_id, card.storage_path)
    assert [entity.name for entity in reopened.graph.query().entities] == ["Cu"]
    assert str(reopened.collections.list()[0].id) == str(group.id)


def test_relations_need_both_endpoints(card):
    import uuid

    from mkb.exceptions import ConflictError

    kb = client.open_client(card.node_id, card.storage_path)
    entity = kb.graph.upsert_entity(type="material", name="Fe")

    with pytest.raises(ConflictError):
        kb.oaw_graph_store.upsert_relation(
            type("R", (), {"id": uuid.uuid4(), "source_id": entity.id,
                           "target_id": uuid.uuid4(), "type": "x", "properties": {},
                           "created_at": entity.created_at, "updated_at": entity.updated_at})())


def test_settings_drive_the_collection_and_engine(card):
    updated = run(card, actions.update_settings, collection_name="Alloys", pdf_engine="text")
    assert updated["settings"]["collection_name"] == "Alloys"

    summary = run(card, actions.overview)
    assert summary["collection"]["name"] == "Alloys"
    assert summary["settings"]["pdf_engine"] == "text"
    assert "text" in summary["engines"]


def test_uploads_are_bounded_and_validated(card):
    # Handlers raise KnowledgeError for their own rules and pydantic's ValidationError
    # for the request shape; both are ValueErrors, which is what every caller sees.
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="../escape.md", content_base64="aGk=")
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="x.md", content_base64="not base64!!")
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="empty.md", content_base64="")
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="x.md", content_base64="aGk=", sql="DROP")


def test_an_unconvertible_upload_fails_in_the_job_not_the_action(card):
    # Ingest must stay fast and never convert: the mutation lock is held for its duration.
    submitted = run(card, actions.ingest, filename="scan.bin",
                    content_base64=base64.b64encode(b"\x00\x01\x02").decode(),
                    media_type="application/octet-stream")
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(submitted["job"]["id"], timeout=60)
    assert job.status == "FAILED"
    assert "markdown engine" in (job.error or "")

    listing = run(card, actions.sources)["sources"]
    assert listing[0]["markdown"] is None
    with pytest.raises(KnowledgeError):
        run(card, actions.markdown, source_id=submitted["source"]["id"])


def test_jobs_report_progress_events(card):
    source_id, job = ingest_document(card)
    detail = run(card, actions.jobs, job_id=str(job.id))
    assert detail["job"]["status"] == "COMPLETED"
    assert detail["job"]["source_id"] == source_id
    assert any("markdown" in (event["message"] or "").lower() for event in detail["events"])
    assert json.dumps(detail)  # the payload must stay JSON serializable
