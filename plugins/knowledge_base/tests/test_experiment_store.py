"""experiment_store.py in isolation: CRUD, evidence links, optimistic revisions."""
import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine  # noqa: E402

from oaw_knowledge_base import experiment_store  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402
from oaw_knowledge_base.experiment_store import ExperimentRecordStore  # noqa: E402


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    yield ExperimentRecordStore(engine)
    engine.dispose()


def test_create_returns_a_draft_at_revision_one(store):
    record = store.create(group_id="g1", schema_id="sch1", name="Run A1",
                          data={"conductivity": 1.2}, created_by="user:desktop")
    assert record["status"] == "draft"
    assert record["revision"] == 1
    assert record["conflicts"] == []
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


def test_list_filters_by_group_schema_and_status(store):
    a = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    store.create(group_id="g2", schema_id="sch1", name="B", data={})
    store.update(a["id"], status="confirmed")

    assert {item["name"] for item in store.list(group_id="g1")} == {"A"}
    assert {item["name"] for item in store.list()} == {"A", "B"}
    assert {item["name"] for item in store.list(status="confirmed")} == {"A"}
    assert {item["name"] for item in store.list(status="draft")} == {"B"}


def test_update_bumps_revision_and_merges_only_given_fields(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={"x": 1},
                          conflicts=[{"field": "x", "values": []}])
    updated = store.update(record["id"], data={"x": 2}, expected_revision=1)
    assert updated["revision"] == 2
    assert updated["data"] == {"x": 2}
    # Conflicts and name were not touched by this call.
    assert updated["conflicts"] == [{"field": "x", "values": []}]
    assert updated["name"] == "A"


def test_update_rejects_a_stale_revision(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    store.update(record["id"], data={"x": 1}, expected_revision=1)
    with pytest.raises(experiment_store.ConflictingRevision):
        store.update(record["id"], data={"x": 2}, expected_revision=1)


def test_update_rejects_an_unknown_status(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    with pytest.raises(ValueError):
        store.update(record["id"], status="published")


def test_evidence_links_are_replaced_wholesale_on_reassembly(store):
    record = store.create(group_id="g1", schema_id="sch1", name="A", data={})
    store.set_evidence(record["id"], [
        {"projection_id": "p1", "source_id": "s1", "artifact_id": "a1"},
        {"projection_id": "p2", "source_id": "s2", "artifact_id": None},
    ])
    assert len(store.evidence_for(record["id"])) == 2

    store.set_evidence(record["id"], [{"projection_id": "p3", "source_id": "s3"}])
    links = store.evidence_for(record["id"])
    assert len(links) == 1
    assert links[0]["projection_id"] == "p3"
    assert links[0]["artifact_id"] is None
