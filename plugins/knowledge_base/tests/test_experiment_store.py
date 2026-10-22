"""experiment_store.py in isolation: bundles, records, source links, revisions, migration."""
import json

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

from oaw_knowledge_base import experiment_store  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402
from oaw_knowledge_base.experiment_store import ExperimentRecordStore  # noqa: E402


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


@pytest.fixture
def store(engine):
    return ExperimentRecordStore(engine)


def test_create_returns_a_draft_at_revision_one(store):
    record = store.create(group_id="g1", schema_id="sch1", name="Run A1",
                          data={"conductivity": 1.2}, created_by="user:desktop",
                          validation={"valid": True})
    assert record["status"] == "draft"
    assert record["revision"] == 1
    assert record["bundle_id"] is None
    assert record["validation"] == {"valid": True}
    assert record["data"] == {"conductivity": 1.2}
    assert record["created_by"] == "user:desktop"
    assert record["created_at"].tzinfo is not None


def test_get_and_require_round_trip(store):
    created = store.create(group_id="g1", schema_id="sch1", name="Run A1", data={})
    assert store.get(created["id"]) == created
    assert store.require(created["id"]) == created
    assert store.get("missing") is None
    with pytest.raises(KnowledgeError):
        store.require("missing")


def test_list_filters_by_group_schema_bundle_and_status(store):
    bundle = store.create_bundle(group_id="g1", schema_id="sch1", name="Sheet 1")
    a = store.create(group_id="g1", schema_id="sch1", name="A", data={}, bundle_id=bundle["id"])
    store.create(group_id="g2", schema_id="sch1", name="B", data={})
    store.update(a["id"], status="published")

    assert {item["name"] for item in store.list(group_id="g1")} == {"A"}
    assert {item["name"] for item in store.list()} == {"A", "B"}
    assert {item["name"] for item in store.list(status="published")} == {"A"}
    assert {item["name"] for item in store.list(status="draft")} == {"B"}
    assert {item["name"] for item in store.list(bundle_id=bundle["id"])} == {"A"}


def test_update_bumps_revision_and_merges_only_given_fields(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={"x": 1},
                          note="from row 3")
    updated = store.update(record["id"], data={"x": 2}, expected_revision=1)
    assert updated["revision"] == 2
    assert updated["data"] == {"x": 2}
    assert updated["note"] == "from row 3"
    assert updated["name"] == "A"


def test_update_rejects_a_stale_revision(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    store.update(record["id"], data={"x": 1}, expected_revision=1)
    with pytest.raises(experiment_store.ConflictingRevision):
        store.update(record["id"], data={"x": 2}, expected_revision=1)


def test_update_rejects_an_unknown_status(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    with pytest.raises(ValueError):
        store.update(record["id"], status="confirmed")


def test_source_links_are_replaced_wholesale_and_deduplicated(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    store.set_sources(record["id"], [
        {"source_id": "s1", "artifact_id": "a1"}, {"source_id": "s2"}, {"source_id": "s1"}])
    assert len(store.sources_for(record["id"])) == 2

    store.set_sources(record["id"], [{"source_id": "s3"}])
    links = store.sources_for(record["id"])
    assert len(links) == 1
    assert links[0]["source_id"] == "s3"
    assert links[0]["artifact_id"] is None


def test_delete_removes_the_record_and_its_links(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    store.set_sources(record["id"], [{"source_id": "s1"}])
    store.delete(record["id"])
    assert store.get(record["id"]) is None
    assert store.sources_for(record["id"]) == []
    with pytest.raises(KnowledgeError):
        store.delete(record["id"])


def test_bundles_hold_ordered_sources_and_tag_them(store):
    bundle = store.create_bundle(group_id="g1", schema_id="sch1", name="Sheet 1",
                                 notes="columns are mm", created_by="user:desktop")
    assert bundle["status"] == "ready"
    assert bundle["notes"] == "columns are mm"
    store.add_bundle_source(bundle["id"], "s2")
    store.add_bundle_source(bundle["id"], "s1")
    assert store.bundle_source_ids(bundle["id"]) == ["s2", "s1"]
    assert store.bundle_of_source("s1") == bundle["id"]
    assert store.bundle_of_source("other") is None
    assert store.tagged_source_ids() == {"s1", "s2"}
    store.remove_bundle_source("s2")
    assert store.bundle_source_ids(bundle["id"]) == ["s1"]
    assert [item["id"] for item in store.list_bundles(schema_id="sch1")] == [bundle["id"]]
    assert store.list_bundles(schema_id="other") == []


def test_bundle_update_sets_status_and_can_clear_the_run(store):
    bundle = store.create_bundle(group_id="g1", schema_id="sch1", name="Sheet 1")
    running = store.update_bundle(bundle["id"], status="extracting", run_id="run-1")
    assert (running["status"], running["run_id"]) == ("extracting", "run-1")
    done = store.update_bundle(bundle["id"], status="extracted", run_id=None)
    assert (done["status"], done["run_id"]) == ("extracted", None)
    renamed = store.update_bundle(bundle["id"], name="Sheet A")
    assert renamed["run_id"] is None and renamed["name"] == "Sheet A"
    with pytest.raises(KnowledgeError):
        store.update_bundle(bundle["id"], status="done")
    with pytest.raises(KnowledgeError):
        store.update_bundle("missing", name="x")


def test_bundle_schema_moves_only_while_it_has_no_records(store):
    bundle = store.create_bundle(group_id="g1", schema_id="sch1", name="Sheet 1")
    moved = store.update_bundle(bundle["id"], schema_id="sch2")
    assert moved["schema_id"] == "sch2"
    store.create(group_id="g1", schema_id="sch2", name="A", data={}, bundle_id=bundle["id"])
    with pytest.raises(KnowledgeError):
        store.update_bundle(bundle["id"], schema_id="sch1")
    assert store.require_bundle(bundle["id"])["schema_id"] == "sch2"


def test_reextraction_drops_only_the_bundle_drafts(store):
    bundle = store.create_bundle(group_id="g1", schema_id="sch1", name="Sheet 1")
    draft = store.create(group_id="g1", schema_id="sch1", name="A", data={}, bundle_id=bundle["id"])
    store.set_sources(draft["id"], [{"source_id": "s1"}])
    published = store.create(group_id="g1", schema_id="sch1", name="B", data={}, bundle_id=bundle["id"])
    store.update(published["id"], status="published")
    other = store.create(group_id="g1", schema_id="sch1", name="C", data={})

    assert store.delete_drafts_for_bundle(bundle["id"]) == [draft["id"]]
    assert store.get(draft["id"]) is None
    assert store.sources_for(draft["id"]) == []
    assert store.get(published["id"]) is not None
    assert store.get(other["id"]) is not None


def test_deleting_a_bundle_removes_its_records_but_keeps_its_files_tagged(store):
    bundle = store.create_bundle(group_id="g1", schema_id="sch1", name="Sheet 1")
    store.add_bundle_source(bundle["id"], "s1")
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={}, bundle_id=bundle["id"])
    store.update(record["id"], status="published")
    assert store.delete_bundle(bundle["id"]) == [record["id"]]
    assert store.get_bundle(bundle["id"])["status"] == "deleted"
    assert store.list_bundles() == []
    assert store.get(record["id"]) is None
    # MKB cannot delete the source, so it must not resurface in Literature.
    assert store.tagged_source_ids() == {"s1"}
    with pytest.raises(KnowledgeError):
        store.delete_bundle("missing")


def test_a_database_from_the_merge_era_is_migrated_once(engine):
    """Old layout: no bundle columns, ``confirmed`` status, projection-keyed evidence."""
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE oaw_kb_experiment_record (id VARCHAR(36) PRIMARY KEY, group_id VARCHAR(36) NOT NULL, "
            "schema_id VARCHAR(36) NOT NULL, name VARCHAR(500) NOT NULL, data TEXT NOT NULL, conflicts TEXT NOT NULL, "
            "status VARCHAR(32) NOT NULL, revision INTEGER NOT NULL, created_by VARCHAR(255), "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        connection.execute(text(
            "CREATE TABLE oaw_kb_experiment_record_evidence (record_id VARCHAR(36) NOT NULL, "
            "projection_id VARCHAR(36) NOT NULL, source_id VARCHAR(36) NOT NULL, artifact_id VARCHAR(36), "
            "PRIMARY KEY (record_id, projection_id))"))
        connection.execute(text(
            "INSERT INTO oaw_kb_experiment_record VALUES ('r1', 'g1', 'sch1', 'A1', :data, '[]', 'confirmed', 2, "
            "'user:desktop', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"), {"data": json.dumps({"x": 1})})
        connection.execute(text(
            "INSERT INTO oaw_kb_experiment_record_evidence VALUES ('r1', 'p1', 's1', 'a1'), ('r1', 'p2', 's1', 'a2'), "
            "('r1', 'p3', 's2', NULL)"))

    store = ExperimentRecordStore(engine)
    record = store.require("r1")
    assert record["status"] == "published"
    assert record["bundle_id"] is None and record["validation"] is None
    links = {link["source_id"]: link["artifact_id"] for link in store.sources_for("r1")}
    assert links == {"s1": "a1", "s2": None}
    with engine.connect() as connection:
        tables = {row[0] for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")).all()}
    assert "oaw_kb_experiment_record_evidence" not in tables

    # Opening the store again is harmless.
    ExperimentRecordStore(engine)
    assert store.require("r1")["status"] == "published"
