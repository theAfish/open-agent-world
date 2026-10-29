"""Project/Space identity, snapshot boundaries, and optional result revision."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import uuid

import pytest
from sqlalchemy import create_engine

from oaw_knowledge_base import actions, client, projects, spaces
from oaw_knowledge_base.context import KnowledgeContext, MemorySettings
from oaw_knowledge_base.errors import KnowledgeError
from oaw_knowledge_base.experiment_store import ConflictingRevision
from oaw_knowledge_base.projection_store import ProjectionStore


TABLE = {"type": "object", "required": ["value"], "additionalProperties": False,
         "properties": {"value": {"type": "number"}}}


@pytest.fixture
def card(tmp_path):
    context = KnowledgeContext(str(uuid.uuid4()), tmp_path, state=MemorySettings())
    yield context
    client.close_client(context.node_id)


def kb_for(card):
    return client.open_client(card.node_id, card.storage_path)


def add_file(card, project, path="report.md", text="# Result\nValue: 42", *, convert=True):
    kb = kb_for(card)
    source = kb.sources.add_text(project["id"], text, filename=path.rsplit("/", 1)[-1],
                                 metadata={"relative_path": path})
    projects.register_project_file(kb, project["id"], source, path)
    if convert:
        jobs = actions.process_sources(card, {"source_ids": [str(source.id)]})["jobs"]
        assert kb.jobs.wait(jobs[0]["job"]["id"], timeout=30).status == "COMPLETED"
    return str(source.id)


def make_project(card, name="Report package"):
    project = projects.project_manage(card, {"operation": "create", "name": name})["project"]
    add_file(card, project)
    return project


def make_space(card, **values):
    return spaces.space_manage(card, {"operation": "create", "name": "Metrics", "definition": TABLE,
                                     "system_prompt": "Extract the reported value.", **values})["space"]


def prepare(card, space, *project_ids, **values):
    return spaces.projection_prepare(card, {"space_id": space["id"], "project_ids": list(project_ids), **values})["projections"]


def save(card, projection, data=None):
    return spaces.projection_save(card, {"projection_id": projection["id"], "data": data or {"value": 42},
                                        "expected_revision": projection["revision"]})["projection"]


def test_partial_batch_selection_reuses_project_results_and_markdown(card):
    first, second = make_project(card, "First"), make_project(card, "Second")
    si_id = add_file(card, first, "support/SI.md", "# Supplement\nUncertainty: 0.2")
    space = make_space(card)
    runs = prepare(card, space, first["id"], second["id"], first["id"])
    assert len(runs) == 2 and all(run["disposition"] == "created" for run in runs)
    prompt = spaces.projection_prompt(card, {"projection_id": runs[0]["id"], "limit": 25})
    text = prompt["markdown"]
    while prompt["has_more"]:
        prompt = spaces.projection_prompt(card, {"projection_id": runs[0]["id"], "offset": len(text), "limit": 25})
        text += prompt["markdown"]
    assert "Value: 42" in text and "support/SI.md" in text and "Uncertainty: 0.2" in text
    assert si_id in text
    assert prompt["space"]["version"] == 1
    save(card, runs[0])
    repeated = prepare(card, space, first["id"])[0]
    assert repeated["id"] == runs[0]["id"] and repeated["disposition"] == "reused"
    assert prepare(card, space, second["id"])[0]["disposition"] == "running"
    assert len(spaces.projections(card, {"include_history": True})["projections"]) == 2


def test_all_project_files_must_be_converted_before_any_claim(card):
    first, second = make_project(card, "Ready"), make_project(card, "Waiting")
    add_file(card, second, "supplement.md", convert=False)
    space = make_space(card)
    with pytest.raises(KnowledgeError, match="Convert all project files"):
        prepare(card, space, first["id"], second["id"])
    assert spaces.projections(card, {})["projections"] == []


def test_replaced_source_invalidates_running_result_but_keeps_original_prompt(card):
    project, space = make_project(card), make_space(card)
    old = prepare(card, space, project["id"])[0]
    add_file(card, project, text="# Corrected result\nValue: 84")
    with pytest.raises(KnowledgeError, match="files or conversions changed"):
        save(card, old)
    assert "Value: 42" in spaces.projection_prompt(card, {"projection_id": old["id"]})["markdown"]
    rerun = prepare(card, space, project["id"], rerun=True)[0]
    assert rerun["id"] != old["id"] and rerun["generation"] == 2
    assert "Value: 84" in spaces.projection_prompt(card, {"projection_id": rerun["id"]})["markdown"]
    with pytest.raises(ConflictingRevision):
        save(card, old)
    assert save(card, rerun, {"value": 84})["data"] == {"value": 84}


def test_space_revisions_are_snapshotted_and_cannot_accept_stale_extraction(card):
    project, space = make_project(card), make_space(card)
    run = prepare(card, space, project["id"])[0]
    updated = spaces.space_manage(card, {"operation": "update", "space_id": space["id"],
                                        "system_prompt": "Extract the corrected value."})["space"]
    assert updated["version"] == 2
    prompt = spaces.projection_prompt(card, {"projection_id": run["id"]})
    assert prompt["space"]["system_prompt"] == space["system_prompt"]
    with pytest.raises(KnowledgeError, match="Space configuration changed"):
        save(card, run)
    fresh = prepare(card, updated, project["id"], rerun=True)[0]
    assert fresh["space_version"] == 2


def test_optional_review_keeps_history_and_rejects_stale_revision(card):
    project, space = make_project(card), make_space(card)
    completed = save(card, prepare(card, space, project["id"])[0])
    prompt = spaces.projection_review_prompt(card, {"projection_id": completed["id"]})
    assert prompt["data"] == {"value": 42} and "optional review" in prompt["instruction"]
    with pytest.raises(ConflictingRevision):
        spaces.projection_revise(card, {"projection_id": completed["id"], "expected_revision": 1,
                                        "data": {"value": 43}, "note": "Correct value"})
    revised = spaces.projection_revise(card, {"projection_id": completed["id"],
        "expected_revision": completed["revision"], "data": {"value": 43}, "note": "Correct value"})["projection"]
    assert revised["parent_id"] == completed["id"] and revised["generation"] == 2
    assert prepare(card, space, project["id"])[0]["id"] == revised["id"]
    assert spaces.projections(card, {"operation": "get", "projection_id": completed["id"]})["projection"]["data"] == {"value": 42}
    with pytest.raises(ConflictingRevision):
        spaces.projection_revise(card, {"projection_id": completed["id"], "expected_revision": completed["revision"],
                                        "data": {"value": 44}, "note": "Obsolete view"})


def test_schema_validation_blocks_bad_data_and_external_references(card):
    project, space = make_project(card), make_space(card)
    run = prepare(card, space, project["id"])[0]
    for data in ({"value": "invented"}, {"value": 1, "extra": True}, {"value": float("nan")}):
        with pytest.raises(KnowledgeError):
            spaces.projection_save(card, {"projection_id": run["id"], "data": data})
    assert kb_for(card).oaw_projections.require(run["id"])["status"] == "running"
    with pytest.raises(KnowledgeError, match="local"):
        make_space(card, name="Remote", definition={"$ref": "https://example.test/schema.json"})
    # Read operations never accept writes, including from agents.
    with pytest.raises(ValueError):
        spaces.spaces(replace(card, actor_id="reader"), {"operation": "create", "name": "Unauthorized"})


def test_graph_rejects_foreign_evidence_and_unknown_endpoints(card):
    project, other = make_project(card), make_project(card, "Other")
    foreign = str(projects.project_sources(kb_for(card), other["id"])[0].id)
    space = make_space(card, kind="graph", definition=spaces.GRAPH_SCHEMA)
    run = prepare(card, space, project["id"])[0]
    data = {"entities": [{"id": "a", "name": "A", "source_ids": [foreign]}], "relations": []}
    with pytest.raises(KnowledgeError, match="files in this project"):
        save(card, run, data)
    data["entities"][0].pop("source_ids")
    data["relations"] = [{"source": "a", "target": "missing", "predicate": "contains"}]
    with pytest.raises(KnowledgeError, match="endpoints"):
        save(card, run, data)
    data["relations"] = []
    assert save(card, run, data)["data"] == data


def test_independent_clients_claim_one_run_concurrently(card):
    project, space = make_project(card), make_space(card)
    _, sources, fingerprint = spaces._snapshot(kb_for(card), project["id"])
    engines = [create_engine(f"sqlite:///{card.storage_path / client.FILE_NAME}", connect_args={"timeout": 30}) for _ in range(4)]
    stores = [ProjectionStore(engine) for engine in engines]
    barrier = threading.Barrier(4)
    def claim(store):
        barrier.wait()
        return store.prepare(project_id=project["id"], project_name=project["name"], space=space,
                             sources=sources, fingerprint=fingerprint, actor="test")
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            claims = list(executor.map(claim, stores))
        assert len({value[0]["id"] for value in claims}) == 1
        assert sorted(value[1] for value in claims) == ["created", "running", "running", "running"]
    finally:
        for engine in engines:
            engine.dispose()


def test_failed_run_can_retry_and_completed_receipt_survives_reopen(card):
    project, space = make_project(card), make_space(card)
    run = prepare(card, space, project["id"])[0]
    spaces.projection_fail(card, {"projection_id": run["id"], "error": "Model connection interrupted"})
    retry = prepare(card, space, project["id"])[0]
    assert retry["generation"] == 2 and retry["disposition"] == "created"
    completed = save(card, retry)
    assert save(card, retry)["id"] == completed["id"]
    client.close_client(card.node_id)
    assert prepare(card, space, project["id"])[0]["id"] == completed["id"]
    assert len(spaces.projections(card, {"include_history": True})["projections"]) == 2


def test_model_messages_use_original_markdown_and_support_array_result(card):
    project = make_project(card)
    space = make_space(card, definition={"type": "array", "items": {"type": "number"}})
    run = prepare(card, space, project["id"])[0]
    prompt = spaces.projection_prompt(card, {"projection_id": run["id"]})
    messages = spaces.build_messages(prompt)
    assert "Value: 42" in messages[1]["content"]
    data = spaces.parse_output(json.dumps({"data": [42]}))
    assert save(card, run, data)["data"] == [42]
    with pytest.raises(KnowledgeError, match="must return"):
        spaces.parse_output("[]")
