"""The evidence chain: original file → record → insight, walked from every end, and
the retained bytes behind it on more than one object store.

Reuses the end-to-end helpers of ``test_pipeline`` so the chain is built the way the
card and the service build it.
"""
import base64

import pytest

pytest.importorskip("mkb", reason="Install mat-know-base into this environment")

from oaw_knowledge_base import actions, client, insights, lineage  # noqa: E402
from oaw_knowledge_base.context import KnowledgeContext, MemorySettings  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402
from test_pipeline import card, run, _experiment_schema, _bundle, _upload  # noqa: E402,F401

RULE = {"kind": "rule", "title": "Conductivity rises with temperature",
        "statement": "Across the batch, conductivity increases with temperature."}


def _chain(card):
    """Two files, two published records (the second read from both files) and one
    insight citing both records."""
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    first = _upload(card, bundle, "A1 conductivity 1.0 at 300 K", "a1.md")
    second = _upload(card, bundle, "A2 conductivity 1.4 at 320 K", "a2.md")
    records = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.0}, "source_ids": [first]},
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 1.4}, "source_ids": [first, second]},
    ])["records"]
    card.confirmed = True
    for record in records:
        run(card, actions.experiment_review, operation="publish", record_id=record["id"])
    card.confirmed = False
    insight = run(card, insights.insight_save, schema_id=schema["id"], insights=[
        {**RULE, "evidence": [{"record_id": records[0]["id"]}, {"record_id": records[1]["id"], "role": "observed_in"}]}
    ])["insights"][0]
    return bundle, first, second, records, insight


def test_records_and_insights_trace_back_to_the_retained_originals(card):
    bundle, first, second, records, insight = _chain(card)
    kb = client.open_client(card.node_id, card.storage_path)
    hashes = {source_id: kb.sources.require(source_id).sha256 for source_id in (first, second)}

    # The record payload itself carries the content-addressed pointer.
    shown = run(card, actions.experiments, operation="get", record_id=records[1]["id"])["experiment"]
    assert {(link["source_id"], link["sha256"]) for link in shown["sources"]} == set(hashes.items())
    assert all(link["artifact_id"] for link in shown["sources"])

    # From the insight down: both records, both files, every link intact.
    chain = run(card, lineage.lineage, insight_id=insight["id"])
    assert chain["root"] == {"type": "insight", "id": insight["id"]}
    assert {source["id"]: source["sha256"] for source in chain["sources"]} == hashes
    assert all(source["content_available"] and source["bundle_id"] == bundle["id"]
               and source["markdown"]["record_id"] for source in chain["sources"])
    by_name = {record["name"]: record for record in chain["records"]}
    assert [link["source_id"] for link in by_name["A1"]["sources"]] == [first]
    assert {link["source_id"] for link in by_name["A2"]["sources"]} == {first, second}
    assert all(link["intact"] and link["sha256"] == hashes[link["source_id"]]
               for record in chain["records"] for link in record["sources"])
    assert chain["insights"][0]["id"] == insight["id"] and chain["insights"][0]["status"] == "draft"
    assert {(link["record_id"], link["role"]) for link in chain["insights"][0]["records"]} == {
        (records[0]["id"], "supports"), (records[1]["id"], "observed_in")}
    assert [item["id"] for item in chain["bundles"]] == [bundle["id"]]

    # From one record: its files and the insight resting on it.
    chain = run(card, lineage.lineage, record_id=records[0]["id"])
    assert [source["id"] for source in chain["sources"]] == [first]
    assert [record["id"] for record in chain["records"]] == [records[0]["id"]]
    assert [item["id"] for item in chain["insights"]] == [insight["id"]]

    # From one file up: every record read from it, and so the second file too.
    chain = run(card, lineage.lineage, source_id=second)
    assert [record["name"] for record in chain["records"]] == ["A2"]
    assert {source["id"] for source in chain["sources"]} == {first, second}
    assert [item["id"] for item in chain["insights"]] == [insight["id"]]
    chain = run(card, lineage.lineage, source_id=first)
    assert sorted(record["name"] for record in chain["records"]) == ["A1", "A2"]

    # A file nothing was read from still identifies itself.
    lonely = _upload(card, bundle, "unused", "unused.md")
    chain = run(card, lineage.lineage, source_id=lonely)
    assert chain["sources"][0]["id"] == lonely and chain["records"] == [] and chain["insights"] == []


def test_a_broken_chain_is_reported_not_hidden(card):
    bundle, first, second, records, insight = _chain(card)
    card.confirmed = True
    run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=bundle["id"])

    chain = run(card, lineage.lineage, insight_id=insight["id"])
    assert all(record["missing"] and record["sources"] == [] for record in chain["records"])
    assert chain["sources"] == [] and chain["insights"][0]["id"] == insight["id"]
    with pytest.raises(KnowledgeError):
        run(card, lineage.lineage, record_id=records[0]["id"])
    for arguments in ({}, {"source_id": first, "record_id": records[0]["id"]}):
        with pytest.raises(ValueError):
            run(card, lineage.lineage, **arguments)


def test_the_chain_holds_on_a_filesystem_object_store(tmp_path, monkeypatch):
    storage, objects = tmp_path / "node-2", tmp_path / "objects"
    storage.mkdir()
    monkeypatch.setenv(client.OBJECT_STORE_ENV, f"file://{objects}")
    card = KnowledgeContext("node-2", storage, state=MemorySettings())
    try:
        bundle, first, second, records, insight = _chain(card)
        kb = client.open_client(card.node_id, card.storage_path)
        assert kb.oaw_object_store_url == f"file://{objects}"
        chain = run(card, lineage.lineage, insight_id=insight["id"])
        assert all(source["content_available"] for source in chain["sources"])
        assert all(link["intact"] for record in chain["records"] for link in record["sources"])
        stored = kb.sources.read_bytes(first)
        assert stored == b"A1 conductivity 1.0 at 300 K"
        assert any(path.is_file() and path.read_bytes() == stored for path in objects.rglob("*"))
    finally:
        client.close_client("node-2")

    # The same database must not be opened against another store.
    monkeypatch.delenv(client.OBJECT_STORE_ENV)
    with pytest.raises(KnowledgeError, match="keeps its original files"):
        client.open_client("node-2", storage)
    monkeypatch.setenv(client.OBJECT_STORE_ENV, f"file://{objects}")
    try:
        assert client.open_client("node-2", storage).sources.require(first).sha256
    finally:
        client.close_client("node-2")


def test_a_database_from_before_the_store_was_recorded_is_treated_as_sql(tmp_path, monkeypatch):
    storage = tmp_path / "node-3"
    storage.mkdir()
    card = KnowledgeContext("node-3", storage, state=MemorySettings())
    try:
        kb = client.open_client("node-3", storage)
        from sqlalchemy import text
        with kb.oaw_engine.begin() as connection:
            connection.execute(text("DELETE FROM oaw_kb_evidence_store"))
        text_file = base64.b64encode(b"note").decode()
        schema = _experiment_schema(card)
        bundle = _bundle(card, schema)
        run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename="n.md",
            content_base64=text_file, media_type="text/markdown")
    finally:
        client.close_client("node-3")
    monkeypatch.setenv(client.OBJECT_STORE_ENV, f"file://{tmp_path / 'elsewhere'}")
    with pytest.raises(KnowledgeError, match="'sql:'"):
        client.open_client("node-3", storage)
    monkeypatch.delenv(client.OBJECT_STORE_ENV)
    try:
        assert client.open_client("node-3", storage).sources.list(limit=1)
    finally:
        client.close_client("node-3")
