"""End-to-end pipeline: upload, convert, project, review, graph — with no host at all.

The suite drives the same handlers the OAW card and the service call, through the
plain :class:`~oaw_knowledge_base.context.KnowledgeContext`, so it runs in any venv
that has ``mat-know-base`` installed. ``cd plugins/knowledge_base && pytest``.
"""
import base64
import json
import uuid

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
    ingested = run(context, actions.ingest, filename=filename,
                   content_base64=base64.b64encode(text.encode()).decode(),
                   media_type="text/markdown")
    source_id = ingested["source"]["id"]
    processed = run(context, actions.process_sources, source_ids=[source_id])
    kb = client.open_client(context.node_id, context.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return source_id, job


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


def test_mineru_job_resolves_a_private_token_without_persisting_it(card, monkeypatch):
    from oaw_knowledge_base import markdown

    seen = []
    card.resolve_secret = lambda reference: "private-job-token" if reference == "OAW_MINERU_TOKEN" else None
    def convert(data, filename, base_url, **kwargs):
        seen.append(kwargs["token"])
        return "# Converted paper"
    monkeypatch.setattr(markdown, "_mineru_markdown", convert)
    run(card, actions.update_settings, pdf_engine="mineru", mineru_base_url="https://mineru.example")
    source = run(card, actions.ingest, filename="paper.pdf", media_type="application/pdf",
                 content_base64=base64.b64encode(b"%PDF-test").decode())["source"]
    result = run(card, actions.process_sources, source_ids=[source["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(result["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    assert seen == ["private-job-token"]
    assert "private-job-token" not in json.dumps(card.state.get())
    from sqlalchemy import text
    with kb.oaw_engine.connect() as connection:
        tables = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        for (table,) in list(tables):
            escaped = table.replace('"', '""')
            rows = connection.execute(text(f'SELECT * FROM "{escaped}"')).fetchall()
            assert "private-job-token" not in str(rows), table


def test_conversion_also_indexes_the_document_for_search(card):
    source_id, _ = ingest_document(card)
    kb = client.open_client(card.node_id, card.storage_path)

    from oaw_knowledge_base import search_store

    hits = search_store.search(kb.oaw_engine, "sintered")
    assert hits and hits[0]["source_id"] == source_id
    assert hits[0]["group_id"] == str(kb.collections.list()[0].id)


def test_reopening_the_client_backfills_records_indexed_before_search_existed(card):
    source_id, _ = ingest_document(card)
    kb = client.open_client(card.node_id, card.storage_path)

    from oaw_knowledge_base import search_store

    # Simulate a database from before the search feature existed: its chunks are
    # gone, as if this record's conversion predated search_store entirely.
    with kb.oaw_engine.begin() as connection:
        connection.exec_driver_sql(f"DELETE FROM {search_store.TABLE}")
    assert search_store.search(kb.oaw_engine, "sintered") == []

    client.close_client(card.node_id)
    reopened = client.open_client(card.node_id, card.storage_path)
    hits = search_store.search(reopened.oaw_engine, "sintered")
    assert hits and hits[0]["source_id"] == source_id


def test_search_action_returns_attributable_excerpts_scoped_to_a_group(card):
    source_id, _ = ingest_document(card, filename="sintering.md")
    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]
    ingested = run(card, actions.ingest, filename="alloy.md",
                   content_base64=base64.b64encode(
                       b"# Alloy notes\n\nA different microstructure entirely.\n").decode(),
                   media_type="text/markdown", group_id=alloys["id"])
    processed = run(card, actions.process_sources, source_ids=[ingested["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error

    found = run(card, actions.search, query="sintered")["results"]
    assert found and found[0]["source_id"] == source_id
    assert found[0]["filename"] == "sintering.md"
    assert found[0]["group_name"] == "Knowledge base"
    assert "Method" in (found[0]["heading_path"] or "")

    # Scoped to a group with nothing matching: no results, even though the term
    # exists elsewhere in the base.
    assert run(card, actions.search, query="sintered", group_id=alloys["id"])["results"] == []

    # A no-op query (all whitespace or nothing meaningful) returns cleanly, not an error.
    assert run(card, actions.search, query="   ")["results"] == []


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


def test_schemas_default_to_literature_and_can_be_tagged_and_filtered(card):
    literature = run(card, actions.schemas, operation="create", name="Process graph",
                     definition=SCHEMA, system_prompt="x")["schema"]
    assert literature["kind"] == "literature"

    experiment = run(card, actions.schemas, operation="create", name="Synthesis run",
                     definition=SCHEMA, system_prompt="x", kind="experiment")["schema"]
    assert experiment["kind"] == "experiment"

    # A schema created before "kind" existed carries MKB's own "freeform" purpose;
    # that must still read back as literature, with no migration needed.
    kb = client.open_client(card.node_id, card.storage_path)
    legacy = kb.schemas.create(name="Legacy", domain="materials", definition=SCHEMA,
                               system_prompt="x")
    assert legacy.purpose == "freeform"
    legacy_json = run(card, actions.schemas, operation="get", schema_id=str(legacy.id))["schema"]
    assert legacy_json["kind"] == "literature"

    all_names = {item["name"] for item in run(card, actions.schemas)["schemas"]}
    assert all_names == {"Process graph", "Synthesis run", "Legacy"}
    experiment_only = run(card, actions.schemas, kind="experiment")["schemas"]
    assert [item["name"] for item in experiment_only] == ["Synthesis run"]

    retagged = run(card, actions.schemas, operation="update", schema_id=experiment["id"],
                   kind="literature")["schema"]
    assert retagged["kind"] == "literature"


def test_graph_schema_is_one_stored_pointer_restricted_to_literature_schemas(card):
    # Unset until someone chooses one; "get" alone never fails.
    assert run(card, actions.graph_schema)["schema_id"] == ""
    assert run(card, actions.overview)["graph_schema_id"] is None

    literature = run(card, actions.schemas, operation="create", name="Process graph",
                     definition=SCHEMA, system_prompt="x")["schema"]
    experiment = run(card, actions.schemas, operation="create", name="Synthesis run",
                     definition=SCHEMA, system_prompt="x", kind="experiment")["schema"]

    with pytest.raises(KnowledgeError):
        run(card, actions.graph_schema, operation="set", schema_id=experiment["id"])

    set_result = run(card, actions.graph_schema, operation="set", schema_id=literature["id"])
    assert set_result["schema_id"] == literature["id"]
    assert run(card, actions.graph_schema)["schema_id"] == literature["id"]
    assert run(card, actions.overview)["graph_schema_id"] == literature["id"]

    # Setting it again to a blank id clears it.
    cleared = run(card, actions.graph_schema, operation="set", schema_id="")
    assert cleared["schema_id"] == ""
    assert run(card, actions.overview)["graph_schema_id"] is None


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


def test_graph_elements_default_to_the_literature_layer_and_can_be_retagged(card):
    kb = client.open_client(card.node_id, card.storage_path)
    store = kb.oaw_graph_store
    facts = kb.graph.extract({"entities": [{"type": "material", "name": "Cu"}]},
                             extractor=lambda payload: payload)
    insight = kb.graph.extract({
        "entities": [{"type": "anomaly", "name": "B7 high", "id": "insight:1"},
                     {"type": "experiment_record", "name": "B7", "id": "record:b7"},
                     {"type": "field", "name": "moisture", "id": "field:moisture"}],
        "relations": [{"source": "insight:1", "target": "record:b7", "type": "observed_in"},
                      {"source": "insight:1", "target": "field:moisture", "type": "involves"}],
    }, extractor=lambda payload: payload)
    store.tag([e.id for e in insight.entities], [r.id for r in insight.relations], layer="experiment")

    assert store.counts(layer="literature") == {"entities": 1, "relations": 0}
    assert store.counts(layer="experiment") == {"entities": 3, "relations": 2}
    assert store.counts() == {"entities": 4, "relations": 2}
    assert [e.name for e in store.list_entities(layer="literature")] == ["Cu"]
    with pytest.raises(ValueError):
        store.list_entities(layer="rumour")

    literature = run(card, actions.graph, layer="literature")
    assert [e["name"] for e in literature["entities"]] == ["Cu"]
    experiment = run(card, actions.graph, layer="experiment", entity_type="anomaly")
    assert [e["name"] for e in experiment["entities"]] == ["B7 high"]
    assert {r["type"] for r in experiment["relations"]} == set()  # endpoints filtered by type
    everything = run(card, actions.graph)
    assert len(everything["entities"]) == 4

    # Unpublishing: the insight goes, its relations go, and the reference nodes it
    # alone cited are orphans to prune — while the literature fact is untouched.
    anomaly = next(e for e in insight.entities if e.type == "anomaly")
    store.remove([anomaly.id])
    orphans = store.orphans(layer="experiment", types=("experiment_record", "field"))
    assert len(orphans) == 2
    store.remove(orphans)
    assert store.counts() == {"entities": 1, "relations": 0}
    assert facts.entities[0].name == "Cu" and store.get_entity(facts.entities[0].id) is not None


def test_a_graph_from_before_layers_existed_is_migrated_on_open(tmp_path):
    from sqlalchemy import create_engine, text

    from oaw_knowledge_base.graph_store import SqlGraphStore

    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE oaw_kg_entity (id VARCHAR(36) PRIMARY KEY, type VARCHAR(255) NOT NULL, "
            "name VARCHAR(1024) NOT NULL, properties TEXT NOT NULL, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"))
        connection.execute(text(
            "CREATE TABLE oaw_kg_relation (id VARCHAR(36) PRIMARY KEY, source_id VARCHAR(36) NOT NULL, "
            "target_id VARCHAR(36) NOT NULL, type VARCHAR(255) NOT NULL, properties TEXT NOT NULL, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        connection.execute(text(
            "INSERT INTO oaw_kg_entity VALUES ('e1', 'material', 'Cu', '{}', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
    store = SqlGraphStore(engine)
    assert store.counts(layer="literature") == {"entities": 1, "relations": 0}
    assert store.list_entities(group_id="nope") == []
    engine.dispose()


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
    assert summary["settings"]["vision_model"] == ""
    assert "text" in summary["engines"]

    # The vision model is a stored reference the conversion job resolves later.
    updated = run(card, actions.update_settings, vision_model="oaw:model:m1")
    assert updated["settings"]["vision_model"] == "oaw:model:m1"
    assert updated["settings"]["collection_name"] == "Alloys"


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
    assert "job" not in submitted
    processed = run(card, actions.process_sources, source_ids=[submitted["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "FAILED"
    assert "markdown engine" in (job.error or "")

    listing = run(card, actions.sources)["sources"]
    assert listing[0]["markdown"] is None
    with pytest.raises(KnowledgeError):
        run(card, actions.markdown, source_id=submitted["source"]["id"])


def test_an_image_upload_converts_through_a_configured_vision_engine(card, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {
                "content": "Handwritten note: sample A1 fired at 1750 C."}}]}

    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResponse()

    monkeypatch.setenv("OAW_VISION_API_KEY", "test-key")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr("httpx.Client", _FakeClient)

    submitted = run(card, actions.ingest, filename="notebook.jpg",
                    content_base64=base64.b64encode(b"fake-jpeg-bytes").decode(),
                    media_type="image/jpeg")
    processed = run(card, actions.process_sources, source_ids=[submitted["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error

    document = run(card, actions.markdown, source_id=submitted["source"]["id"])
    assert document["engine"] == "vision"
    assert "1750 C" in document["markdown"]

    # The vision transcription is searchable exactly like any other converted source.
    from oaw_knowledge_base import search_store

    hits = search_store.search(kb.oaw_engine, "handwritten note sample")
    assert hits and hits[0]["source_id"] == submitted["source"]["id"]


def test_jobs_report_progress_events(card):
    source_id, job = ingest_document(card)
    detail = run(card, actions.jobs, job_id=str(job.id))
    assert detail["job"]["status"] == "COMPLETED"
    assert detail["job"]["source_id"] == source_id
    assert any("markdown" in (event["message"] or "").lower() for event in detail["events"])
    assert json.dumps(detail)  # the payload must stay JSON serializable


def test_groups_can_be_created_renamed_and_deleted(card):
    listed = run(card, actions.groups)["groups"]
    assert len(listed) == 1 and listed[0]["is_default"]
    default_id = listed[0]["id"]

    created = run(card, actions.groups, operation="create", name="Alloys")["group"]
    assert created["name"] == "Alloys" and created["source_count"] == 0

    with pytest.raises(KnowledgeError):
        run(card, actions.groups, operation="create", name="Alloys")

    renamed = run(card, actions.groups, operation="rename",
                  group_id=created["id"], name="Alloys v2")["group"]
    assert renamed["name"] == "Alloys v2"

    run(card, actions.groups, operation="delete", group_id=created["id"])
    assert {item["id"] for item in run(card, actions.groups)["groups"]} == {default_id}

    with pytest.raises(KnowledgeError):
        run(card, actions.groups, operation="delete", group_id=default_id)


def test_sources_are_filed_into_the_chosen_group_and_listed_separately(card):
    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]

    default_source, _ = ingest_document(card, filename="default.md")
    ingested = run(card, actions.ingest, filename="alloy.md",
                  content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
                  media_type="text/markdown", group_id=alloys["id"])
    assert ingested["group_id"] == alloys["id"]
    assert "job" not in ingested  # a batch of one still needs an explicit process step

    # The default view stays scoped to the default group, exactly as a plain card
    # without any groups behaves.
    default_only = run(card, actions.sources)["sources"]
    assert [item["filename"] for item in default_only] == ["default.md"]

    scoped = run(card, actions.sources, group_id=alloys["id"])["sources"]
    assert [item["filename"] for item in scoped] == ["alloy.md"]
    assert scoped[0]["group_name"] == "Alloys"

    everything = run(card, actions.sources, all_groups=True)["sources"]
    assert {item["filename"] for item in everything} == {"default.md", "alloy.md"}

    overview_default = run(card, actions.overview)
    assert overview_default["counts"]["sources"] == 1
    assert {group["name"] for group in overview_default["groups"]} == {"Knowledge base", "Alloys"}
    overview_all = run(card, actions.overview, all_groups=True)
    assert overview_all["counts"]["sources"] == 2


def test_process_sources_runs_in_batch_and_skips_what_is_already_converted(card):
    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]
    ids = []
    for name in ("a.md", "b.md"):
        result = run(card, actions.ingest, filename=name,
                     content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
                     media_type="text/markdown", group_id=alloys["id"])
        ids.append(result["source"]["id"])
        assert "job" not in result  # uploading never queues its own conversion

    processed = run(card, actions.process_sources, source_ids=ids)
    kb = client.open_client(card.node_id, card.storage_path)
    for entry in processed["jobs"]:
        kb.jobs.wait(entry["job"]["id"], timeout=60)
    listing = run(card, actions.sources, group_id=alloys["id"])["sources"]
    assert all(item["markdown"] is not None for item in listing)

    # Re-running the batch over already-converted sources is a safe no-op.
    again = run(card, actions.process_sources, source_ids=ids)
    assert all(entry.get("skipped") == "already converted" for entry in again["jobs"])

    # With no source_ids, "process all pending" only picks up unconverted sources.
    run(card, actions.ingest, filename="c.md",
        content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
        media_type="text/markdown", group_id=alloys["id"])
    pending = run(card, actions.process_sources, group_id=alloys["id"])
    assert len(pending["jobs"]) == 1 and pending["jobs"][0]["filename"] == "c.md"


def test_drafts_and_the_graph_stay_scoped_to_the_group_that_built_them(card):
    default_source, _ = ingest_document(card, filename="default.md")
    default_record = run(card, actions.sources)["sources"][0]["record_id"]

    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]
    result = run(card, actions.ingest, filename="alloy.md",
                content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
                media_type="text/markdown", group_id=alloys["id"])
    processed = run(card, actions.process_sources, source_ids=[result["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    alloy_record = run(card, actions.sources, group_id=alloys["id"])["sources"][0]["record_id"]

    schema = run(card, actions.schemas, operation="create", name="Process graph",
                definition=SCHEMA, system_prompt="Extract entities and relations.")["schema"]

    def project(record_id, name):
        data = {"entities": [{"type": "material", "name": name}], "relations": []}
        return run(card, actions.save_projection, schema_id=schema["id"],
                  record_id=record_id, data=data)["projection"]["id"]

    default_projection = project(default_record, "Cu")
    alloy_projection = project(alloy_record, "Steel")

    # Mixing groups in one draft is refused, so a published graph traces back to
    # exactly one group's sources.
    with pytest.raises(KnowledgeError):
        run(card, actions.draft, operation="create",
            projection_ids=[default_projection, alloy_projection])

    default_draft = run(card, actions.draft, operation="create",
                        projection_ids=[default_projection])["draft"]
    alloy_draft = run(card, actions.draft, operation="create",
                      projection_ids=[alloy_projection])["draft"]

    confirmed = KnowledgeContext(card.node_id, card.storage_path, state=card.state,
                                confirmed=True)
    for built in (default_draft, alloy_draft):
        run(card, actions.review, operation="submit", draft_id=built["id"], expected_revision=1)
        run(confirmed, actions.review, operation="approve", draft_id=built["id"],
            expected_revision=1)

    all_entities = run(card, actions.graph)["entities"]
    assert {item["name"] for item in all_entities} == {"Cu", "Steel"}

    default_group_id = run(card, actions.overview)["collection"]["id"]
    scoped = run(card, actions.graph, group_id=default_group_id)["entities"]
    assert {item["name"] for item in scoped} == {"Cu"}
    scoped_alloy = run(card, actions.graph, group_id=alloys["id"])["entities"]
    assert {item["name"] for item in scoped_alloy} == {"Steel"}


EXPERIMENT_SCHEMA = {
    "type": "object", "required": ["sample_id", "conductivity"],
    "properties": {"sample_id": {"type": "string"}, "conductivity": {"type": "number"}},
}


def _experiment_schema(card):
    return run(card, actions.schemas, operation="create", name="Conductivity run",
              definition=EXPERIMENT_SCHEMA, system_prompt="x", kind="experiment")["schema"]


def _bundle(card, schema, name="Batch 7", notes=""):
    return run(card, actions.experiment_bundle_manage, operation="create",
               schema_id=schema["id"], name=name, notes=notes)["bundle"]


def _upload(card, bundle, text, filename, media_type="text/markdown"):
    """Upload one file into a bundle and wait for the conversion it queues itself."""
    result = run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename=filename,
                 content_base64=base64.b64encode(text.encode()).decode(), media_type=media_type)
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(result["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return result["source"]["id"]


def test_an_experiment_upload_is_projected_into_several_reviewed_records(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema, notes="Conductivity is in S/cm; the photo shows batch 7.")
    assert bundle["status"] == "ready" and bundle["files"] == [] and bundle["records"] == 0

    sheet = _upload(card, bundle, "sample,conductivity\nA1,1.2\nA2,0.9\n", "batch7.csv", "text/csv")
    note = _upload(card, bundle, "# Notes\n\nA2 was cracked after pressing.\n\n"
                   + "Pressing log line.\n" * 40, "notes.md")

    # The upload's files are the Experiment section's, not Literature's.
    assert run(card, actions.sources)["sources"] == []
    tagged = run(card, actions.sources, workflow="experiment")["sources"]
    assert {item["filename"] for item in tagged} == {"batch7.csv", "notes.md"}
    assert {item["workflow"] for item in tagged} == {"experiment"}
    assert len(run(card, actions.sources, workflow="all")["sources"]) == 2

    listed = run(card, actions.experiment_bundles, schema_id=schema["id"])["bundles"]
    assert [item["id"] for item in listed] == [bundle["id"]]
    assert [f["state"] for f in listed[0]["files"]] == ["converted", "converted"]

    # One document: both files in upload order, each under a heading that names it.
    prompt = run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])
    assert prompt["schema_id"] == schema["id"] and prompt["definition"] == EXPERIMENT_SCHEMA
    assert prompt["notes"].startswith("Conductivity is in S/cm")
    assert [f["source_id"] for f in prompt["files"]] == [sheet, note]
    assert prompt["markdown"].index(f"## File: batch7.csv (source_id={sheet})") < \
        prompt["markdown"].index(f"## File: notes.md (source_id={note})")
    assert "| A1 |" in prompt["markdown"] and "cracked" in prompt["markdown"]
    assert prompt["has_more"] is False and "records" in prompt["instruction"]
    page = run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"], limit=500)
    assert page["has_more"] is True and len(page["markdown"]) == 500
    assert page["total_characters"] == prompt["total_characters"]

    saved = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.2}, "source_ids": [sheet]},
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 0.9}, "source_ids": [sheet, note],
         "note": "cracked, see notes.md"},
        {"name": "A3", "data": {"sample_id": "A3"}},
    ])
    assert saved["invalid"] == 1
    by_name = {item["name"]: item for item in saved["records"]}
    assert by_name["A1"]["status"] == "draft" and by_name["A1"]["bundle_id"] == bundle["id"]
    assert [s["filename"] for s in by_name["A1"]["sources"]] == ["batch7.csv"]
    assert {s["source_id"] for s in by_name["A2"]["sources"]} == {sheet, note}
    # Uncited records are linked to every file in the upload.
    assert {s["source_id"] for s in by_name["A3"]["sources"]} == {sheet, note}
    assert by_name["A3"]["validation"]["valid"] is False
    assert run(card, actions.experiment_bundles, operation="get", bundle_id=bundle["id"])["bundle"]["status"] == "extracted"

    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
            {"name": "X", "data": {}, "source_ids": [str(uuid.uuid4())]}])

    listed = run(card, actions.experiments, bundle_id=bundle["id"])["experiments"]
    assert {item["name"] for item in listed} == {"A1", "A2", "A3"}
    assert run(card, actions.overview)["counts"]["experiment_records"] == 3

    # A person fixes the invalid one; the edit is re-validated.
    fixed = run(card, actions.experiment_update, record_id=by_name["A3"]["id"],
                data={"sample_id": "A3", "conductivity": 1.1}, expected_revision=1)["experiment"]
    assert fixed["revision"] == 2 and fixed["validation"]["valid"] is True

    # Publishing asks first, then marks the record approved and freezes it.
    asked = run(card, actions.experiment_review, operation="publish", record_id=by_name["A1"]["id"])
    assert asked["status"] == "confirmation_required"
    card.confirmed = True
    published = run(card, actions.experiment_review, operation="publish",
                    record_id=by_name["A1"]["id"], expected_revision=1)["experiment"]
    card.confirmed = False
    assert published["status"] == "published"
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_update, record_id=published["id"], name="A1b")
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_review, operation="delete", record_id=published["id"])
    assert {item["name"] for item in run(card, actions.experiments, status="published")["experiments"]} == {"A1"}

    # Re-extraction replaces the drafts but keeps what a person already published.
    again = run(card, actions.experiment_save, bundle_id=bundle["id"], replace_drafts=True, records=[
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 0.95}}])
    assert len(again["records"]) == 1
    remaining = run(card, actions.experiments, bundle_id=bundle["id"])["experiments"]
    assert {item["name"] for item in remaining} == {"A1", "A2"}

    reopened = run(card, actions.experiment_review, operation="unpublish",
                   record_id=published["id"])["experiment"]
    assert reopened["status"] == "draft"
    run(card, actions.experiment_review, operation="delete", record_id=reopened["id"])
    assert {item["name"] for item in run(card, actions.experiments, bundle_id=bundle["id"])["experiments"]} == {"A2"}


def test_a_record_that_fails_its_schema_cannot_be_published(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "A9\n", "a.md")
    saved = run(card, actions.experiment_save, bundle_id=bundle["id"],
                records=[{"name": "A9", "data": {"sample_id": "A9"}}])
    card.confirmed = True
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_review, operation="publish", record_id=saved["records"][0]["id"])


def test_extraction_waits_for_every_file_and_an_upload_needs_an_experiment_schema(card):
    literature = run(card, actions.schemas, operation="create", name="Literature schema",
                     definition=SCHEMA, system_prompt="x")["schema"]
    with pytest.raises(KnowledgeError):
        _bundle(card, literature)

    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_save, bundle_id=bundle["id"],
            records=[{"name": "A", "data": {}}])

    # Queue a file but do not wait for it: the prompt refuses until it is converted.
    result = run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename="a.md",
                 content_base64=base64.b64encode(b"# A\n").decode(), media_type="text/markdown")
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.get(result["job"]["id"])
    if job.status != "COMPLETED":
        with pytest.raises(KnowledgeError, match="Not every file is converted yet"):
            run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])
    kb.jobs.wait(result["job"]["id"], timeout=60)
    assert run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])["has_more"] is False


def test_deleting_an_upload_removes_its_records_and_keeps_its_files_out_of_literature(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "A1 1.0\n", "a.md")
    run(card, actions.experiment_save, bundle_id=bundle["id"],
        records=[{"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.0}}])

    marked = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
                 status="extracting", run_id="run-1")["bundle"]
    assert (marked["status"], marked["run_id"]) == ("extracting", "run-1")
    renamed = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
                  name="Batch 7b")["bundle"]
    assert renamed["run_id"] == "run-1" and renamed["name"] == "Batch 7b"
    # The schema is fixed once records exist, and can only ever be an experiment schema.
    other = run(card, actions.schemas, operation="create", name="Other run",
                definition=EXPERIMENT_SCHEMA, system_prompt="x", kind="experiment")["schema"]
    with pytest.raises(KnowledgeError, match="already has records"):
        run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
            schema_id=other["id"])
    empty = _bundle(card, schema, name="Empty")
    literature = run(card, actions.schemas, operation="create", name="Lit", definition=SCHEMA,
                     system_prompt="x")["schema"]
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_bundle_manage, operation="update", bundle_id=empty["id"],
            schema_id=literature["id"])
    moved = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=empty["id"],
                schema_id=other["id"])["bundle"]
    assert moved["schema_id"] == other["id"]
    run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=empty["id"])

    removed = run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=bundle["id"])
    assert removed["records_removed"] == 1
    assert run(card, actions.experiment_bundles)["bundles"] == []
    assert run(card, actions.experiments)["experiments"] == []
    assert run(card, actions.sources)["sources"] == []
    assert len(run(card, actions.sources, workflow="experiment")["sources"]) == 1
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename="b.md",
            content_base64=base64.b64encode(b"x").decode())


def test_chart_data_source_reads_published_experiment_records_only(card):
    # data_source.py is the one module besides plugin.py that imports OAW itself.
    pytest.importorskip("open_agent_world", reason="Run inside the OAW backend environment")
    from oaw_knowledge_base import data_source

    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "x\n", "a.md")
    saved = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.2}},
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 0.9}}])
    card.confirmed = True
    run(card, actions.experiment_review, operation="publish", record_id=saved["records"][0]["id"])

    class Query:
        schema_id = f"experiments:{schema['id']}"
        aggregate = "none"
        columns = ["sample_id", "conductivity"]
        order_by = None
        descending = False
        limit = 50
        entity_type = relation_type = None

    table = data_source.read(card, Query())
    assert table["rows"] == [["A1", 1.2]]


INSIGHT_SCHEMA = {
    "type": "object", "required": ["sample_id"],
    "properties": {"sample_id": {"type": "string"}, "temperature": {"type": "number"},
                   "conductivity": {"type": "number"}, "moisture": {"type": "number"}},
}


def _published_batch(card, count=8):
    """One experiment schema with ``count`` published records (one moisture outlier)
    and one draft record that must stay invisible to insights."""
    from oaw_knowledge_base import insights

    schema = run(card, actions.schemas, operation="create", name="Electrolyte batch",
                 definition=INSIGHT_SCHEMA, system_prompt="x", kind="experiment")["schema"]
    bundle = _bundle(card, schema, name="July")
    _upload(card, bundle, "log\n", "log.md")
    records = [{"name": f"B{i}", "data": {"sample_id": f"B{i}", "temperature": 500 + 10 * i,
                                        "conductivity": 1.0 + 0.1 * i,
                                        "moisture": 9.0 if i == count - 1 else 0.5 + 0.01 * i}}
               for i in range(count)]
    records.append({"name": "DRAFT", "data": {"sample_id": "DRAFT", "moisture": 99}})
    saved = run(card, actions.experiment_save, bundle_id=bundle["id"], records=records)["records"]
    card.confirmed = True
    for record in saved:
        if record["name"] != "DRAFT":
            run(card, actions.experiment_review, operation="publish", record_id=record["id"])
    card.confirmed = False
    by_name = {item["name"]: item for item in saved}
    return insights, schema, by_name


def test_insights_are_drafted_from_published_records_and_published_into_the_graph_layer(card):
    insights, schema, records = _published_batch(card)
    outlier, draft = records["B7"], records["DRAFT"]

    # The prompt: statistics the plugin computed, the records as a table, and the
    # draft record nowhere in it.
    prompt = run(card, insights.insight_prompt, schema_id=schema["id"])
    assert prompt["record_count"] == 8 and prompt["existing"] == []
    assert set(prompt["kinds"]) == {"anomaly", "rule", "trend", "correlation", "threshold", "note"}
    stats = {item["field"]: item for item in prompt["statistics"]["fields"]}
    assert [o["record_id"] for o in stats["moisture"]["outliers"]] == [outlier["id"]]
    assert prompt["statistics"]["correlations"][0]["a"] == "temperature"
    assert f"| {outlier['id']} | B7 |" in prompt["markdown"]
    assert draft["id"] not in prompt["markdown"] and "| DRAFT |" not in prompt["markdown"]
    assert prompt["has_more"] is False and "insights" in prompt["instruction"]
    page = run(card, insights.insight_prompt, schema_id=schema["id"], limit=500)
    assert page["has_more"] is True and page["total_characters"] == prompt["total_characters"]

    # Evidence must be published records of this schema; fields are advisory.
    with pytest.raises(KnowledgeError):
        run(card, insights.insight_save, schema_id=schema["id"], insights=[
            {"kind": "note", "title": "x", "statement": "y", "evidence": [{"record_id": draft["id"]}]}])
    with pytest.raises(KnowledgeError):
        run(card, insights.insight_prompt, schema_id=run(
            card, actions.schemas, operation="create", name="Lit", definition=SCHEMA,
            system_prompt="x")["schema"]["id"])
    saved = run(card, insights.insight_save, schema_id=schema["id"], run_id="run-1", insights=[
        {"kind": "anomaly", "title": "B7 moisture high",
         "statement": "B7 moisture is far above the batch mean.", "fields": ["moisture", "humidity"],
         "confidence": 0.9, "properties": {"z": 2.6, "type": "ignored"},
         "evidence": [{"record_id": outlier["id"], "role": "observed_in"}]},
        {"kind": "correlation", "title": "Conductivity rises with temperature",
         "statement": "r = 1.0 over 8 records.", "fields": ["temperature", "conductivity"],
         "evidence": [{"record_id": records[f"B{i}"]["id"]} for i in range(8)]},
    ])
    assert saved["warnings"] == ['"B7 moisture high": unknown fields dropped: humidity']
    anomaly, correlation = saved["insights"]
    assert anomaly["status"] == "draft" and anomaly["fields"] == ["moisture"]
    assert anomaly["evidence"] == [{"record_id": outlier["id"], "role": "observed_in",
                                    "name": "B7", "schema_id": schema["id"], "stale": False}]
    assert anomaly["schema_id"] == schema["id"] and anomaly["schema_ids"] == [schema["id"]]
    assert anomaly["run_id"] == "run-1" and anomaly["created_by"] == "user:desktop"
    counts = run(card, actions.overview)["counts"]
    assert counts["insights"] == 2 and counts["published_insights"] == 0

    # Reading: list, get, stats — the agent's side of the tab.
    listed = run(card, insights.insights, schema_id=schema["id"], kind="anomaly")["insights"]
    assert [item["id"] for item in listed] == [anomaly["id"]]
    assert run(card, insights.insights, operation="get", insight_id=anomaly["id"])["insight"]["title"] == "B7 moisture high"
    computed = run(card, insights.insights, operation="stats", schema_id=schema["id"])["statistics"]
    assert computed == prompt["statistics"]

    # A person edits a draft; the published graph is still empty.
    edited = run(card, insights.insight_update, insight_id=anomaly["id"], title="B7 is wet",
                 confidence=0.95, expected_revision=1)["insight"]
    assert edited["revision"] == 2 and edited["confidence"] == 0.95
    assert run(card, actions.graph, layer="experiment")["entities"] == []

    # Publishing asks first, then writes the insight and what it cites into the
    # experiment layer — and only there.
    asked = run(card, insights.insight_review, operation="publish", insight_ids=[anomaly["id"], correlation["id"]])
    assert asked["status"] == "confirmation_required" and "B7 is wet" in asked["message"]
    card.confirmed = True
    published = run(card, insights.insight_review, operation="publish",
                    insight_ids=[anomaly["id"], correlation["id"]])
    card.confirmed = False
    assert {item["status"] for item in published["insights"]} == {"published"}
    # 2 insights + 3 fields + 8 records; B7 is cited by both, so 11 relations.
    assert published["graph"] == {"entities": 2 + 3 + 8 + 1, "relations": 1 + 1 + 2 + 8}
    layer = run(card, actions.graph, layer="experiment", limit=100)
    assert {e["type"] for e in layer["entities"]} == {"anomaly", "correlation", "field", "experiment_record"}
    assert len([e for e in layer["entities"] if e["type"] == "experiment_record"]) == 8
    node = next(e for e in layer["entities"] if e["type"] == "anomaly")
    assert node["name"] == "B7 is wet" and node["properties"]["insight_id"] == anomaly["id"]
    assert node["properties"]["z"] == 2.6 and "type" not in node["properties"]
    assert {r["type"] for r in layer["relations"]} == {"involves", "observed_in", "supported_by"}
    assert run(card, actions.graph, layer="literature")["entities"] == []
    assert run(card, actions.overview)["counts"]["layers"]["experiment"]["entities"] == 13
    with pytest.raises(KnowledgeError):
        run(card, insights.insight_update, insight_id=anomaly["id"], title="late")
    with pytest.raises(KnowledgeError):
        run(card, insights.insight_review, operation="delete", insight_id=anomaly["id"])
    assert [item["title"] for item in run(card, insights.insight_prompt, schema_id=schema["id"])["existing"]] \
        == ["Conductivity rises with temperature", "B7 is wet"]

    # Re-extraction replaces drafts and never a published insight.
    run(card, insights.insight_save, schema_id=schema["id"], insights=[
        {"kind": "note", "title": "old draft", "statement": "s", "evidence": [{"record_id": outlier["id"]}]}])
    again = run(card, insights.insight_save, schema_id=schema["id"], replace_drafts=True, insights=[
        {"kind": "note", "title": "new draft", "statement": "s", "evidence": [{"record_id": outlier["id"]}]}])
    titles = {item["title"] for item in run(card, insights.insights, schema_id=schema["id"])["insights"]}
    assert titles == {"B7 is wet", "Conductivity rises with temperature", "new draft"}

    # Unpublishing the anomaly removes its node and edges; B7's record node stays
    # because the correlation still cites it, and the moisture field node goes.
    reopened = run(card, insights.insight_review, operation="unpublish", insight_id=anomaly["id"])
    assert reopened["insights"][0]["status"] == "draft" and reopened["graph"] == {"removed": 1, "pruned": 1}
    layer = run(card, actions.graph, layer="experiment", limit=100)
    assert {e["type"] for e in layer["entities"]} == {"correlation", "field", "experiment_record"}
    assert len(layer["entities"]) == 1 + 2 + 8
    run(card, insights.insight_review, operation="unpublish", insight_id=correlation["id"])
    assert run(card, actions.graph, layer="experiment")["entities"] == []
    run(card, insights.insight_review, operation="delete", insight_ids=[anomaly["id"], again["insights"][0]["id"]])
    assert [item["title"] for item in run(card, insights.insights, schema_id=schema["id"])["insights"]] \
        == ["Conductivity rises with temperature"]


def test_an_insight_whose_record_was_unpublished_is_stale_and_cannot_be_published(card):
    insights, schema, records = _published_batch(card, count=4)
    saved = run(card, insights.insight_save, schema_id=schema["id"], insights=[
        {"kind": "note", "title": "about B1", "statement": "s", "evidence": [{"record_id": records["B1"]["id"]}]}])
    run(card, actions.experiment_review, operation="unpublish", record_id=records["B1"]["id"])
    insight = run(card, insights.insights, operation="get", insight_id=saved["insights"][0]["id"])["insight"]
    assert insight["evidence"][0]["stale"] is True
    card.confirmed = True
    with pytest.raises(KnowledgeError):
        run(card, insights.insight_review, operation="publish", insight_id=insight["id"])


def test_insights_scoped_to_a_group_see_only_that_groups_records(card):
    insights, schema, records = _published_batch(card, count=4)
    other = run(card, actions.groups, operation="create", name="Other")["group"]
    with pytest.raises(KnowledgeError):
        run(card, insights.insight_prompt, schema_id=schema["id"], group_id=other["id"])
    everywhere = run(card, insights.insight_prompt, schema_id=schema["id"], all_groups=True)
    assert everywhere["group_id"] is None and everywhere["record_count"] == 4
    saved = run(card, insights.insight_save, schema_id=schema["id"], all_groups=True, insights=[
        {"kind": "note", "title": "global", "statement": "s", "evidence": [{"record_id": records["B0"]["id"]}]}])
    assert saved["insights"][0]["group_id"] is None
    # An all-groups insight shows up inside any one group's view too.
    default = run(card, actions.overview)["collection"]["id"]
    assert [i["title"] for i in run(card, insights.insights, group_id=default)["insights"]] == ["global"]
    assert [i["title"] for i in run(card, insights.insights, group_id=other["id"])["insights"]] == ["global"]


XRD_SCHEMA = {"type": "object", "required": ["sample_id"],
              "properties": {"sample_id": {"type": "string"}, "crystallinity": {"type": "number"},
                             "phase": {"type": "string"}}}


def test_insights_can_span_several_schemas_matched_by_specimen_name(card):
    insights, synthesis, records = _published_batch(card, count=6)
    # A second schema describing the same specimens B0..B5, published too.
    xrd = run(card, actions.schemas, operation="create", name="XRD", definition=XRD_SCHEMA,
              system_prompt="x", kind="experiment")["schema"]
    bundle = _bundle(card, xrd, name="XRD July")
    _upload(card, bundle, "xrd\n", "xrd.md")
    saved = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": f"B{i}", "data": {"sample_id": f"B{i}", "crystallinity": 0.5 + 0.05 * i, "phase": "argyrodite"}}
        for i in range(6)])["records"]
    card.confirmed = True
    for record in saved:
        run(card, actions.experiment_review, operation="publish", record_id=record["id"])
    card.confirmed = False
    # An experiment schema with no published records is left out of an open scope.
    run(card, actions.schemas, operation="create", name="Empty", definition=XRD_SCHEMA,
        system_prompt="x", kind="experiment")

    # No schema named: every schema with published records, each with its own table
    # and statistics, plus what the specimens shared by name show across them.
    prompt = run(card, insights.insight_prompt)
    assert [item["schema_name"] for item in prompt["schemas"]] == ["Electrolyte batch", "XRD"]
    # Presentation follows the schema names; the scope key is the sorted id set.
    assert prompt["schema_ids"] == sorted([synthesis["id"], xrd["id"]])
    assert "schema_id" not in prompt and prompt["record_count"] == 12
    assert prompt["markdown"].index("# Schema: Electrolyte batch") < prompt["markdown"].index("# Schema: XRD")
    assert prompt["cross_schema"]["matched"] == 6
    assert {(c["a"], c["b"]) for c in prompt["cross_schema"]["correlations"]} >= {
        ("Electrolyte batch.temperature", "XRD.crystallinity")}
    assert len(prompt["statistics_by_schema"]) == 2
    # Naming two schemas explicitly is the same scope; naming one narrows it.
    explicit = run(card, insights.insight_prompt, schema_ids=[xrd["id"], synthesis["id"]])
    assert explicit["schema_ids"] == prompt["schema_ids"]
    assert run(card, insights.insight_prompt, schema_id=xrd["id"])["record_count"] == 6

    xrd_b3 = next(item for item in saved if item["name"] == "B3")
    result = run(card, insights.insight_save, insights=[
        {"kind": "correlation", "title": "Hotter batches are more crystalline",
         "statement": "Crystallinity rises with sintering temperature across the six specimens.",
         "fields": ["Electrolyte batch.temperature", "XRD.crystallinity", "phase", "temperature"],
         "evidence": [{"record_id": records["B3"]["id"]}, {"record_id": xrd_b3["id"]}]}])
    cross = result["insights"][0]
    assert result["schema_ids"] == prompt["schema_ids"] and cross["schema_id"] is None
    assert cross["schema_ids"] == prompt["schema_ids"]
    # A bare field name resolves when exactly one schema declares it; "temperature"
    # also exists only in the batch schema, so both bare names are kept.
    assert cross["fields"] == ["Electrolyte batch.temperature", "XRD.crystallinity", "phase", "temperature"]
    assert {link["schema_id"] for link in cross["evidence"]} == {synthesis["id"], xrd["id"]}

    # It is listed under either schema, found by text, and not under an unrelated one.
    assert [i["id"] for i in run(card, insights.insights, schema_id=xrd["id"])["insights"]] == [cross["id"]]
    assert [i["id"] for i in run(card, insights.insights, schema_id=synthesis["id"])["insights"]] == [cross["id"]]
    assert [i["id"] for i in run(card, insights.insights, query="crystalline")["insights"]] == [cross["id"]]
    assert run(card, insights.insights, query="nothing like this")["insights"] == []
    stats = run(card, insights.insights, operation="stats")
    assert stats["cross_schema"]["matched"] == 6 and "statistics" not in stats

    card.confirmed = True
    published = run(card, insights.insight_review, operation="publish", insight_id=cross["id"])
    card.confirmed = False
    # 1 insight + 3 resolved field nodes (temperature named twice is one node, one
    # edge) + 2 records.
    assert published["graph"] == {"entities": 1 + 3 + 2, "relations": 3 + 2}
    layer = run(card, actions.graph, layer="experiment", limit=100)
    fields = {e["name"] for e in layer["entities"] if e["type"] == "field"}
    assert fields == {"Electrolyte batch · temperature", "XRD · crystallinity", "XRD · phase"}
    node = next(e for e in layer["entities"] if e["type"] == "correlation")
    assert node["properties"]["schema_ids"] == prompt["schema_ids"]

    # Re-extracting the cross-schema scope replaces only its own drafts.
    single = run(card, insights.insight_save, schema_id=xrd["id"], insights=[
        {"kind": "note", "title": "xrd only", "statement": "s", "evidence": [{"record_id": xrd_b3["id"]}]}])
    run(card, insights.insight_save, replace_drafts=True, insights=[
        {"kind": "note", "title": "cross draft", "statement": "s", "evidence": [{"record_id": xrd_b3["id"]}]}])
    run(card, insights.insight_save, replace_drafts=True, insights=[
        {"kind": "note", "title": "cross draft 2", "statement": "s", "evidence": [{"record_id": xrd_b3["id"]}]}])
    titles = {i["title"] for i in run(card, insights.insights)["insights"]}
    assert titles == {"Hotter batches are more crystalline", "xrd only", "cross draft 2"}
    assert run(card, insights.insights, operation="get", insight_id=single["insights"][0]["id"])["insight"]["schema_id"] == xrd["id"]
