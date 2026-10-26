"""Plugin imports use the host's immutable artifact and live graph boundaries."""
import asyncio
import hashlib
from contextlib import aclosing
from dataclasses import replace
from threading import Event

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import NotFoundError, PermissionDeniedError, ResourceValidationError
from backend.plugins import CapabilityDefinition, CapabilityGrantDefinition, CapabilitySelector, RelationshipDefinition
from backend.plugins.resources import NodeResourceAction
from backend.runs import InvocationCaller, InvocationContext, RunStore
from backend.runs.manager import _current_invocation
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_conversation_attachments import ref, room, upload
from backend.tests.test_tool_projection import connect
from backend.world.models import EdgePatch


@pytest.fixture
def transfer(client):
    services = client.app.state.services
    received = []
    handoff = {}

    def save(context, arguments):
        if "during_write" in handoff:
            handoff["during_write"]()
        context.storage_path.mkdir(parents=True, exist_ok=True)
        data = arguments["data"]
        (context.storage_path / "imported.bin").write_bytes(data)
        return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    async def handler(context, capability, arguments):
        data = await context.read_artifact_file(capability, arguments["collection_id"],
            arguments["version_id"], arguments["path"], max_bytes=arguments.get("max_bytes", 32 * 1024 * 1024))
        received.append(data)
        if "before_write" in handoff:
            await handoff["before_write"]()
        return await context.node_resource_action(capability, "save", {"data": data})

    def configure(registration):
        kind = "test.artifact_transfer.read"
        registration.register_node_type(replace(services.plugins.node_type("core.artifact-collection"),
            id="test.artifact_transfer.destination", traits=frozenset(), templateable=False,
            resource_actions={"save": NodeResourceAction(save, capability_kind=kind)}))
        registration.register_capability(CapabilityDefinition(kind, "test_import_artifact", "Import a file",
            {"type": "object", "properties": {key: {"type": "string"}
                for key in ("collection_id", "version_id", "path")}},
            selectors=(CapabilitySelector("evidence", "evidence_id", frozenset({"text.read"})),),
            target_capabilities=frozenset({"text.edit"})), handler)
        for suffix, grants in (("import", (kind, "text.edit")), ("without_edit", (kind,))):
            registration.register_relationship(RelationshipDefinition(
                id=f"test.artifact_transfer.{suffix}", label="Import", short_label="import",
                description="Import a file into the selected destination",
                source_traits=frozenset({"core.agent"}), target_types=frozenset({"test.artifact_transfer.destination"}),
                capabilities=tuple(CapabilityGrantDefinition(grant) for grant in grants)))

    install_test_plugin(services.plugins, "test.artifact_transfer", configure)
    agent = create_node(client, "agent")
    destination = create_node(client, "test.artifact_transfer.destination")
    evidence = create_node(client, "text", content="Import instructions")
    destination_edge = connect(client, agent, destination, "test.artifact_transfer.import")
    evidence_edge = connect(client, agent, evidence, "read")
    conversation, _, _ = room(client)
    source_edge = connect(client, agent, conversation, "participate")
    response = client.post(f"/api/conversations/{conversation['id']}/sessions",
        json={"title": "Import", "group_title": "Research", "participant_ids": [agent["id"]]})
    assert response.status_code == 201, response.text
    session = response.json()
    base = f"/api/conversations/{conversation['id']}/sessions/{session['id']}"
    data = b"%PDF-1.7\n\0binary attachment\xff\n"
    attachment = upload(client, base, "paper.pdf", data)
    provider = WorldAgentCapabilityProvider(services)
    RunStore(services.database).create(agent_id=agent["id"], run_id="plugin-transfer",
        runtime_provider_id="core.mock", caller_kind="conversation", context_id=session["id"])

    async def invoke(*, session_id=session["id"], active_session=True, **arguments):
        context = InvocationContext(run_id="plugin-transfer", agent_id=agent["id"],
            parent_run_id=None, root_run_id="plugin-transfer", caller=InvocationCaller("conversation", conversation["id"]),
            context_id=session_id, task_id=None, runtime_provider_id="core.mock") if active_session else None
        token = _current_invocation.set(context)
        try:
            return await provider.invoke_tool(agent["id"], "operation:test_import_artifact", {
                "target": destination["id"], "evidence": evidence["id"], "collection_id": conversation["id"],
                **ref(attachment), **arguments})
        finally:
            _current_invocation.reset(token)

    def send():
        response = client.post(base + "/messages", json={"attachments": [ref(attachment)]})
        assert response.status_code == 202, response.text

    return dict(client=client, services=services, agent=agent, conversation=conversation, session=session,
        base=base, attachment=attachment, data=data, received=received, invoke=invoke, send=send,
        handoff=handoff, imported=services.resources.node_storage_path(destination["id"]) / "imported.bin",
        source_edge=source_edge, destination_edge=destination_edge, evidence_edge=evidence_edge)


@pytest.mark.parametrize("source", ["conversation", "collection"])
def test_plugin_receives_verified_binary_without_returning_bytes_to_model(transfer, source):
    state = transfer
    client = state["client"]
    options = {}
    if source == "conversation":
        state["send"]()
    else:
        collection = create_node(client, "core.artifact-collection")
        connect(client, state["agent"], collection, "artifact.read")
        assert client.put(f"/api/artifact-collections/{collection['id']}/references/{state['attachment']['version_id']}").status_code == 200
        options = {"collection_id": collection["id"], "active_session": False}
    result = client.portal.call(lambda: state["invoke"](**options))
    assert state["received"] == [state["data"]]
    assert state["imported"].read_bytes() == state["data"]
    assert result == {"size_bytes": len(state["data"]), "sha256": hashlib.sha256(state["data"]).hexdigest()}
    assert state["services"].resources.artifacts.consumers[state["attachment"]["version_id"]] == 0


@pytest.mark.parametrize("denial", ["unsent", "other_session", "no_session", "removed_participant", "revoked_source"])
def test_plugin_transfer_preserves_conversation_visibility(transfer, denial):
    state = transfer
    client = state["client"]
    options = {}
    if denial != "unsent":
        state["send"]()
    if denial == "other_session":
        response = client.post(f"/api/conversations/{state['conversation']['id']}/sessions",
            json={"title": "Other", "participant_ids": [state["agent"]["id"]]})
        assert response.status_code == 201, response.text
        options["session_id"] = response.json()["id"]
    elif denial == "no_session":
        options["active_session"] = False
    elif denial == "removed_participant":
        assert client.delete(state["base"] + "/participants/" + state["agent"]["id"]).status_code == 200
    elif denial == "revoked_source":
        assert client.delete("/api/edges/" + state["source_edge"]["id"]).status_code == 200
    with pytest.raises(PermissionDeniedError):
        client.portal.call(lambda: state["invoke"](**options))
    assert state["received"] == []


@pytest.mark.parametrize("revoked", ["destination", "required_grant", "selector", "source_at_eof"])
def test_plugin_transfer_rechecks_scopes_during_read_and_at_eof(transfer, monkeypatch, revoked):
    state = transfer
    state["send"]()
    services = state["services"]
    store = services.resources.artifacts
    original = store.read

    async def revoke():
        if revoked == "required_grant":
            await services.update_edge(state["destination_edge"]["id"],
                EdgePatch(relationship="test.artifact_transfer.without_edit"))
        else:
            edge = state[{"destination": "destination_edge", "selector": "evidence_edge",
                          "source_at_eof": "source_edge"}[revoked]]
            await services.delete_edge(edge["id"])

    async def read(*args, **kwargs):
        async with aclosing(original(*args, **kwargs)) as stream:
            async for chunk in stream:
                if revoked != "source_at_eof":
                    await revoke()
                yield chunk
        if revoked == "source_at_eof":
            await revoke()

    monkeypatch.setattr(store, "read", read)
    with pytest.raises(PermissionDeniedError):
        state["client"].portal.call(state["invoke"])
    assert state["received"] == []
    assert store.consumers[state["attachment"]["version_id"]] == 0


@pytest.mark.parametrize("revoked", ["source", "participant", "selector", "reference"])
def test_plugin_transfer_rechecks_access_between_read_and_persistence(transfer, revoked):
    state = transfer
    state["send"]()
    services = state["services"]

    async def revoke():
        if revoked == "participant":
            await services.remove_conversation_session_participant(
                state["conversation"]["id"], state["session"]["id"], state["agent"]["id"])
        elif revoked == "reference":
            from backend.api.artifacts import remove_reference
            await remove_reference(state["conversation"]["id"], state["attachment"]["version_id"], services)
        else:
            edge = state["source_edge" if revoked == "source" else "evidence_edge"]
            await services.delete_edge(edge["id"])

    state["handoff"]["before_write"] = revoke
    with pytest.raises(PermissionDeniedError):
        state["client"].portal.call(state["invoke"])
    assert state["received"] == [state["data"]]  # Reading succeeded before access changed.
    assert not state["imported"].exists()


@pytest.mark.parametrize("revocation", ["participant", "session", "group", "reference"])
def test_conversation_revocation_waits_for_authorized_import_to_persist(transfer, revocation):
    state = transfer
    state["send"]()
    services = state["services"]
    entered, finish = Event(), Event()

    def during_write():
        entered.set()
        assert finish.wait(timeout=10), "Import did not finish"

    state["handoff"]["during_write"] = during_write

    async def revoke():
        if revocation == "participant":
            await services.remove_conversation_session_participant(
                state["conversation"]["id"], state["session"]["id"], state["agent"]["id"])
        elif revocation == "session":
            await services.delete_conversation_session(state["conversation"]["id"], state["session"]["id"])
        elif revocation == "reference":
            from backend.api.artifacts import remove_reference
            await remove_reference(state["conversation"]["id"], state["attachment"]["version_id"], services)
        else:
            await services.delete_conversation_group(state["conversation"]["id"], state["session"]["group_id"])

    async def check():
        importing = asyncio.create_task(state["invoke"]())
        revoking = None
        try:
            assert await asyncio.to_thread(entered.wait, 10), "Import did not reach persistence"
            revoking = asyncio.create_task(revoke())
            await asyncio.sleep(0)
            assert not revoking.done()
            session = services.conversations.get_session(state["conversation"]["id"], state["session"]["id"])
            assert state["agent"]["id"] in session.participant_ids
        finally:
            finish.set()
            await asyncio.wait_for(importing, timeout=10)
            if revoking is not None:
                await asyncio.wait_for(revoking, timeout=10)

    state["client"].portal.call(check)
    assert state["imported"].read_bytes() == state["data"]


@pytest.mark.parametrize("failure", ["path", "too_large", "size", "checksum"])
def test_plugin_transfer_rejects_invalid_or_unverified_bytes(transfer, failure):
    state = transfer
    state["send"]()
    store = state["services"].resources.artifacts
    path = store.path(state["attachment"]["version_id"]) / state["attachment"]["path"]
    options = {}
    if failure == "path":
        options["path"] = "../paper.pdf"
    elif failure == "too_large":
        options["max_bytes"] = len(state["data"]) - 1
    else:
        path.write_bytes(state["data"][:-1] if failure == "size" else b"X" * len(state["data"]))
    with pytest.raises(NotFoundError if failure == "path" else ResourceValidationError):
        state["client"].portal.call(lambda: state["invoke"](**options))
    assert state["received"] == []
    assert not store.consumers.get(state["attachment"]["version_id"])


def test_plugin_transfer_cancellation_closes_artifact_lease(transfer, monkeypatch):
    state = transfer
    state["send"]()
    store = state["services"].resources.artifacts
    original = store.read

    async def read(*args, **kwargs):
        async with aclosing(original(*args, **kwargs)) as stream:
            async for _ in stream:
                raise asyncio.CancelledError()
                yield  # Keep this test wrapper an async generator.

    monkeypatch.setattr(store, "read", read)
    async def check():
        with pytest.raises(asyncio.CancelledError):
            await state["invoke"]()
    state["client"].portal.call(check)
    assert state["received"] == []
    assert store.consumers[state["attachment"]["version_id"]] == 0


def test_waiting_for_artifact_store_does_not_block_graph_revocation(transfer, monkeypatch):
    state = transfer
    state["send"]()
    services = state["services"]
    store = services.resources.artifacts
    original = store.read
    entered = asyncio.Event()

    async def read(*args, **kwargs):
        entered.set()
        async with aclosing(original(*args, **kwargs)) as stream:
            async for chunk in stream:
                yield chunk

    monkeypatch.setattr(store, "read", read)

    async def check():
        # Artifact publication also takes the graph lock while holding this lock.
        async with store._lock:
            reading = asyncio.create_task(state["invoke"]())
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.wait_for(services.delete_edge(state["destination_edge"]["id"]), timeout=5)
        with pytest.raises(PermissionDeniedError):
            await asyncio.wait_for(reading, timeout=5)

    state["client"].portal.call(check)
    assert state["received"] == []
