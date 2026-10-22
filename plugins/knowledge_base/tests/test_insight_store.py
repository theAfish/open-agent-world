"""insight_store.py in isolation: drafts, evidence links, revisions, replacement."""
import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine  # noqa: E402

from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402
from oaw_knowledge_base.experiment_store import ConflictingRevision  # noqa: E402
from oaw_knowledge_base.insight_store import InsightStore  # noqa: E402


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    yield InsightStore(engine)
    engine.dispose()


def make(store, **overrides):
    values = {"schema_ids": ["sch1"], "kind": "anomaly", "title": "B7 moisture high",
              "statement": "Sample B7 moisture is 3.2σ above the batch mean.",
              "fields": ["moisture"], "confidence": 0.9, "properties": {"z": 3.2},
              "created_by": "agent:lab"}
    values.update(overrides)
    return store.create(**values)


def test_create_returns_a_draft_at_revision_one(store):
    insight = make(store)
    assert insight["status"] == "draft" and insight["revision"] == 1
    assert insight["schema_id"] == "sch1" and insight["schema_ids"] == ["sch1"]
    assert insight["fields"] == ["moisture"] and insight["properties"] == {"z": 3.2}
    assert insight["group_id"] is None and insight["created_at"].tzinfo is not None
    assert store.get(insight["id"]) == insight
    assert store.get("missing") is None
    with pytest.raises(KnowledgeError):
        store.require("missing")


def test_kind_and_status_are_restricted(store):
    with pytest.raises(KnowledgeError):
        make(store, kind="prophecy")
    insight = make(store)
    with pytest.raises(KnowledgeError):
        store.update(insight["id"], status="approved")
    with pytest.raises(KnowledgeError):
        store.update(insight["id"], kind="prophecy")


def test_list_filters(store):
    a = make(store, group_id="g1")
    make(store, kind="rule", schema_ids=["sch2"])
    cross = make(store, kind="correlation", schema_ids=["sch2", "sch1"], title="Across", statement="t rises with phase")
    store.update(a["id"], status="published")
    assert cross["schema_id"] is None and cross["schema_ids"] == ["sch1", "sch2"]
    assert {item["id"] for item in store.list(group_id="g1")} == {a["id"]}
    # A schema filter finds the insights that involve it, alone or with others.
    assert {item["kind"] for item in store.list(schema_id="sch2")} == {"rule", "correlation"}
    assert {item["id"] for item in store.list(status="published")} == {a["id"]}
    assert len(store.list(kind="anomaly")) == 1
    assert [item["id"] for item in store.list(text="PHASE")] == [cross["id"]]
    assert [item["id"] for item in store.list(text="moisture")] != []
    assert len(store.list()) == 3


def test_update_bumps_the_revision_and_can_clear_confidence(store):
    insight = make(store)
    updated = store.update(insight["id"], title="B7", confidence=None, note="checked",
                           expected_revision=1)
    assert updated["revision"] == 2 and updated["title"] == "B7"
    assert updated["confidence"] is None and updated["note"] == "checked"
    assert updated["statement"] == insight["statement"]
    with pytest.raises(ConflictingRevision):
        store.update(insight["id"], title="late", expected_revision=1)


def test_evidence_links_are_replaced_and_validated(store):
    insight = make(store)
    store.set_evidence(insight["id"], [{"record_id": "r1", "role": "observed_in"},
                                       {"record_id": "r2"}, {"record_id": "r1", "role": "supports"}])
    assert store.evidence_for(insight["id"]) == [{"record_id": "r1", "role": "observed_in"},
                                                 {"record_id": "r2", "role": "supports"}]
    with pytest.raises(KnowledgeError):
        store.set_evidence(insight["id"], [{"record_id": "r1", "role": "guesses"}])
    store.set_evidence(insight["id"], [])
    assert store.evidence_for(insight["id"]) == []


def test_published_citing_and_delete(store):
    insight = make(store)
    store.set_evidence(insight["id"], [{"record_id": "r1"}])
    assert store.published_citing("r1") == []
    store.update(insight["id"], status="published")
    assert store.published_citing("r1") == [insight["id"]]
    store.delete(insight["id"])
    assert store.get(insight["id"]) is None and store.evidence_for(insight["id"]) == []
    with pytest.raises(KnowledgeError):
        store.delete(insight["id"])


def test_delete_drafts_respects_schema_group_and_status(store):
    kept_published = make(store)
    store.update(kept_published["id"], status="published")
    grouped = make(store, group_id="g1")
    ungrouped = make(store)
    other_schema = make(store, schema_ids=["sch2"])
    cross = make(store, schema_ids=["sch1", "sch2"])

    removed = store.delete_drafts(schema_ids=["sch1"])
    assert removed == [ungrouped["id"]]
    for kept in (grouped, other_schema, cross):
        assert store.get(kept["id"]) is not None
    assert store.delete_drafts(schema_ids=["sch1"], group_id="g1") == [grouped["id"]]
    # The scope is the exact set: order does not matter, membership does.
    assert store.delete_drafts(schema_ids=["sch2", "sch1"]) == [cross["id"]]
    assert store.get(kept_published["id"]) is not None


def test_a_single_schema_insight_table_is_rebuilt_for_cross_schema_insights():
    from sqlalchemy import text

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE oaw_kb_insight (id VARCHAR(36) PRIMARY KEY, group_id VARCHAR(36), "
            "schema_id VARCHAR(36) NOT NULL, kind VARCHAR(32) NOT NULL, title VARCHAR(500) NOT NULL, "
            "statement TEXT NOT NULL, fields TEXT NOT NULL, confidence FLOAT, properties TEXT NOT NULL, "
            "status VARCHAR(32) NOT NULL, revision INTEGER NOT NULL, note TEXT, run_id VARCHAR(64), "
            "created_by VARCHAR(255), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        connection.execute(text("CREATE INDEX ix_oaw_insight_schema ON oaw_kb_insight (schema_id)"))
        connection.execute(text(
            "INSERT INTO oaw_kb_insight VALUES ('i1', NULL, 'sch1', 'note', 'old', 's', '[]', NULL, '{}', "
            "'published', 1, NULL, NULL, 'user:desktop', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
    store = InsightStore(engine)
    old = store.get("i1")
    assert old["schema_id"] == "sch1" and old["schema_ids"] == ["sch1"] and old["status"] == "published"
    cross = make(store, schema_ids=["sch1", "sch2"])
    assert cross["schema_id"] is None
    # Reopening does not rebuild again.
    InsightStore(engine)
    assert store.get("i1") is not None and store.get(cross["id"]) is not None
    engine.dispose()
