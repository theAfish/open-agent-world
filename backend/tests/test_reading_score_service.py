"""Local reading-score jobs: source identity, isolation, cancellation and cache."""
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pytest

from backend.reading_scorer import result_key
from backend.tests.conftest import create_node
from backend.tests.test_research_plugins import sample_pdf


MODEL = {"manifest_sha256": "a" * 64, "algorithm_version": "test-v1", "dtype": "float32",
         "device": "cpu", "versions": {"torch": "test-1", "tokenizers": "test-1"}}


def paper(client, raw=None):
    node = create_node(client, "library.paper")
    raw = raw or sample_pdf()
    replace_pdf(client, node["id"], raw)
    return node["id"], hashlib.sha256(raw).hexdigest(), raw


def replace_pdf(client, node_id, raw):
    revision = client.get(f"/api/nodes/{node_id}/document").json()["revision"]
    response = client.post(f"/api/nodes/{node_id}/actions/import", json={"expected_revision": revision,
        "arguments": {"filename": "local-test.pdf", "pdf": base64.b64encode(raw).decode()}})
    assert response.status_code == 200, response.text


def url(node_id, identity=None):
    return f"/api/library/papers/{node_id}/reading-scores" + (f"/{identity}" if identity else "")


def request(digest, **changes):
    return {"document_sha256": digest, "page": 1, "text": "A synthetic source sentence.",
            "text_parser_version": "pdfjs-text-items-v1/5.4.624/items-newline-v1", **changes}


def submit(client, node_id, digest, **changes):
    response = client.post(url(node_id), json=request(digest, **changes))
    assert response.status_code == 202, response.text
    return response.json()["id"]


def terminal(client, node_id, identity, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(url(node_id, identity))
        assert response.status_code == 200, response.text
        value = response.json()
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(.015)
    raise AssertionError("Reading-score job did not terminate")


class FakeScorer:
    def __init__(self):
        self.ready = True
        self.model = copy.deepcopy(MODEL)
        self.gate = asyncio.Lock()
        self.release = threading.Event()
        self.release.set()
        self.started = threading.Event()
        self.calls = []
        self.cancelled = []

    async def request(self, op):
        assert op == "probe"
        self.ready = True
        return {"ready": True, "sample": {"model": self.model}}

    async def score(self, arguments, progress):
        async with self.gate:
            self.calls.append(arguments["text"])
            self.started.set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(.01)
                progress({"scored_tokens": 1, "total_tokens": 1})
                return {"text_sha256": hashlib.sha256(arguments["text"].encode()).hexdigest(),
                        "model": copy.deepcopy(self.model), "token_count": 2, "scored_count": 1,
                        "tokens": [{"index": 0, "status": "no_context", "logprob": None, "bits": None},
                                   {"index": 1, "status": "scored", "logprob": -1, "bits": 1.442695}]}
            except asyncio.CancelledError:
                self.cancelled.append(arguments["text"])
                raise

    async def close(self):
        self.ready = False


@pytest.fixture
def fake_scoring(client):
    assert client.get("/api/library/reading-scorer").status_code == 200
    jobs = client.app.state.services.reading_scores
    fake = FakeScorer()
    jobs.scorer = fake
    try:
        yield client, jobs, fake
    finally:
        client.portal.call(jobs.close)


def test_no_configuration_never_claims_a_ready_model(client):
    status = client.get("/api/library/reading-scorer").json()
    assert status["configured"] is False and status["ready"] is False
    assert status["execution"] == "local-cpu"
    node_id, digest, _ = paper(client)
    job = terminal(client, node_id, submit(client, node_id, digest))
    assert job["status"] == "failed"
    assert "not configured" in job["error"]
    assert "result" not in job
    assert client.app.state.services.reading_scores.scorer.process is None


def test_current_document_and_page_are_checked_before_creating_a_job(fake_scoring):
    client, jobs, fake = fake_scoring
    node_id, digest, _ = paper(client)
    for body in (request("0" * 64), request(digest, page=2), request(digest, stride=256),
                 request(digest, context_tokens=True), request(digest, text_parser_version="invented")):
        response = client.post(url(node_id), json=body)
        assert response.status_code == 422, response.text
    assert jobs.jobs == {} and fake.calls == []


def test_same_pdf_cache_reuse_does_not_expose_another_papers_job(fake_scoring):
    client, jobs, fake = fake_scoring
    one, digest, raw = paper(client)
    two, _, _ = paper(client, raw)
    first_id = submit(client, one, digest)
    first = terminal(client, one, first_id)
    assert first["status"] == "complete" and first["cached"] is False
    assert client.get(url(two, first_id)).status_code == 404
    assert client.delete(url(two, first_id)).status_code == 404
    second_id = submit(client, two, digest)
    second = terminal(client, two, second_id)
    assert first_id != second_id
    assert second["status"] == "complete" and second["cached"] is True
    assert len(fake.calls) == 1
    third = terminal(client, one, submit(client, one, digest))
    assert third["cached"] is True and len(fake.calls) == 1


def test_cancel_only_owns_its_job_and_never_publishes_result_or_cache(fake_scoring):
    client, jobs, fake = fake_scoring
    one, digest, raw = paper(client)
    two, _, _ = paper(client, raw)
    fake.release.clear()
    first_id = submit(client, one, digest, text="active first")
    assert fake.started.wait(2)
    queued_id = submit(client, one, digest, text="queued same paper")
    other_id = submit(client, two, digest, text="other paper")
    assert client.post(url(one), json=request(digest, text="overflow")).status_code == 422
    assert client.delete(url(one, other_id)).status_code == 404
    assert client.delete(url(one, queued_id)).json()["status"] == "cancelled"
    assert client.get(url(one, first_id)).json()["status"] == "running"
    assert client.delete(url(one, first_id)).json()["status"] == "cancelled"
    fake.release.set()
    assert terminal(client, two, other_id)["status"] == "complete"
    for identity in (first_id, queued_id):
        job = client.get(url(one, identity)).json()
        assert job["status"] == "cancelled" and "result" not in job
    assert "queued same paper" not in fake.calls
    assert fake.cancelled == ["active first"]
    assert len(list(jobs.cache.glob("*.json"))) == 1


def test_replacement_during_scoring_rejects_the_stale_result(fake_scoring):
    client, _, fake = fake_scoring
    node_id, digest, raw = paper(client)
    fake.release.clear()
    identity = submit(client, node_id, digest)
    assert fake.started.wait(2)
    replace_pdf(client, node_id, raw + b"\n% replacement\n")
    fake.release.set()
    job = terminal(client, node_id, identity)
    assert job["status"] == "failed" and "result" not in job
    assert "PDF changed" in job["error"]
    assert client.post(url(node_id), json=request(digest)).status_code == 422


def test_cache_identity_includes_text_parser_model_and_context():
    def key(**overrides):
        arguments = {"model": copy.deepcopy(MODEL), "document_sha256": "1" * 64, "page": 1,
                     "parser_version": "parser-v1", "text": "same original", "context_tokens": 256, "stride": 128}
        arguments.update(overrides)
        return result_key(**arguments)
    baseline = key()
    for changes in ({"document_sha256": "2" * 64}, {"page": 2}, {"parser_version": "parser-v2"},
                    {"text": "changed original"}, {"context_tokens": 512}, {"stride": 64},
                    {"model": {**MODEL, "manifest_sha256": "b" * 64}},
                    {"model": {**MODEL, "algorithm_version": "test-v2"}},
                    {"model": {**MODEL, "dtype": "bfloat16"}},
                    {"model": {**MODEL, "versions": {**MODEL["versions"], "tokenizers": "test-2"}}}):
        assert key(**changes) != baseline, changes


def test_service_cache_misses_after_text_parser_or_model_changes(fake_scoring):
    client, _, fake = fake_scoring
    node_id, digest, _ = paper(client)
    first = terminal(client, node_id, submit(client, node_id, digest))
    assert first["status"] == "complete"
    changed = terminal(client, node_id, submit(client, node_id, digest, text="different source"))
    assert changed["cached"] is False
    parsed = terminal(client, node_id, submit(client, node_id, digest,
        text_parser_version="pdfjs-text-items-v1/5.4.625/items-newline-v1"))
    assert parsed["cached"] is False
    fake.model["manifest_sha256"] = "b" * 64
    modeled = terminal(client, node_id, submit(client, node_id, digest))
    assert modeled["cached"] is False
    assert len(fake.calls) == 4


def test_real_local_scorer_service_optional(client, data_root):
    """Run explicitly once in an isolated profile; normal CI needs no model."""
    config_path = os.environ.get("OAW_TEST_SCORER_CONFIG")
    if not config_path:
        pytest.skip("Set OAW_TEST_SCORER_CONFIG for the bounded local-model acceptance")
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    (data_root / "reading-scorer.json").write_text(json.dumps(config), encoding="utf-8")
    node_id, digest, _ = paper(client)
    identity = submit(client, node_id, digest, text="The lithium ion moves. 锂离子迁移。🙂 e\u0301")
    jobs = client.app.state.services.reading_scores
    try:
        result = terminal(client, node_id, identity, timeout=90)
        assert result["status"] == "complete", result
        assert result["cached"] is False
        value = result["result"]
        assert value["model"]["device"] == "cpu"
        assert value["model"]["model_id"] == "HuggingFaceTB/SmolLM2-135M"
        assert value["tokens"][0]["status"] == "no_context"
        assert all(t["status"] == "scored" for t in value["tokens"][1:])
        cached = terminal(client, node_id, submit(client, node_id, digest,
            text="The lithium ion moves. 锂离子迁移。🙂 e\u0301"), timeout=10)
        assert cached["cached"] is True
        evidence_dir = os.environ.get("OAW_SCORER_EVIDENCE_DIR")
        if evidence_dir:
            Path(evidence_dir).mkdir(parents=True, exist_ok=True)
            (Path(evidence_dir) / "service-probe-report.json").write_text(json.dumps(
                {"passed": True, "job": result, "repeat_cached": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        process = jobs.scorer.process
        client.portal.call(jobs.close)
        assert jobs.scorer.process is None
        if process is not None:
            assert process.returncode is not None
