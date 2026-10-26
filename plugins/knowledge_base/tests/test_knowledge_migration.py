"""knowledge_migration.py: typed v1 ids collapse onto concept ids, in drafts and graph alike."""
import json
import uuid
from datetime import datetime, timezone

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, insert, select  # noqa: E402

from oaw_knowledge_base.graph_store import ENTITIES, RELATIONS, SqlGraphStore  # noqa: E402
from oaw_knowledge_base.knowledge_migration import (  # noqa: E402
    KNOWLEDGE_MODEL_VERSION, current_version, migrate_knowledge_model,
)
from oaw_knowledge_base.knowledge_store import DRAFTS, KnowledgeDraftStore  # noqa: E402


def key(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def v1_entity(old_id, type_, name, layer="literature", **properties):
    return {"id": key(old_id), "type": type_, "name": name, "properties": json.dumps(properties),
            "created_at": NOW, "updated_at": NOW, "group_id": "g1", "layer": layer}


def v1_relation(old_id, source, target, type_, layer="literature", **properties):
    return {"id": key(old_id), "source_id": key(source), "target_id": key(target), "type": type_,
            "properties": json.dumps(properties), "created_at": NOW, "updated_at": NOW,
            "group_id": "g1", "layer": layer}


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    SqlGraphStore(engine)
    KnowledgeDraftStore(engine)
    yield engine
    engine.dispose()


def seed_v1(engine):
    draft_entities = [
        {"id": "concept:method:xrd", "type": "method", "name": "XRD", "description": None, "source_ids": ["s1"]},
        {"id": "concept:concept:xrd", "type": "concept", "name": "XRD", "description": "diffraction", "source_ids": ["s2"]},
        {"id": "concept:quantity:density", "type": "quantity", "name": "Density", "description": None, "source_ids": ["s1"]},
    ]
    draft_relations = [
        {"id": "concept:quantity:density|measured_by|concept:method:xrd", "source": "concept:quantity:density",
         "target": "concept:method:xrd", "type": "measured_by", "source_ids": ["s1"]},
        {"id": "concept:quantity:density|measured_by|concept:concept:xrd", "source": "concept:quantity:density",
         "target": "concept:concept:xrd", "type": "measured_by", "source_ids": ["s2"]},
    ]
    with engine.begin() as connection:
        connection.execute(insert(DRAFTS).values(
            id="d1", group_id="g1", title="t", entities=json.dumps(draft_entities),
            relations=json.dumps(draft_relations), status="published", revision=3, run_id=None,
            created_by=None, created_at=NOW, updated_at=NOW))
        connection.execute(insert(ENTITIES), [
            v1_entity("concept:method:xrd", "method", "XRD", source_ids=["s1"]),
            v1_entity("concept:concept:xrd", "concept", "XRD", description="diffraction", source_ids=["s2"]),
            v1_entity("concept:quantity:density", "quantity", "Density"),
            v1_entity("insight:i1", "trend", "A trend", layer="experiment"),
        ])
        connection.execute(insert(RELATIONS), [
            v1_relation("concept:quantity:density|measured_by|concept:method:xrd",
                        "concept:quantity:density", "concept:method:xrd", "measured_by", source_ids=["s1"]),
            v1_relation("concept:quantity:density|measured_by|concept:concept:xrd",
                        "concept:quantity:density", "concept:concept:xrd", "measured_by", source_ids=["s2"]),
        ])


def test_typed_nodes_collapse_in_drafts_and_graph_with_matching_ids(engine):
    seed_v1(engine)
    assert current_version(engine) == 1
    result = migrate_knowledge_model(engine)
    assert result == {"drafts": 1, "entities": 2, "relations": 1}
    assert current_version(engine) == KNOWLEDGE_MODEL_VERSION

    draft = KnowledgeDraftStore(engine).require("d1")
    by_id = {item["id"]: item for item in draft["entities"]}
    assert set(by_id) == {"concept:xrd", "concept:density"}
    assert by_id["concept:xrd"]["description"] == "diffraction" and by_id["concept:xrd"]["source_ids"] == ["s1", "s2"]
    assert draft["relations"] == [{"id": "concept:density|measured_by|concept:xrd", "source": "concept:density",
                                   "target": "concept:xrd", "predicate": "measured_by", "description": None,
                                   "evidence_level": 3, "snippet": None, "source_ids": ["s1", "s2"]}]

    with engine.connect() as connection:
        entities = connection.execute(select(ENTITIES).order_by(ENTITIES.c.name)).mappings().all()
        relations = connection.execute(select(RELATIONS)).mappings().all()
    literature = [row for row in entities if row["layer"] == "literature"]
    # Graph ids are what unpublishing the draft will look for.
    assert {row["id"] for row in literature} == {key(item["id"]) for item in draft["entities"]}
    assert all(row["type"] == "concept" for row in literature)
    xrd = next(row for row in literature if row["name"] == "XRD")
    assert json.loads(xrd["properties"]) == {"aliases": [], "description": "diffraction", "source_ids": ["s1", "s2"]}
    assert len(relations) == 1 and relations[0]["id"] == key(draft["relations"][0]["id"])
    assert relations[0]["source_id"] == key("concept:density") and relations[0]["target_id"] == key("concept:xrd")
    # The experiment layer is not touched.
    assert any(row["layer"] == "experiment" and row["type"] == "trend" for row in entities)


def test_a_second_run_is_a_no_op(engine):
    seed_v1(engine)
    migrate_knowledge_model(engine)
    assert migrate_knowledge_model(engine) is None
