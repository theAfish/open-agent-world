"""knowledge_store.py in isolation: drafts over several sources, merging, revisions."""
import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine  # noqa: E402

from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402
from oaw_knowledge_base.experiment_store import ConflictingRevision  # noqa: E402
from oaw_knowledge_base.knowledge_prompt import normalize  # noqa: E402
from oaw_knowledge_base.knowledge_store import KnowledgeDraftStore  # noqa: E402


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    yield KnowledgeDraftStore(engine)
    engine.dispose()


def graph(*names, relations=(), extra=None):
    extra = extra or {}
    return normalize([{"name": name, **extra.get(name, {})} for name in names],
                     [{"source": a, "target": b, "predicate": "related_to", **extra.get((a, b), {})}
                      for a, b in relations])


def test_create_is_an_empty_draft_over_its_sources(store):
    draft = store.create(group_id="g1", title="Sintering papers", source_ids=["s1", "s2", "s1"])
    assert draft["status"] == "draft" and draft["revision"] == 1
    assert draft["entities"] == [] and draft["relations"] == []
    assert [item["source_id"] for item in store.sources_for(draft["id"])] == ["s1", "s2"]
    assert all(item["extracted_at"] is None for item in store.sources_for(draft["id"]))
    assert store.get("missing") is None
    with pytest.raises(KnowledgeError):
        store.create(group_id="g1", title="empty", source_ids=[])


def test_merge_unions_by_id_and_accumulates_source_evidence(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1", "s2"])
    entities, relations = graph("Si3N4", "Sintering", relations=[("Si3N4", "Sintering")])
    store.merge(draft["id"], source_id="s1", entities=entities, relations=relations)
    entities, relations = graph("Si3N4", "Density", relations=[("Si3N4", "Density")])
    draft = store.merge(draft["id"], source_id="s2", entities=entities, relations=relations)

    by_name = {item["name"]: item for item in draft["entities"]}
    assert set(by_name) == {"Si3N4", "Sintering", "Density"}
    assert by_name["Si3N4"]["source_ids"] == ["s1", "s2"]
    assert by_name["Density"]["source_ids"] == ["s2"]
    assert len(draft["relations"]) == 2 and draft["revision"] == 3
    assert all(item["extracted_at"] is not None for item in store.sources_for(draft["id"]))


def test_merge_unions_aliases_and_keeps_the_stronger_evidence(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1", "s2"])
    entities, relations = graph("LLZO", "Conductivity", relations=[("LLZO", "Conductivity")],
                                extra={"LLZO": {"aliases": ["Li7La3Zr2O12"]},
                                       ("LLZO", "Conductivity"): {"evidence_level": 4}})
    store.merge(draft["id"], source_id="s1", entities=entities, relations=relations)
    entities, relations = graph("LLZO", "Conductivity", relations=[("LLZO", "Conductivity")],
                                extra={"LLZO": {"aliases": ["garnet LLZO", "li7la3zr2o12"]},
                                       ("LLZO", "Conductivity"): {"evidence_level": 2, "snippet": "quoted"}})
    draft = store.merge(draft["id"], source_id="s2", entities=entities, relations=relations)
    assert draft["entities"][0]["aliases"] == ["Li7La3Zr2O12", "garnet LLZO"]
    assert draft["relations"][0]["evidence_level"] == 2 and draft["relations"][0]["snippet"] == "quoted"
    assert draft["relations"][0]["source_ids"] == ["s1", "s2"]


def test_a_fresh_draft_has_an_empty_review_log_that_update_can_append_to(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1"])
    assert draft["review_log"] == []
    updated = store.update(draft["id"], review_log=[{"at": "now", "operations": []}])
    assert updated["review_log"] == [{"at": "now", "operations": []}] and updated["revision"] == 2


def test_merge_drops_relations_whose_endpoints_are_not_in_the_draft(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1"])
    entities, _ = graph("A")
    draft = store.merge(draft["id"], source_id="s1", entities=entities, relations=[
        {"id": "concept:a|related_to|concept:zzz", "source": "concept:a",
         "target": "concept:zzz", "predicate": "related_to"}])
    assert draft["relations"] == []


def test_clear_forgets_elements_and_extraction_state(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1"])
    entities, _ = graph("A")
    store.merge(draft["id"], source_id="s1", entities=entities, relations=[])
    draft = store.clear(draft["id"])
    assert draft["entities"] == [] and store.sources_for(draft["id"])[0]["extracted_at"] is None


def test_update_replaces_lists_and_checks_the_revision(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1"])
    entities, _ = graph("A", "B")
    draft = store.merge(draft["id"], source_id="s1", entities=entities, relations=[])
    kept = [draft["entities"][0]]
    updated = store.update(draft["id"], title="renamed", entities=kept, expected_revision=draft["revision"])
    assert updated["title"] == "renamed" and updated["entities"] == kept
    with pytest.raises(ConflictingRevision):
        store.update(draft["id"], title="late", expected_revision=draft["revision"])
    with pytest.raises(KnowledgeError):
        store.update(draft["id"], status="approved")


def test_changing_the_source_set_keeps_shared_elements_and_drops_orphans(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1", "s2"])
    entities, _ = graph("Shared", "OnlyOne")
    store.merge(draft["id"], source_id="s1", entities=entities, relations=[])
    entities, _ = graph("Shared", "OnlyTwo")
    store.merge(draft["id"], source_id="s2", entities=entities, relations=[])
    draft = store.update(draft["id"], source_ids=["s2", "s3"])
    names = {item["name"]: item["source_ids"] for item in draft["entities"]}
    assert names == {"Shared": ["s2"], "OnlyTwo": ["s2"]}
    sources = {item["source_id"]: item["extracted_at"] for item in store.sources_for(draft["id"])}
    assert sources["s2"] is not None and sources["s3"] is None


def test_published_element_ids_exclude_the_named_draft(store):
    first = store.create(group_id="g1", title="a", source_ids=["s1"])
    second = store.create(group_id="g1", title="b", source_ids=["s2"])
    entities, _ = graph("Shared", "A")
    store.merge(first["id"], source_id="s1", entities=entities, relations=[])
    entities, _ = graph("Shared", "B")
    store.merge(second["id"], source_id="s2", entities=entities, relations=[])
    store.update(first["id"], status="published")
    store.update(second["id"], status="published")
    assert store.published_element_ids(except_draft_id=first["id"]) == {"concept:shared", "concept:b"}
    assert store.list(status="published", group_id="g1") and not store.list(group_id="other")


def test_delete_removes_the_draft_and_its_sources(store):
    draft = store.create(group_id="g1", title="t", source_ids=["s1"])
    store.delete(draft["id"])
    assert store.get(draft["id"]) is None and store.sources_for(draft["id"]) == []
    with pytest.raises(KnowledgeError):
        store.delete(draft["id"])
