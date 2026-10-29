"""End-to-end pipeline: upload, convert, extract, review, graph — with no host at all.

The suite drives the same handlers the OAW card and the service call, through the
plain :class:`~oaw_knowledge_base.context.KnowledgeContext`, so it runs in any venv
that has ``mat-know-base`` installed. ``cd plugins/knowledge_base && pytest``.
"""
import base64
import json
import uuid
from dataclasses import replace

import pytest

pytest.importorskip("mkb", reason="Install mat-know-base into this environment")
pytest.importorskip("sqlalchemy")

from oaw_knowledge_base import actions, client, insights, projects, spaces  # noqa: E402
from oaw_knowledge_base.context import KnowledgeContext, MemorySettings  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError
from oaw_knowledge_base.experiment_store import ConflictingRevision  # noqa: E402

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


def _ingest(context, arguments):
    values = dict(arguments)
    project_id = values.pop("group_id", None)
    file = projects.ImportFile.model_validate(values)
    preview = projects.project_import_preview(context, {"files": [file.model_dump()], "grouping": "single"})
    target = {"key": "__all__", "name": file.filename}
    if project_id:
        target["project_id"] = project_id
    imported = projects.project_import_commit(context, {"token": preview["token"], "grouping": "single",
        "projects": [target], "auto_process": False})
    kb = client.open_client(context.node_id, context.storage_path)
    source = kb.sources.require(imported["source_ids"][0])
    return {"source": {"id": str(source.id), "filename": source.filename,
        "size": source.size, "sha256": source.sha256}, "group_id": imported["projects"][0]["id"]}


def ingest_document(context, text=DOCUMENT, filename="sintering.md"):
    ingested = run(context, _ingest, filename=filename,
                   content_base64=base64.b64encode(text.encode()).decode(),
                   media_type="text/markdown")
    source_id = ingested["source"]["id"]
    processed = run(context, actions.process_sources, source_ids=[source_id])
    kb = client.open_client(context.node_id, context.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return source_id, job


def test_agent_schema_writes_require_the_management_operation(card):
    agent = replace(card, actor_id="researcher")
    body = {"operation": "create", "name": "Sample", "definition": SCHEMA, "system_prompt": "Read samples."}
    with pytest.raises(KnowledgeError, match="read-only"):
        actions.schemas(agent, body)
    created = actions.schema_manage(agent, body)["schema"]
    with pytest.raises(KnowledgeError, match="read-only"):
        run(agent, actions.schemas, operation="update", schema_id=created["id"], name="Changed")
    changed = run(agent, actions.schema_manage, operation="update", schema_id=created["id"], name="Changed")["schema"]
    assert run(agent, actions.schemas, operation="get", schema_id=created["id"])["schema"] == changed
    # Desktop and deployed read surfaces obey the same read-only contract.
    with pytest.raises(KnowledgeError, match="read-only"):
        run(card, actions.schemas, operation="update", schema_id=created["id"], name="Desktop")
    assert run(card, actions.schema_manage, operation="update", schema_id=created["id"], name="Desktop")["schema"]["name"] == "Desktop"


def test_agent_cannot_change_the_schema_of_published_records(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "A1 conductivity 1.0", "batch.md")
    record = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.0}}])["records"][0]
    card.confirmed = True
    run(card, actions.experiment_review, operation="publish", record_id=record["id"])
    agent = replace(card, actor_id="researcher", confirmed=False)
    for patch in ({"definition": {"type": "object", "properties": {}}}, {"system_prompt": "Changed extraction rule"}):
        with pytest.raises(KnowledgeError, match="published records"):
            run(agent, actions.schema_manage, operation="update", schema_id=schema["id"], **patch)
    assert run(agent, actions.schemas, operation="get", schema_id=schema["id"])["schema"] == schema


def test_agent_conversion_requires_selected_sources_and_leaves_other_uploads_pending(card):
    agent = replace(card, actor_id="researcher")
    selected = run(card, _ingest, filename="chosen.md", content_base64=base64.b64encode(DOCUMENT.encode()).decode())["source"]
    pending = run(card, _ingest, filename="later.md", content_base64=base64.b64encode(b"Later document").decode())["source"]
    for values in ({}, {"source_ids": []}):
        with pytest.raises(ValueError):
            actions.process_selected(agent, values)
    result = run(agent, actions.process_selected, source_ids=[selected["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    assert kb.jobs.wait(result["jobs"][0]["job"]["id"], timeout=60).status == "COMPLETED"
    assert "Si3N4" in run(agent, actions.markdown, source_id=selected["id"])["markdown"]
    assert not kb.artifacts.list(source_id=pending["id"], limit=10)
    assert run(agent, actions.process_selected, source_ids=[selected["id"]])["jobs"][0]["skipped"] == "already converted"


def test_agent_conversion_retries_a_failed_pdf_with_corrected_engine_settings(card):
    import pymupdf

    with pymupdf.open() as document:
        document.new_page().insert_text((50, 50), "Measured conductivity 1.0 S/cm")
        data = document.tobytes()
    run(card, actions.update_settings, pdf_engine="mineru", mineru_base_url="")
    source = run(card, _ingest, filename="measurement.pdf", media_type="application/pdf",
                 content_base64=base64.b64encode(data).decode())["source"]
    agent = replace(card, actor_id="researcher")
    kb = client.open_client(card.node_id, card.storage_path)
    failed = run(agent, actions.process_selected, source_ids=[source["id"]])["jobs"][0]["job"]
    failure = kb.jobs.wait(failed["id"], timeout=60)
    assert failure.status == "FAILED" and "No MinerU base URL" in failure.error
    run(card, actions.update_settings, pdf_engine="pymupdf4llm")
    retried = run(agent, actions.process_selected, source_ids=[source["id"]])["jobs"][0]["job"]
    assert retried["id"] != failed["id"]
    completed = kb.jobs.wait(retried["id"], timeout=60)
    assert completed.status == "COMPLETED" and completed.parameters["engine"] == "pymupdf4llm"
    assert "Measured conductivity" in run(agent, actions.markdown, source_id=source["id"])["markdown"]
    assert kb.jobs.require(failed["id"]).status == "FAILED"


@pytest.mark.parametrize("older_job", [False, True], ids=["recent-job", "next-page-job"])
def test_agent_conversion_resumes_an_index_failure_without_duplicating_the_artifact(card, monkeypatch, older_job):
    from oaw_knowledge_base import search_store

    index = search_store.index_record
    unavailable = True

    def temporarily_unavailable(*args, **kwargs):
        if unavailable:
            raise KnowledgeError("Index temporarily unavailable")
        return index(*args, **kwargs)

    monkeypatch.setattr(search_store, "index_record", temporarily_unavailable)
    source = run(card, _ingest, filename="notes.md", content_base64="IyBOb3Rlcw==")["source"]
    agent = replace(card, actor_id="researcher")
    kb = client.open_client(card.node_id, card.storage_path)
    failed = run(agent, actions.process_selected, source_ids=[source["id"]])["jobs"][0]["job"]
    assert kb.jobs.wait(failed["id"], timeout=60).status == "FAILED"
    assert run(agent, actions.markdown, source_id=source["id"])["markdown"] == "# Notes"
    pages = []
    if older_job:
        failed_job = kb.jobs.require(failed["id"])
        newer = [failed_job.model_copy(update={"id": uuid.uuid4(), "inputs": {"source_id": str(uuid.uuid4())}})
                 for _ in range(200)]

        def paged_history(*, limit, offset=0):
            pages.append(offset)
            return (newer + [kb.jobs.require(failed["id"])])[offset:offset + limit]

        monkeypatch.setattr(kb.jobs, "list", paged_history)
    unavailable = False
    # Converter changes do not invalidate the completed conversion checkpoint.
    run(card, actions.update_settings, pdf_engine="text")
    selected = [source["id"], source["id"]] if older_job else [source["id"]]
    resumed_jobs = run(agent, actions.process_selected, source_ids=selected)["jobs"]
    assert len(resumed_jobs) == 1
    resumed = resumed_jobs[0]["job"]
    assert resumed["id"] == failed["id"]
    if older_job:
        assert pages == [0, 200]
    completed = kb.jobs.wait(resumed["id"], timeout=60)
    assert completed.status == "COMPLETED" and completed.parameters["engine"] == "auto"
    assert run(agent, actions.search, query="Notes")["results"][0]["source_id"] == source["id"]
    assert len(kb.artifacts.list(source_id=source["id"], limit=10)) == 1


def test_agent_bundle_preparation_reuses_drafts_and_refuses_published_or_destructive_changes(card):
    schema = _experiment_schema(card)
    agent = replace(card, actor_id="researcher")
    arguments = {"schema_id": schema["id"], "name": "Batch", "notes": "Measured conductivity"}
    bundle = actions.experiment_bundle_prepare(agent, arguments)["bundle"]
    assert actions.experiment_bundle_prepare(agent, arguments)["bundle"]["id"] == bundle["id"]
    _upload(card, bundle, "A1 1.0\n", "batch.md")
    saved = run(agent, actions.experiment_save, bundle_id=bundle["id"],
                records=[{"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.0}}])["records"][0]
    assert run(agent, actions.experiment_bundle_prepare, operation="update", bundle_id=bundle["id"],
               schema_id=schema["id"], name="Batch reviewed")["bundle"]["name"] == "Batch reviewed"
    for extra in ({"operation": "delete"}, {"status": "extracted"}, {"run_id": "forged"}):
        with pytest.raises(ValueError):
            actions.experiment_bundle_prepare(agent, {"bundle_id": bundle["id"], **extra})
    card.confirmed = True
    run(card, actions.experiment_review, operation="publish", record_id=saved["id"])
    with pytest.raises(KnowledgeError, match="published records"):
        run(agent, actions.experiment_bundle_prepare, operation="update", bundle_id=bundle["id"], notes="Changed")


def test_agent_index_build_is_separate_from_status_and_status_does_not_write_failures(card):
    from oaw_knowledge_base.operations import BY_NAME

    agent = replace(card, actor_id="researcher")
    with pytest.raises(KnowledgeError, match="read-only"):
        run(agent, actions.graph_index, operation="build")
    assert run(agent, actions.graph_index_build)["enabled"] is False
    with pytest.raises(ValueError):
        BY_NAME["graph_index"].model.model_validate({"operation": "build"})
    run(card, actions.update_settings, embedding_model="oaw:model:missing")
    before = card.state.get()
    assert run(agent, actions.graph_index)["last_error"]
    assert card.state.get() == before


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
    source = run(card, _ingest, filename="paper.pdf", media_type="application/pdf",
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
    alloys = run(card, projects.project_manage, operation="create", name="Alloys")["project"]
    ingested = run(card, _ingest, filename="alloy.md",
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
    assert found[0]["group_name"] == "sintering.md"
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
        run(card, actions.schema_manage, operation="create", name="bad",
            definition={"type": "string"}, system_prompt="x")


def test_experiment_schemas_and_projection_spaces_have_separate_contracts(card):
    experiment = run(card, actions.schema_manage, operation="create", name="Synthesis run",
                     definition=SCHEMA, system_prompt="x")["schema"]
    assert experiment["kind"] == "experiment"
    space = run(card, spaces.space_manage, operation="create", name="Report table", kind="table",
                definition=SCHEMA, system_prompt="Extract a report.")["space"]
    assert [item["name"] for item in run(card, actions.schemas)["schemas"]] == ["Synthesis run"]
    assert [item["name"] for item in run(card, spaces.spaces)["spaces"]] == ["Report table"]
    with pytest.raises(KnowledgeError):
        _bundle(card, space)
    with pytest.raises(ValueError):
        run(card, actions.schema_manage, operation="update", schema_id=experiment["id"], kind="literature")


def test_a_name_query_returns_every_relation_touching_the_concept_with_names_and_statements(card):
    """What the Lab assistant asks: "what does the base say about DPA-SSE and NEB?"."""
    first, _ = ingest_document(card, "# Paper\n\nDPA-SSE and NEB.", "dpa.md")
    entities, relations = (
        [{"name": "DPA-SSE", "aliases": ["DPA SSE model"], "description": "A pre-trained deep potential for sulfide electrolytes."},
         {"name": "Hopping barrier"}, {"name": "NEB", "aliases": ["nudged elastic band"]}, {"name": "Li7La3Zr2O12"}],
        [{"source": "DPA-SSE", "target": "Hopping barrier", "predicate": "computes",
          "description": "DPA-SSE computes the Li+ hopping barrier of the electrolyte.", "evidence_level": 2,
          "snippet": "the hopping barrier computed with DPA-SSE"},
         {"source": "NEB", "target": "DPA-SSE", "predicate": "used with",
          "description": "NEB calculations are driven by DPA-SSE forces to find the migration path."},
         {"source": "Li7La3Zr2O12", "target": "Hopping barrier", "predicate": "has property"}])
    kb = client.open_client(card.node_id, card.storage_path)
    payload = {"entities": [{"type": "concept", "name": entity["name"], "properties": {
        **{key: value for key, value in entity.items() if key != "name"}, "source_ids": [first]}}
        for entity in entities], "relations": [{"source": relation["source"], "target": relation["target"],
        "type": relation["predicate"].replace(" ", "_"), "properties": {
            **{key: value for key, value in relation.items() if key not in {"source", "target", "predicate"}},
            "source_ids": [first]}} for relation in relations]}
    kb.graph.extract(payload, extractor=lambda value: value)

    # One name, spelled loosely: the concept, both relations touching it, and the
    # concepts on their other ends — each relation readable without joining ids.
    # full=True is the UI's view with every property; the agent's compact view is
    # covered by its own test below.
    found = run(card, actions.graph, names=["dpa sse"], full=True)
    assert found["matched"] == 1 and found["truncated"] is False
    assert [(item["name"], item["matched"]) for item in found["entities"]] == [
        ("DPA-SSE", True), ("Hopping barrier", False), ("NEB", False)]
    assert found["entities"][0]["aliases"] == ["DPA SSE model"] and found["entities"][0]["description"].startswith("A pre-trained")
    statements = {(item["source_name"], item["predicate"], item["target_name"]): item for item in found["relations"]}
    assert set(statements) == {("DPA-SSE", "computes", "Hopping barrier"), ("NEB", "used_with", "DPA-SSE")}
    computes = statements[("DPA-SSE", "computes", "Hopping barrier")]
    assert computes["statement"] == "DPA-SSE computes the Li+ hopping barrier of the electrolyte."
    assert computes["evidence_level"] == 2 and computes["snippet"] == "the hopping barrier computed with DPA-SSE"
    assert computes["source_ids"] == [first] and computes["properties"]["description"] == computes["statement"]

    # Two names, one by alias: the relation between them comes back.
    both = run(card, actions.graph, names=["DPA-SSE", "nudged elastic band"])
    assert both["matched"] == 2
    assert ("NEB", "used_with", "DPA-SSE") in {(r["source_name"], r["predicate"], r["target_name"]) for r in both["relations"]}

    # A word in a statement finds the relation even when no concept carries that name.
    hopping = run(card, actions.graph, relation_contains="migration path")
    assert [(r["source_name"], r["target_name"]) for r in hopping["relations"]] == [("NEB", "DPA-SSE")]
    assert sorted(item["name"] for item in hopping["entities"]) == ["DPA-SSE", "NEB"]

    # A single search term includes incoming and outgoing claims.
    single_term = run(card, actions.graph, name_contains="DPA-SSE", full=True)
    assert len(single_term["relations"]) == 2
    # Nothing matched: an honest empty answer, not a guess.
    assert run(card, actions.graph, names=["graphene"]) == {
        "entities": [], "relations": [], "matched": 0, "truncated": False, "connections": [], "search_text": "graphene"}
    # An unfiltered query still lists everything, unflagged.
    everything = run(card, actions.graph, layer="literature")
    assert everything["matched"] is None and len(everything["entities"]) == 4 and len(everything["relations"]) == 3
    assert all("matched" not in item for item in everything["entities"])
    # Traversal reads the same way.
    start = found["entities"][0]["id"]
    walked = run(card, actions.graph, operation="traverse", entity_id=start)
    assert {r["source_name"] for r in walked["relations"]} == {"DPA-SSE", "NEB"}
    assert next(item for item in walked["entities"] if item["id"] == start)["matched"] is True


def _hub_graph(card):
    kb = client.open_client(card.node_id, card.storage_path)
    entities = [{"type": "model", "name": "DPA-SSE", "aliases": ["DPA SSE"]},
                {"type": "method", "name": "Nudged Elastic Band"},
                {"type": "property", "name": "Li-ion migration barrier"},
                {"type": "material", "name": "Assessed class"}]
    entities += [{"type": "material", "name": f"Li{i}PS"} for i in range(60)]
    relations = [{"source": "Nudged Elastic Band", "target": "Li-ion migration barrier", "type": "computes",
                  "properties": {"description": "NEB computes the migration barrier."}},
                 {"source": "DPA-SSE", "target": "Li-ion migration barrier", "type": "predicts"}]
    relations += [{"source": "DPA-SSE", "target": f"Li{i}PS", "type": "trained_on"} for i in range(60)]
    kb.graph.extract({"entities": entities, "relations": relations}, extractor=lambda payload: payload)


def test_loose_names_acronyms_and_a_question_find_concepts_a_literal_substring_missed(card):
    _hub_graph(card)

    # A typed name may carry extra words, and an acronym finds the spelled-out concept.
    found = run(card, actions.graph, names=["DPA-SSE model", "NEB"])
    assert {e["name"] for e in found["entities"] if e["matched"]} == {"DPA-SSE", "Nudged Elastic Band"}
    # A short acronym matches whole words only, not the middle of "Assessed".
    assert {e["name"] for e in run(card, actions.graph, names=["SSE"])["entities"] if e["matched"]} == {"DPA-SSE"}

    answer = run(card, actions.graph, query="What is the relation between DPA-SSE and NEB based on the graph?")
    assert {e["name"] for e in answer["entities"] if e["matched"]} == {"DPA-SSE", "Nudged Elastic Band"}
    # The 60-relation hub does not bury the evidence: the statement mentioning NEB leads.
    assert answer["relations"][0]["predicate"] == "computes"
    # And the answer says how the two concepts connect, through the shared barrier.
    chain = answer["connections"][0]
    assert chain["length"] == 2 and {chain["from"], chain["to"]} == {"DPA-SSE", "Nudged Elastic Band"}
    assert all(step.endswith("→ Li-ion migration barrier") for step in chain["steps"])

    assert run(card, actions.graph, query="graphene oxide") == {
        "entities": [], "relations": [], "matched": 0, "truncated": False, "connections": [], "search_text": "graphene oxide"}


def test_connect_returns_the_shortest_relation_chains_between_two_named_concepts(card):
    _hub_graph(card)

    linked = run(card, actions.graph, operation="connect", names=["DPA-SSE", "NEB"])
    assert linked["connected"] is True and linked["resolved"] == [["DPA-SSE"], ["Nudged Elastic Band"]]
    [path] = linked["paths"]
    assert path["length"] == 2
    assert path["steps"] == ["DPA-SSE —predicts→ Li-ion migration barrier",
                             "Nudged Elastic Band —computes→ Li-ion migration barrier"]
    assert [(r["source_name"], r["predicate"]) for r in linked["relations"]] == [
        ("DPA-SSE", "predicts"), ("Nudged Elastic Band", "computes")]
    # The UI's full view carries the relations and every endpoint in each path.
    full = run(card, actions.graph, operation="connect", names=["DPA-SSE", "NEB"], full=True)
    assert [(r["source_name"], r["predicate"], r["target_name"]) for r in full["paths"][0]["relations"]] == [
        ("DPA-SSE", "predicts", "Li-ion migration barrier"),
        ("Nudged Elastic Band", "computes", "Li-ion migration barrier")]
    assert {"Li-ion migration barrier"} <= {e["name"] for e in full["entities"]}

    # Too shallow a walk, an unknown name and a wrong name count each say so plainly.
    assert run(card, actions.graph, operation="connect", names=["DPA-SSE", "NEB"], max_depth=1)["connected"] is False
    assert run(card, actions.graph, operation="connect", names=["DPA-SSE", "graphene"])["unresolved"] == ["graphene"]
    with pytest.raises(KnowledgeError):
        run(card, actions.graph, operation="connect", names=["DPA-SSE"])


def _embedding_card(card, calls):
    """A context whose embedding model is a fake: one axis for 'ionic conduction' words."""
    from types import SimpleNamespace

    def resolve(reference):
        return SimpleNamespace(adapter="openai", base_url="http://embed.test", api_key="k", model_id="openai/emb")

    state = MemorySettings({"settings": {"embedding_model": "oaw:model:emb"}})
    return KnowledgeContext(card.node_id, card.storage_path, state=state, resolve_model=resolve)


def _fake_embed(calls):
    axis = ("conductor", "ionic", "transport")

    def embed(texts, connection, **kwargs):
        calls.append(list(texts))
        return [[1.0 if any(word in text.lower() for word in axis) else 0.0, 0.1] for text in texts]
    return embed


def _semantic_graph(card):
    kb = client.open_client(card.node_id, card.storage_path)
    entities = [{"type": "material", "name": "LLZO", "description": "garnet-type lithium conductor"}]
    entities += [{"type": "material", "name": f"Cu{i}"} for i in range(11)]
    relations = [{"source": "LLZO", "target": "Cu0", "type": "doped_with"}]
    relations += [{"source": f"Cu{i}", "target": f"Cu{i + 1}", "type": "alloyed_with"} for i in range(10)]
    kb.graph.extract({"entities": entities, "relations": relations}, extractor=lambda payload: payload)
    return kb


def test_an_embedding_model_finds_a_concept_the_words_missed_and_only_embeds_what_changed(card, monkeypatch):
    from oaw_knowledge_base import embeddings

    kb = _semantic_graph(card)
    calls = []
    monkeypatch.setattr(embeddings, "embed", _fake_embed(calls))
    question = "which material shows fast ionic transport?"

    # Off by default: the words share nothing with the description, and the answer says nothing of embeddings.
    off = run(card, actions.graph, query=question)
    assert off["entities"] == [] and "semantic" not in off

    ctx = _embedding_card(card, calls)
    on = run(ctx, actions.graph, query=question)
    assert on["semantic"] is True
    assert [e["name"] for e in on["entities"] if e["matched"]] == ["LLZO"]
    assert sum(len(batch) for batch in calls) == 23 + 1  # 12 concepts + 11 relations, then the question

    # Unchanged elements are not embedded again; an edited description re-embeds exactly that concept.
    calls.clear()
    run(ctx, actions.graph, query=question)
    assert calls == [[question]]
    calls.clear()
    llzo = next(e for e in kb.oaw_graph_store.list_entities() if e.name == "LLZO")
    kb.oaw_graph_store.upsert_entity(llzo.model_copy(update={"properties": {
        **llzo.properties, "description": "garnet-type lithium oxide, a fast ionic conductor"}}))
    run(ctx, actions.graph, query=question)
    assert len(calls) == 2 and len(calls[0]) == 1 and "fast ionic conductor" in calls[0][0]

    # An unrelated question stands out from nothing, so it matches nothing.
    assert run(ctx, actions.graph, query="graphene sheets")["entities"] == []


def test_search_falls_back_to_words_and_says_why_when_the_embedding_model_cannot_be_used(card, monkeypatch):
    from oaw_knowledge_base import embeddings

    _semantic_graph(card)

    def unreachable(texts, connection, **kwargs):
        raise KnowledgeError("Embedding model request failed (HTTP 503)")

    monkeypatch.setattr(embeddings, "embed", unreachable)
    ctx = _embedding_card(card, [])
    failed = run(ctx, actions.graph, query="LLZO")
    assert failed["semantic"] is False and "HTTP 503" in failed["semantic_error"]
    assert [e["name"] for e in failed["entities"] if e["matched"]] == ["LLZO"]

    # No host model resolver (the standalone service) and a non-OpenAI connection are refused plainly.
    bare = KnowledgeContext(card.node_id, card.storage_path, state=ctx.state)
    assert "cannot resolve" in run(bare, actions.graph, query="LLZO")["semantic_error"]
    wrong = KnowledgeContext(card.node_id, card.storage_path, state=ctx.state,
                             resolve_model=lambda ref: __import__("types").SimpleNamespace(
                                 adapter="anthropic", base_url="", api_key="k", model_id="claude"))
    assert "OpenAI-compatible" in run(wrong, actions.graph, query="LLZO")["semantic_error"]

    saved = run(card, actions.update_settings, embedding_model="oaw:model:emb")
    assert saved["settings"]["embedding_model"] == "oaw:model:emb"

    # Whatever else goes wrong inside the optional channel, the word-based answer still comes back.
    def broken(texts, connection, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(embeddings, "embed", broken)
    survived = run(ctx, actions.graph, query="LLZO")
    assert survived["semantic"] is False and survived["semantic_error"] == "boom"
    assert [e["name"] for e in survived["entities"] if e["matched"]] == ["LLZO"]


def test_the_embeddings_request_batches_inputs_and_restores_their_order(monkeypatch):
    import httpx
    from oaw_knowledge_base import embeddings

    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((str(request.url), body["model"], len(body["input"]), request.headers["authorization"]))
        # A provider may answer out of order; ``index`` is the contract.
        data = [{"index": i, "embedding": [float(len(text)), 0.0]} for i, text in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data[::-1]})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    texts = ["a" * n for n in range(1, 24)]
    vectors = embeddings.embed(texts, ("http://embed.test/v1/", "k", "emb"))
    assert [item[2] for item in seen] == [10, 10, 3] and seen[0][:2] == ("http://embed.test/v1/embeddings", "emb")
    assert seen[0][3] == "Bearer k" and all(v == [1.0, 0.0] for v in vectors)

    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "bad key"}})), **kw))
    with pytest.raises(KnowledgeError, match="HTTP 401: bad key"):
        embeddings.embed(["x"], ("http://embed.test", "k", "emb"))


def test_every_lookup_form_runs_the_same_hybrid_search_so_no_parameter_bypasses_the_embedding_model(card, monkeypatch):
    """The flaw: the assistant passed names=/name_contains= and semantic search never ran."""
    from oaw_knowledge_base import embeddings

    _semantic_graph(card)
    calls = []
    monkeypatch.setattr(embeddings, "embed", _fake_embed(calls))
    ctx = _embedding_card(card, calls)

    # names= : the typed name is a strong match AND the question is embedded.
    by_name = run(ctx, actions.graph, names=["ionic transport"])
    assert by_name["semantic"] is True and by_name["search_text"] == "ionic transport"
    assert [e["name"] for e in by_name["entities"] if e["matched"]] == ["LLZO"]
    assert calls[-1] == ["ionic transport"]
    # name_contains= and relation_contains= : same path, same answer.
    for form in ({"name_contains": "ionic transport"}, {"relation_contains": "ionic transport"}):
        result = run(ctx, actions.graph, **form)
        assert result["semantic"] is True and [e["name"] for e in result["entities"] if e["matched"]] == ["LLZO"]
    # Mixed: names and a question are searched together.
    mixed = run(ctx, actions.graph, query="which has ionic transport", names=["Cu3"])
    assert mixed["search_text"] == "which has ionic transport Cu3"
    assert {e["name"] for e in mixed["entities"] if e["matched"]} == {"Cu3", "LLZO"}

    # The graph map's filter opts out: no model call, lexical only.
    calls.clear()
    plain = run(ctx, actions.graph, name_contains="ionic transport", semantic=False)
    assert calls == [] and "semantic" not in plain and plain["matched"] == 0
    # The unfiltered listing never embeds either.
    listing = run(ctx, actions.graph)
    assert calls == [] and listing["matched"] is None and len(listing["entities"]) == 12


def test_the_agent_tool_schema_hides_the_lexical_knobs_but_the_ui_and_cli_may_still_pass_them():
    from oaw_knowledge_base.operations import BY_NAME

    schema = BY_NAME["graph"].input_schema()
    assert {"query", "names", "operation", "entity_id", "max_depth", "layer", "group_id", "limit"} <= set(schema["properties"])
    assert not {"name_contains", "relation_contains", "entity_type", "relation_type", "semantic"} & set(schema["properties"])
    assert actions.GraphQuery.model_validate({"name_contains": "x", "semantic": False, "entity_type": "material"})
    assert "name_contains" not in BY_NAME["search"].input_schema()["properties"]  # untouched models unaffected
    assert BY_NAME["graph_index"].tool_name == "knowledge_graph_index"


def test_the_semantic_index_reports_coverage_and_builds_on_request(card, monkeypatch):
    from oaw_knowledge_base import embeddings

    _semantic_graph(card)
    calls = []
    monkeypatch.setattr(embeddings, "embed", _fake_embed(calls))

    # Off: nothing to report but the size of the graph.
    off = run(card, actions.graph_index)
    assert off["enabled"] is False and off["total"] == 23 and off["embedded"] == 0

    ctx = _embedding_card(card, calls)
    before = run(ctx, actions.graph_index)
    assert before["enabled"] is True and before["model_id"] == "emb"
    assert (before["embedded"], before["pending"], before["stale"]) == (0, 23, 0) and calls == []

    built = run(ctx, actions.graph_index, operation="build")
    assert (built["embedded"], built["pending"], built["stale"]) == (23, 0, 0) and built["built_at"]
    assert sum(len(batch) for batch in calls) == 23
    # A search afterwards embeds only the question.
    calls.clear()
    run(ctx, actions.graph, query="ionic transport")
    assert calls == [["ionic transport"]]

    # An edited description shows as stale until the next build.
    kb = client.open_client(card.node_id, card.storage_path)
    llzo = next(e for e in kb.oaw_graph_store.list_entities() if e.name == "LLZO")
    kb.oaw_graph_store.upsert_entity(llzo.model_copy(update={"properties": {**llzo.properties, "description": "edited"}}))
    assert run(ctx, actions.graph_index)["stale"] == 1

    # A failing build keeps the last error for the Settings panel and the assistant.
    def broken(texts, connection, **kwargs):
        raise KnowledgeError("Embedding model request failed (HTTP 503)")

    monkeypatch.setattr(embeddings, "embed", broken)
    failed = run(ctx, actions.graph_index, operation="build")
    assert "HTTP 503" in failed["last_error"] and failed["embedded"] == 22
    assert "HTTP 503" in run(ctx, actions.graph_index)["last_error"]
    # ...and a failed search records it the same way.
    assert run(ctx, actions.graph, query="ionic transport")["semantic"] is False
    # A model that cannot be resolved is a reported error, not a crash.
    bare = KnowledgeContext(card.node_id, card.storage_path, state=ctx.state)
    assert "cannot resolve" in run(bare, actions.graph_index)["last_error"]


def test_a_cosine_floor_stops_a_tight_low_population_from_producing_hits():
    from oaw_knowledge_base import embeddings

    # Ten vectors clustered around cos 0.30 to the query, one at 0.40: two standard
    # deviations above the mean, yet nothing a person would call related.
    query = [1.0, 0.0]
    population = {f"d{i}": [0.30, (1 - 0.30 ** 2) ** 0.5] for i in range(10)}
    population["best"] = [0.40, (1 - 0.40 ** 2) ** 0.5]
    assert embeddings.scores(query, population) == {}
    assert list(embeddings.scores(query, population, floor=0.0)) == ["best"]
    population["real"] = [0.80, 0.60]
    assert list(embeddings.scores(query, population)) == ["real"]


def test_the_agent_gets_a_compact_result_that_fits_the_host_window_however_big_the_graph(card):
    """The host projects a tool result over ~8000 characters into a stub the model
    cannot read; the assistant then reported "no results" for a full answer."""
    kb = client.open_client(card.node_id, card.storage_path)
    long_claim = "Fine-tuning uses the public DPA-2 pre-trained model as the foundation, " * 8
    entities = [{"type": "concept", "name": "PFD-kit", "aliases": ["PFD toolkit"], "description": long_claim},
                {"type": "concept", "name": "DPA-2", "description": long_claim}]
    filler = "A long description of a workflow stage, unrelated to the question. " * 8
    entities += [{"type": "concept", "name": f"Step {i}", "description": filler} for i in range(60)]
    relations = [{"source": "PFD-kit", "target": f"Step {i}", "type": "uses",
                  "properties": {"description": f"PFD-kit uses step {i}. " + long_claim, "snippet": long_claim,
                                 "evidence_level": 2, "source_ids": [str(uuid.uuid4())]}} for i in range(60)]
    relations.append({"source": "PFD-kit", "target": "DPA-2", "type": "fine_tunes",
                      "properties": {"description": "PFD-kit fine-tunes DPA-2. " + long_claim, "evidence_level": 1}})
    kb.graph.extract({"entities": entities, "relations": relations}, extractor=lambda payload: payload)

    compact = run(card, actions.graph, query="Can I use PFD kit with DPA-2 model?")
    encoded = json.dumps(compact, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= actions.COMPACT_BYTES and compact["truncated"] is True
    # The answer is still there: both concepts, the relation between them first, its path.
    assert [e["name"] for e in compact["entities"] if e["matched"]] == ["PFD-kit", "DPA-2"]
    first = compact["relations"][0]
    assert (first["source_name"], first["predicate"], first["target_name"]) == ("PFD-kit", "fine_tunes", "DPA-2")
    assert first["statement"].endswith("…") and len(first["statement"]) <= actions.STATEMENT_CHARS
    assert compact["connections"][0]["steps"] == ["PFD-kit —fine_tunes→ DPA-2"]
    # Compact means no duplicated property blobs, no snippets, no ids on neighbours.
    assert all("properties" not in item and "snippet" not in item for item in compact["relations"])
    assert all("id" in e for e in compact["entities"] if e["matched"])
    assert all("id" not in e and "description" not in e for e in compact["entities"] if not e["matched"])

    # The UI asks for the full view and gets properties back, uncapped by the window.
    full = run(card, actions.graph, query="Can I use PFD kit with DPA-2 model?", full=True)
    assert len(json.dumps(full).encode()) > actions.COMPACT_BYTES
    assert full["relations"][0]["properties"]["evidence_level"] == 1 and full["entities"][0]["properties"]


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
        run(card, _ingest, filename="../escape.md", content_base64="aGk=")
    with pytest.raises(ValueError):
        run(card, _ingest, filename="x.md", content_base64="not base64!!")
    with pytest.raises(ValueError):
        run(card, _ingest, filename="empty.md", content_base64="")
    with pytest.raises(ValueError):
        run(card, _ingest, filename="x.md", content_base64="aGk=", sql="DROP")


def test_an_unconvertible_upload_fails_in_the_job_not_the_action(card):
    # Ingest must stay fast and never convert: the mutation lock is held for its duration.
    submitted = run(card, _ingest, filename="scan.bin",
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

    submitted = run(card, _ingest, filename="notebook.jpg",
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


def test_process_sources_runs_in_batch_and_skips_what_is_already_converted(card):
    alloys = run(card, projects.project_manage, operation="create", name="Alloys")["project"]
    ids = []
    for name in ("a.md", "b.md"):
        result = run(card, _ingest, filename=name,
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
    run(card, _ingest, filename="c.md",
        content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
        media_type="text/markdown", group_id=alloys["id"])
    pending = run(card, actions.process_sources, group_id=alloys["id"])
    assert len(pending["jobs"]) == 1 and pending["jobs"][0]["filename"] == "c.md"


CONDUCTIVITY_SCHEMA = {
    "type": "object", "required": ["material", "conductivity"],
    "properties": {"material": {"type": "string"}, "conductivity": {"type": "number"},
                   "temperature": {"type": "number"}},
}
PAPER = """# Solid electrolytes

| Material | Conductivity (S/cm) | T (K) |
|---|---|---|
| LGPS | 0.012 | 298 |
| LLZO | 0.0005 | 298 |
"""


EXPERIMENT_SCHEMA = {
    "type": "object", "required": ["sample_id", "conductivity"],
    "properties": {"sample_id": {"type": "string"}, "conductivity": {"type": "number"}},
}


def _experiment_schema(card):
    return run(card, actions.schema_manage, operation="create", name="Conductivity run",
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

    # Experiment materials share the project inventory and remain identifiable.
    assert len(run(card, actions.sources)["sources"]) == 2
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
    projection_space = run(card, spaces.space_manage, operation="create", name="Report schema",
                           definition=SCHEMA, system_prompt="x")["space"]
    with pytest.raises(KnowledgeError):
        _bundle(card, projection_space)

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


def test_deleting_an_upload_removes_its_records_but_retains_project_materials(card):
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
    other = run(card, actions.schema_manage, operation="create", name="Other run",
                definition=EXPERIMENT_SCHEMA, system_prompt="x", kind="experiment")["schema"]
    with pytest.raises(KnowledgeError, match="already has records"):
        run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
            schema_id=other["id"])
    empty = _bundle(card, schema, name="Empty")
    projection_space = run(card, spaces.space_manage, operation="create", name="Reports", definition=SCHEMA,
                           system_prompt="x")["space"]
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_bundle_manage, operation="update", bundle_id=empty["id"],
            schema_id=projection_space["id"])
    moved = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=empty["id"],
                schema_id=other["id"])["bundle"]
    assert moved["schema_id"] == other["id"]
    run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=empty["id"])

    removed = run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=bundle["id"])
    assert removed["records_removed"] == 1
    assert run(card, actions.experiment_bundles)["bundles"] == []
    assert run(card, actions.experiments)["experiments"] == []
    assert len(run(card, actions.sources)["sources"]) == 1
    assert len(run(card, actions.sources, workflow="experiment")["sources"]) == 1
    assert projects.projects(card, {"project_id": bundle["group_id"]})["project"]["source_count"] == 1
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

    schema = run(card, actions.schema_manage, operation="create", name="Electrolyte batch",
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
            card, actions.schema_manage, operation="create", name="Lit", definition=SCHEMA,
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
    other = run(card, projects.project_manage, operation="create", name="Other")["project"]
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
    xrd = run(card, actions.schema_manage, operation="create", name="XRD", definition=XRD_SCHEMA,
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
    run(card, actions.schema_manage, operation="create", name="Empty", definition=XRD_SCHEMA,
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
