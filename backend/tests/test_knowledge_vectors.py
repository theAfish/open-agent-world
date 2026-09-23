"""Vector store: ingesting through Agent grants, lexical/dense/hybrid search, models, jobs and provenance."""
import asyncio
import json
import sqlite3
import time
import zlib
from threading import Event

import httpx
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import NotFoundError, ResourceValidationError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.plugins.resources import NodeResourceContext
from backend.services import create_services
from backend.tests.test_knowledge_common import page_text
from backend.tests.test_paper_extraction import FakeGrobid, imported
from backend.world.models import CardCreate, EdgeCreate
from oaw_knowledge import common, vectors, vectors_embed
from oaw_library import grobid

DIM = 64


def fake_vector(text: str) -> list[float]:
    """Deterministic bag-of-words vector: shared words give high cosine."""
    vector = [0.0] * DIM
    for word in vectors._TERM.findall(text.casefold()):
        vector[zlib.crc32(word.encode()) % DIM] += 1.0
    return vector if any(vector) else [1.0] + [0.0] * (DIM - 1)


class FakeEmbeddings:
    def __init__(self):
        self.requests = []
        self.fail = False
        self.delay = 0.0
        self.dim = DIM

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization"), **body})
        time.sleep(self.delay)
        if self.fail:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json={"data": [{"index": i, "embedding": fake_vector(text)[:self.dim]}
                                                  for i, text in enumerate(body["input"])]})


@pytest.fixture(autouse=True)
def fake_services(monkeypatch):
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(FakeGrobid()))
    for name in ("OAW_EMBEDDING_URL", "OAW_EMBEDDING_MODEL", "OAW_EMBEDDING_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(vectors_embed, "_transport", httpx.MockTransport(fake))
    monkeypatch.setenv("OAW_EMBEDDING_URL", "http://embed.test")
    monkeypatch.setenv("OAW_EMBEDDING_MODEL", "fake-a")
    monkeypatch.setenv("OAW_EMBEDDING_API_KEY", "secret")
    return fake


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


class World:
    def __init__(self, services, agent, store, paper, outside):
        self.services, self.agent, self.store, self.paper, self.outside = services, agent, store, paper, outside
        self.provider = WorldAgentCapabilityProvider(services)

    async def tools(self):
        return {tool.name: tool for tool in await self.provider.list_tools(self.agent.id)}

    async def call(self, name, **arguments):
        tool = (await self.tools())[name]
        return await self.provider.invoke_tool(self.agent.id, tool.capability_id, {"store": self.store.id, **arguments})

    async def user(self, action, **arguments):
        return await invoke_resource_action(self.services, self.store.id, action, ResourceActionRequest(arguments=arguments))

    def db(self):
        connection = sqlite3.connect(self.services.resources.node_storage_path(self.store.id) / common.FILE_NAME)
        connection.row_factory = sqlite3.Row
        return connection


async def setup(services, relationship="knowledge.vectors.curate"):
    library = await services.create_card(CardCreate(type="library.collection", name="Cathodes"))
    paper = await services.create_card(CardCreate(type="library.paper", name="Layered oxide", parent_id=library.id))
    await imported(services, paper.id)
    outside = await services.create_card(CardCreate(type="library.paper", name="Elsewhere"))
    await imported(services, outside.id)
    store = await services.create_card(CardCreate(type="knowledge.vectors", name="Vectors"))
    agent = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.read"))
    await services.create_edge(EdgeCreate(source=agent.id, target=store.id, relationship=relationship))
    return World(services, agent, store, paper, outside)


def test_chunks_overlap_and_stay_bounded():
    text = " ".join(f"Sentence {n} about layered oxide cathodes." for n in range(200))
    pieces = vectors.chunk(text)
    assert all(len(piece) <= vectors.CHUNK_MAX for piece in pieces) and len(pieces) > 3
    assert pieces[1][:30] in pieces[0]  # Overlap with the previous passage.
    assert vectors.chunk("  \n ") == [] and vectors.chunk("Hyphen-\nated word") == ["Hyphenated word"]


@pytest.mark.asyncio
async def test_read_and_curate_tool_sets(services):
    world = await setup(services, "knowledge.vectors.read")
    names = set(await world.tools())
    assert {"semantic_search", "vector_store_status", "check_knowledge_provenance", "read_knowledge_log"} <= names
    assert not names & {"ingest_papers", "add_passage", "remove_passages", "reembed_passages"}
    curator = await setup(services)
    assert {"ingest_papers", "add_passage", "remove_passages", "reembed_passages", "semantic_search"} <= set(await curator.tools())


@pytest.mark.asyncio
async def test_lexical_mode_without_embedding_service(services):
    world = await setup(services)
    result = await world.call("ingest_papers", papers=[world.paper.id, world.outside.id, "no-such-paper"], note="survey")
    outcome = {item["paper"]: item for item in result["papers"]}
    assert outcome[world.paper.id]["status"] == "ingested" and outcome[world.paper.id]["pages"] == 3
    for paper in (world.outside.id, "no-such-paper"):
        assert outcome[paper]["status"] == "failed" and "cannot read" in outcome[paper]["error"]
    assert result["job"] is None and result["mode"] == "lexical"
    with world.db() as db:
        assert {row[0] for row in db.execute("SELECT DISTINCT paper FROM passages")} == {world.paper.id}
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0

    # Each passage cites its page at quote level, pinned to the Paper's fingerprint.
    fingerprint = (await page_text(services, world.agent, world.paper, 1))["fingerprint"]
    with world.db() as db:
        rows = db.execute("SELECT p.page, p.text, s.quote, s.level, s.fingerprint, s.verified_by FROM passages p"
                          " JOIN sources s ON s.record = 'passage:' || p.id").fetchall()
    assert rows and all(r["level"] == "quote" and r["fingerprint"] == fingerprint and r["verified_by"] == world.agent.id
                        and r["text"].startswith(r["quote"]) for r in rows)
    assert {r["page"] for r in rows} == {1, 2, 3}

    found = await world.call("semantic_search", query="acknowledgments NSFC grant", mode="hybrid")
    assert found["mode"] == "lexical" and "No embedding service" in found["warning"] and found["model"] is None
    top = found["results"][0]
    assert top["page"] == 3 and top["cite"] == f"{world.paper.id}#p3" and "bm25" in top["scores"] and "cosine" not in top["scores"]
    assert "warning" not in await world.call("semantic_search", query="cathode")

    status = await world.call("vector_store_status")
    assert status["mode"] == "lexical" and status["embedding"]["configured"] is False
    assert status["papers"][0]["paper"] == world.paper.id and status["papers"][0]["pages"] == 3
    assert "lexical" in status["hint"]
    # Unchanged Papers are skipped; force re-ingests and replaces.
    again = await world.call("ingest_papers", papers=[world.paper.id])
    assert again["papers"][0]["status"] == "unchanged"
    forced = await world.call("ingest_papers", papers=[world.paper.id], force=True)
    assert forced["papers"][0]["replaced"] == forced["papers"][0]["passages"] == status["page_passages"]
    with world.db() as db:
        assert db.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == status["passages"]
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == status["passages"]
    log = await world.call("read_knowledge_log")
    assert log["entries"][-1]["op"] == "ingest" and "survey" in log["entries"][-1]["note"]
    with pytest.raises(ResourceValidationError, match="1-50 Paper ids"):
        await world.call("ingest_papers", papers=[])


@pytest.mark.asyncio
async def test_hybrid_and_dense_with_embedding_service(services, embeddings):
    world = await setup(services)
    result = await world.call("ingest_papers", papers=[world.paper.id])
    assert result["job"]["state"] == "running" and result["job"]["model"] == "fake-a"
    await services.resource_jobs.wait()
    request = embeddings.requests[0]
    assert request["url"] == "http://embed.test/v1/embeddings" and request["model"] == "fake-a" and request["auth"] == "Bearer secret"

    status = await world.call("vector_store_status")
    assert status["mode"] == "hybrid" and status["with_vectors"] == status["passages"] and status["jobs"][0]["state"] == "done"
    found = await world.call("semantic_search", query="acknowledgments NSFC grant supported")
    assert found["mode"] == "hybrid" and found["model"] == "fake-a" and "warning" not in found
    top = found["results"][0]
    assert top["page"] == 3 and {"bm25", "cosine", "lexical_rank", "dense_rank"} <= top["scores"].keys()
    dense = await world.call("semantic_search", query="supported NSFC", mode="dense", limit=2)
    assert dense["mode"] == "dense" and len(dense["results"]) == 2 and "bm25" not in dense["results"][0]["scores"]
    assert dense["results"][0]["scores"]["cosine"] >= dense["results"][1]["scores"]["cosine"]
    capped = await world.call("semantic_search", query="cathode capacity", max_per_paper=1, limit=10)
    assert len(capped["results"]) == 1
    # The workspace search embeds its own query.
    assert (await world.user("ui_search", query="NSFC grant"))["mode"] == "hybrid"

    # The query cannot be embedded: lexical, and the result says why.
    embeddings.fail = True
    degraded = await world.call("semantic_search", query="NSFC grant")
    assert degraded["mode"] == "lexical" and "503" in degraded["warning"] and degraded["results"]


@pytest.mark.asyncio
async def test_model_change_is_reported_and_reembedded(services, embeddings, monkeypatch):
    world = await setup(services)
    await world.call("ingest_papers", papers=[world.paper.id])
    await services.resource_jobs.wait()
    monkeypatch.setenv("OAW_EMBEDDING_MODEL", "fake-b")
    status = await world.call("vector_store_status")
    assert status["mode"] == "lexical" and status["with_vectors"] == 0
    assert status["models"] == [{"model": "fake-a", "dim": DIM, "vectors": status["passages"], "searched": False}]
    found = await world.call("semantic_search", query="NSFC grant")
    assert found["mode"] == "lexical" and "fake-b" in found["warning"]

    result = await world.call("reembed_passages", drop_other_models=True)
    assert result["missing"] == status["passages"] and result["dropped"] == status["passages"]
    await services.resource_jobs.wait()
    status = await world.call("vector_store_status")
    assert status["mode"] == "hybrid" and [m["model"] for m in status["models"]] == ["fake-b"]
    assert (await world.call("reembed_passages"))["job"] is None

    # A failed batch leaves passages without vectors; reembed finishes them.
    embeddings.fail = True
    await world.call("add_passage", text="A note on grant support for this layered oxide study.",
                     citations=[{"paper": world.paper.id, "page": 3}])
    await services.resource_jobs.wait()
    status = await world.call("vector_store_status")
    assert status["jobs"][0]["state"] == "failed" and "503" in status["jobs"][0]["error"] and status["without_vectors"] == 1
    embeddings.fail = False
    assert (await world.call("reembed_passages"))["missing"] == 1


@pytest.mark.asyncio
async def test_add_passage_requires_verified_citations(services):
    world = await setup(services)
    quote = " ".join((await page_text(services, world.agent, world.paper, 2))["text"].split()[:10])
    with pytest.raises(ResourceValidationError, match="Cite at least one"):
        await world.call("add_passage", text="The cathode reaches a high capacity.", citations=[])
    with pytest.raises(ResourceValidationError, match="quote is not on page 1"):
        await world.call("add_passage", text="The cathode reaches a high capacity.",
                         citations=[{"paper": world.paper.id, "page": 1, "quote": "completely invented words here"}])
    with pytest.raises(ResourceValidationError, match="cannot read Paper"):
        await world.call("add_passage", text="The cathode reaches a high capacity.",
                         citations=[{"paper": world.outside.id, "page": 1}])
    # Agent-supplied provenance is ignored; only verified citations are stored.
    added = await world.call("add_passage", text="Summary: the layered oxide cathode shows high capacity.", title="Capacity summary",
                             citations=[{"paper": world.paper.id, "page": 2, "quote": quote}],
                             _sources=[{"paper": "forged", "page": 1, "level": "quote", "fingerprint": "x", "verified_at": "now"}])
    assert [s["cite"] for s in added["sources"]] == [f"{world.paper.id}#p2"] and added["sources"][0]["level"] == "quote"
    found = await world.call("semantic_search", query="capacity summary", kind="agent_note")
    assert [r["passage"] for r in found["results"]] == [added["passage"]] and found["results"][0]["sources"][0]["quote"] == quote
    assert (await world.call("semantic_search", query="summary", papers=["other"]))["results"] == []


@pytest.mark.asyncio
async def test_remove_passages_and_provenance(services):
    world = await setup(services)
    await world.call("ingest_papers", papers=[world.paper.id])
    note = await world.call("add_passage", text="The cathode is promising for sodium storage overall.",
                            citations=[{"paper": world.paper.id, "page": 2}])
    report = await world.call("check_knowledge_provenance")
    assert report["summary"]["stale"] == 0 and report["summary"]["fresh"] > 1

    removed = await world.call("remove_passages", papers=[world.paper.id], note="outdated")
    with world.db() as db:
        assert [row[0] for row in db.execute("SELECT id FROM passages")] == [note["passage"]]
        assert [row[0] for row in db.execute("SELECT record FROM sources")] == [f"passage:{note['passage']}"]
    assert removed["removed"] > 0 and (await world.call("read_knowledge_log"))["entries"][0]["op"] == "remove"
    result = await world.user("ui_remove", passages=[note["passage"], 999999])
    assert result == {"removed": 1, "passages": [note["passage"]], "missing": [999999]}
    assert (await world.call("read_knowledge_log"))["entries"][0]["actor"] == "user"
    with pytest.raises(ResourceValidationError, match="Give passage ids"):
        await world.call("remove_passages")


@pytest.mark.asyncio
async def test_jobs_from_a_previous_process_are_interrupted(services, embeddings):
    world = await setup(services)
    await world.call("ingest_papers", papers=[world.paper.id])
    await services.resource_jobs.wait()
    context = NodeResourceContext(world.store.id, services.resources.node_storage_path(world.store.id), Event())
    with vectors._db(context) as db:  # As a crashed process would leave it.
        db.execute("INSERT INTO jobs (id, state, op, model, passages, started_at, started_by) "
                   "VALUES ('old', 'running', 'ingest', 'fake-a', 5, '2026-01-01T00:00:00+00:00', 'user')")
    status = await world.user("ui_status")
    job = next(job for job in status["jobs"] if job["id"] == "old")
    assert job["state"] == "interrupted" and "reembed_passages" in job["error"]


@pytest.mark.asyncio
async def test_workspace_query_embedding_has_a_hard_deadline(services, embeddings, monkeypatch):
    world = await setup(services)
    await world.call("ingest_papers", papers=[world.paper.id])
    await services.resource_jobs.wait()
    monkeypatch.setattr(vectors, "QUERY_DEADLINE", 0.3)
    embeddings.delay = 1.5
    started = time.monotonic()
    result = await world.user("ui_search", query="NSFC grant")
    assert time.monotonic() - started < 1.2
    assert result["mode"] == "lexical" and "within 0.3 s" in result["warning"] and result["results"]


@pytest.mark.asyncio
async def test_query_dimension_mismatch_falls_back_to_lexical(services, embeddings):
    world = await setup(services)
    await world.call("ingest_papers", papers=[world.paper.id])
    await services.resource_jobs.wait()
    embeddings.dim = 32
    result = await world.call("semantic_search", query="NSFC grant", mode="dense")
    assert result["mode"] == "lexical" and "32 dimensions" in result["warning"] and result["results"]


@pytest.mark.asyncio
async def test_no_network_call_without_background_jobs(services, embeddings, tmp_path):
    context = NodeResourceContext("store", tmp_path / "store", Event(), actor_id="agent")
    with vectors._db(context) as db:
        db.execute("INSERT INTO passages (text, kind, created_at, created_by) VALUES ('text', 'agent_note', 'now', 'agent')")
    job = vectors._start_job(context, "add", [(1, "text")], [])
    assert job["state"] == "failed" and "reembed_passages" in job["error"] and embeddings.requests == []
    assert job["id"] not in vectors._RUNNING


@pytest.mark.asyncio
async def test_paper_deleted_while_ingesting_is_reported_per_paper(services):
    world = await setup(services)
    capability = services.capabilities.capability_for_id(world.agent.id, f"knowledge.vectors.ingest:{world.store.id}")

    from backend.capabilities.provider import _CapabilityContext
    inner = _CapabilityContext(services)

    class Context:  # The second Paper is deleted after its grant was checked.
        async def agent_capability(self, capability, kind, target):
            if target == "gone":
                return type("Grant", (), {"target_id": "gone"})()
            return await inner.agent_capability(capability, kind, target)

        async def node_resource_action(self, grant, action, arguments):
            if grant.target_id == "gone":
                raise NotFoundError("Card not found")
            return await inner.node_resource_action(grant, action, arguments)

    handler = services.plugins.capability_handler("knowledge.vectors.ingest")
    result = await handler(Context(), capability, {"papers": ["gone", world.paper.id]})
    outcome = {item["paper"]: item["status"] for item in result["papers"]}
    assert outcome == {"gone": "failed", world.paper.id: "ingested"}
