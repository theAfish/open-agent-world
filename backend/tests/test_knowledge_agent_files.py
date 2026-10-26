"""Conversation files reach the KB through real scoped Agent tools, without a Sandbox."""
import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.runs import InvocationCaller, InvocationContext, RunStore
from backend.runs.manager import _current_invocation
from backend.tests.test_conversation_attachments import upload, ref

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins/knowledge_base/src"))
from oaw_knowledge_base.client import open_client  # noqa: E402


@pytest.fixture
def research(client):
    response = client.post("/api/legions/presets/knowledge.base.research/instances", json={})
    assert response.status_code == 201, response.text
    keys = response.json()["node_ids"]
    services = client.app.state.services
    session = client.get(f"/api/conversations/{keys['conversation']}").json()["sessions"][0]
    base = f"/api/conversations/{keys['conversation']}/sessions/{session['id']}"
    provider = WorldAgentCapabilityProvider(services)
    RunStore(services.database).create(agent_id=keys["agent"], run_id="knowledge-file-test",
        runtime_provider_id="core.mock", caller_kind="conversation", context_id=session["id"])

    async def invoke(name, args):
        token = _current_invocation.set(InvocationContext(run_id="knowledge-file-test",
            agent_id=keys["agent"], parent_run_id=None, root_run_id="knowledge-file-test",
            caller=InvocationCaller("conversation", keys["conversation"]),
            context_id=session["id"], task_id=None, runtime_provider_id="core.mock"))
        try:
            return await provider.invoke_tool(keys["agent"], "operation:" + name, args)
        finally:
            _current_invocation.reset(token)

    def call(tool_name, **args):
        if tool_name.startswith("knowledge_"):
            args.setdefault("knowledge", keys["knowledge"])
        return client.portal.call(invoke, tool_name, args)

    def attach(name, data):
        file = upload(client, base, name, data)
        assert client.post(base + "/messages", json={"attachments": [ref(file)]}).status_code == 202
        return file

    def wait(imported):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = call("knowledge_jobs", job_id=imported["job"]["id"], wait_seconds=5)["job"]
            if result["status"] not in {"QUEUED", "RUNNING", "PENDING", "RETRYING"}:
                assert result["status"] == "COMPLETED", result
                return result
            time.sleep(.05)
        pytest.fail("Knowledge conversion did not complete within 45 seconds")

    return SimpleNamespace(client=client, keys=keys, services=services, provider=provider,
        call=call, invoke=invoke, attach=attach, wait=wait, base=base)


def test_research_agent_imports_pdf_reads_searches_and_saves_a_literature_draft(research):
    import pymupdf

    r = research
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "Sample A1 was sintered at 1750 C for 2 hours under nitrogen.")
        data = document.tobytes()
    attachment = r.attach("sintering.pdf", data)
    assert r.call("inspect_artifacts", collection=r.keys["conversation"], **ref(attachment))["state"] == "binary"
    imported = r.call("knowledge_import_artifact", source=r.keys["conversation"], **ref(attachment))
    r.wait(imported)
    source_id = imported["source"]["id"]
    document = r.call("knowledge_markdown", source_id=source_id)
    assert document["filename"] == "sintering.pdf" and "1750" in document["markdown"]
    assert r.call("knowledge_document_search", query="nitrogen")["results"]
    assert r.call("knowledge_overview")["counts"]["entities"] == 0

    # The same file can be retried without another source or conversion job.
    repeated = r.call("knowledge_import_artifact", source=r.keys["conversation"], **ref(attachment))
    assert repeated["reused"] and repeated["source"]["id"] == source_id and repeated["state"] == "ready"
    assert r.call("knowledge_overview")["counts"]["sources"] == 1
    kb = open_client(r.keys["knowledge"], r.services.resources.node_storage_path(r.keys["knowledge"]))
    origin = kb.sources.require(source_id).metadata["origin"]
    assert origin == {"collection_id": r.keys["conversation"], **ref(attachment)}

    schema = r.call("knowledge_schema_manage", operation="create", name="Sintering records",
        definition={"type": "object", "properties": {"temperature_c": {"type": "number"}},
                    "required": ["temperature_c"]}, system_prompt="Read each sample's sintering temperature.")["schema"]
    prompt = r.call("knowledge_record_extract_prompt", source_id=source_id, schema_id=schema["id"])
    assert "1750" in prompt["markdown"]
    saved = r.call("knowledge_record_save", source_id=source_id, schema_id=schema["id"],
        records=[{"name": "A1", "data": {"temperature_c": 1750}}])
    assert len(saved["records"]) == 1 and saved["records"][0]["status"] == "draft"
    draft = r.call("knowledge_draft_open", source_ids=[source_id])["draft"]
    assert r.call("knowledge_extract_prompt", draft_id=draft["id"], source_id=source_id)["markdown"]
    # Opening/reading/saving drafts never grants publication or modifies the graph.
    with pytest.raises(PermissionDeniedError):
        r.call("knowledge_review", operation="publish", draft_id=draft["id"], confirm=True)
    assert r.call("knowledge_overview")["counts"]["entities"] == 0
    tools = r.client.portal.call(r.provider.list_tools, r.keys["agent"])
    import_schema = next(tool.input_schema for tool in tools if tool.name == "knowledge_import_artifact")
    assert "content_base64" not in json.dumps(import_schema) and "data" not in import_schema["properties"]


def test_research_agent_prepares_and_reads_experiment_attachments(research):
    r = research
    schema = r.call("knowledge_schema_manage", operation="create", name="Measured density", kind="experiment",
        definition={"type": "object", "properties": {"density": {"type": "number"}}},
        system_prompt="Read density for every sample.")["schema"]
    bundle = r.call("knowledge_experiment_bundle_prepare", name="Batch 7", schema_id=schema["id"])["bundle"]
    attachment = r.attach("batch7.csv", b"sample,density\nA1,3.21\n")
    imported = r.call("knowledge_import_artifact", source=r.keys["conversation"],
        bundle_id=bundle["id"], **ref(attachment))
    r.wait(imported)
    prompt = r.call("knowledge_experiment_extract_prompt", bundle_id=bundle["id"])
    assert "3.21" in prompt["markdown"] and "batch7.csv" in prompt["markdown"]
    saved = r.call("knowledge_experiment_save", bundle_id=bundle["id"],
        records=[{"name": "A1", "data": {"density": 3.21}, "source_ids": [imported["source"]["id"]]}])
    record = saved["records"][0]
    assert record["status"] == "draft"
    assert r.call("knowledge_sources")["sources"] == []  # Experiment files stay in their own workflow.

    # A person may publish; subsequent import retries remain harmless, while a
    # different file cannot silently change the reviewed experiment's inputs.
    response = r.client.post(f"/api/nodes/{r.keys['knowledge']}/resource/experiment_review",
        json={"arguments": {"operation": "publish", "record_id": record["id"]}, "confirm": True})
    assert response.status_code == 200, response.text
    assert r.call("knowledge_import_artifact", source=r.keys["conversation"],
        bundle_id=bundle["id"], **ref(attachment))["reused"]
    extra = r.attach("batch7-extra.csv", b"sample,density\nA2,4.2\n")
    with pytest.raises(ResourceValidationError, match="published records"):
        r.call("knowledge_import_artifact", source=r.keys["conversation"], bundle_id=bundle["id"], **ref(extra))
    assert r.call("knowledge_overview")["counts"]["sources"] == 1


def test_read_connection_cannot_import_write_schemas_build_index_or_forge_ingestion(research):
    r = research
    edge = next(edge for edge in r.services.world.connections_from(r.keys["agent"])
                if edge.target == r.keys["knowledge"])
    response = r.client.patch(f"/api/edges/{edge.id}", json={"relationship": "knowledge.base.read"})
    assert response.status_code == 200, response.text
    for name, args in [
        ("knowledge_import_artifact", {}),
        ("knowledge_schema_manage", {"operation": "create"}),
        ("knowledge_graph_index_build", {}),
    ]:
        with pytest.raises(PermissionDeniedError):
            r.call(name, **args)
    with pytest.raises(ResourceValidationError, match="read-only"):
        r.call("knowledge_schemas", operation="create", name="Unauthorized",
            definition={"type": "object", "properties": {}}, system_prompt="Unauthorized")
    with pytest.raises(ResourceValidationError, match="read-only"):
        r.call("knowledge_graph_index", operation="build")
    response = r.client.post(f"/api/nodes/{r.keys['knowledge']}/resource/import_artifact", json={"arguments": {
        "source_collection_id": r.keys["conversation"], "version_id": "00000000-0000-0000-0000-000000000001",
        "path": "fake.txt", "data": "unverified bytes"}})
    assert response.status_code == 422 and "source access is verified" in response.text
    assert r.call("knowledge_overview")["counts"]["sources"] == 0
    assert r.call("knowledge_schemas")["schemas"] == []


def test_waiting_for_conversion_releases_graph_lock_and_observes_revocation(research, monkeypatch):
    r = research
    attachment = r.attach("note.txt", b"A pending conversion")
    imported = r.call("knowledge_import_artifact", source=r.keys["conversation"], **ref(attachment))
    r.wait(imported)
    kb = open_client(r.keys["knowledge"], r.services.resources.node_storage_path(r.keys["knowledge"]))
    job = kb.jobs.require(imported["job"]["id"])
    first_poll = threading.Event()

    def pending_job(identifier):
        assert str(identifier) == str(job.id)
        first_poll.set()
        return job.model_copy(update={"status": "RUNNING"})

    monkeypatch.setattr(kb.jobs, "require", pending_job)
    edge = next(edge for edge in r.services.world.connections_from(r.keys["agent"])
                if edge.target == r.keys["knowledge"])

    async def revoke_while_waiting():
        task = asyncio.create_task(r.invoke("knowledge_jobs", {"knowledge": r.keys["knowledge"],
            "job_id": imported["job"]["id"], "wait_seconds": 30}))
        try:
            assert await asyncio.to_thread(first_poll.wait, 3)
            await asyncio.wait_for(r.services.delete_edge(edge.id), timeout=2)
            with pytest.raises(PermissionDeniedError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    r.client.portal.call(revoke_while_waiting)
