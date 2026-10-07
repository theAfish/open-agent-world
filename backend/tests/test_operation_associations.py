"""Durable operation observations and provider-neutral execution references."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import NotFoundError
from backend.events import EventType
from backend.node_continuations import NodeContinuationMixin
from backend.operation_associations import active_operation, operation_context, resolve_associated_objects
from backend.runs.models import RunStatus
from backend.tests.test_automatic_events import register_unknown_tool
from backend.tests.test_runs import RecordingProvider
from backend.world.models import CardCreate


def test_concurrent_calls_keep_produced_objects_and_later_runs_separate(client):
    services = client.app.state.services
    runtime = RecordingProvider(mode="waiting")
    services.run_manager.install_provider("core.mock", runtime)
    arrived, release = [], asyncio.Event()

    async def handler(context, capability, arguments):
        identity = context.invocation["invocation_id"]
        node = await services.create_card(CardCreate(type="agent", name="Same generated type",
            config={"runtime_provider_id": "core.mock"}))
        arrived.append(identity)
        if len(arrived) == 2:
            release.set()
        await asyncio.wait_for(release.wait(), 3)
        record = await services.run_manager.start_run(node.id, "Long-running work", caller_kind="plugin")
        context.associate_execution("plugin:batch", identity + ":batch", object_id=node.id, produced=True)
        return {"object_id": node.id, "run_id": record.run_id}

    agent, target, _ = register_unknown_tool(client, handler)
    provider = WorldAgentCapabilityProvider(services)

    async def invoke():
        results = await asyncio.gather(*(provider.invoke_tool(agent["id"], "operation:polish_surface",
            {"target": target["id"]}, request_id=identity) for identity in ("invocation-a", "invocation-b")))
        assert active_operation.get() is None
        return results

    first, second = client.portal.call(invoke)
    events = [event for _, event in services.operation_events.after(0, limit=10000)]
    returned = {event.payload["invocation_id"]: event for event in events if event.type is EventType.CAPABILITY_SUCCEEDED}
    assert set(returned) == {"invocation-a", "invocation-b"}
    for identity, result in zip(("invocation-a", "invocation-b"), (first, second)):
        event = returned[identity]
        assert resolve_associated_objects(event, produced_only=True) == [result["object_id"]]
        assert resolve_associated_objects(event) == [result["object_id"]]
        assert event.payload["caller_object_id"] == agent["id"]
        assert event.payload["target_card_id"] == target["id"]
        assert any(ref["kind"] == "run" and ref["id"] == result["run_id"] for ref in event.payload["associations"])
        assert services.run_manager.get_run(result["run_id"]).status is RunStatus.RUNNING
    assert not any(event.type is EventType.RUN_SUCCEEDED for event in events)

    async def complete():
        runtime.mode = "success"
        runtime.release_turn.set()
        for result in (first, second):
            await asyncio.wait_for(services.run_manager.wait_execution(result["run_id"]), 3)

    client.portal.call(complete)
    completed = [event for _, event in services.operation_events.after(0, limit=10000)
                 if event.type is EventType.RUN_SUCCEEDED]
    assert {event.payload["invocation_id"] for event in completed} == {"invocation-a", "invocation-b"}
    for event in completed:
        expected = first if event.payload["invocation_id"] == "invocation-a" else second
        assert event.run_id == expected["run_id"]
        assert resolve_associated_objects(event, produced_only=True) == [expected["object_id"]]


def test_run_business_mutation_and_durable_event_share_a_transaction(client, monkeypatch):
    services = client.app.state.services
    store, journal = services.run_manager.store, services.operation_events
    sequence = journal.max_sequence()
    with pytest.raises(RuntimeError, match="abort"):
        with services.database.transaction(immediate=True):
            store.create(agent_id="test", run_id="rolled-back", runtime_provider_id="test", caller_kind="test")
            raise RuntimeError("abort")
    with pytest.raises(NotFoundError):
        store.get("rolled-back")
    assert journal.max_sequence() == sequence

    store.create(agent_id="test", run_id="durable", runtime_provider_id="test", caller_kind="test")
    original = store.event_sink

    def fail_after_record(event):
        original(event)
        raise RuntimeError("journal failure")

    monkeypatch.setattr(store, "event_sink", fail_after_record)
    with pytest.raises(RuntimeError, match="journal failure"):
        store.update_status("durable", RunStatus.RUNNING)
    assert store.get("durable").status is RunStatus.CREATED
    assert [event.type for _, event in journal.after(sequence)] == [EventType.RUN_CREATED]
    monkeypatch.setattr(store, "event_sink", original)
    store.update_status("durable", RunStatus.RUNNING)
    store.update_status("durable", RunStatus.WAITING)
    store.update_status("durable", RunStatus.RUNNING)
    store.interrupt_incomplete()
    assert [event.payload["event"] for _, event in journal.after(sequence)] == [
        "run.created", "run.started", "run.waiting", "run.resumed", "run.interrupted"]


def test_registered_document_action_discovery_and_atomic_return_event(client, monkeypatch):
    from backend.node_documents import DocumentActionRequest, invoke_document_action, read_document
    from backend.tests.test_document_action_prepare import install_prepared_node
    services = client.app.state.services

    async def prepare(value, arguments):
        return {"content": "changed"}

    node, _ = install_prepared_node(client, prepare)
    catalog = client.get(f"/api/state-machines/events?card_id={node['id']}").json()
    source = next(source for source in catalog["sources"] if source.get("kind") == "document_action")
    assert source["operation_id"] == "document:example.prepared:load"
    assert {phase["phase"] for phase in source["events"]} == {"invocation"}
    original = services.operation_events.record

    def fail_return(event):
        original(event)
        if event.type is EventType.OPERATION_SUCCEEDED:
            raise RuntimeError("return event persistence failed")

    monkeypatch.setattr(services.operation_events, "record", fail_return)
    with pytest.raises(RuntimeError, match="return event persistence failed"):
        client.portal.call(lambda: invoke_document_action(services, node["id"], "load",
                                                         DocumentActionRequest(expected_revision=0)))
    assert read_document(services, node["id"])["value"] == {"content": "original"}
    assert not any(event.type is EventType.OPERATION_SUCCEEDED
                   for _, event in services.operation_events.after(0, limit=10000))
    monkeypatch.setattr(services.operation_events, "record", original)
    client.portal.call(lambda: invoke_document_action(services, node["id"], "load",
                                                     DocumentActionRequest(expected_revision=0)))
    assert read_document(services, node["id"])["value"] == {"content": "changed"}
    event = next(event for _, event in services.operation_events.after(0, limit=10000)
                 if event.type is EventType.OPERATION_SUCCEEDED)
    assert event.payload["operation_id"] == source["operation_id"]


def test_state_machine_owned_work_cannot_dispatch_legacy_continuation(client):
    class Continuations(NodeContinuationMixin):
        services = SimpleNamespace(run_manager=None)

        def collect_delegations(self, node_id):
            pass

        def state(self, node_id):
            # Even stale auto_continue metadata cannot override durable ownership.
            return {"attempts": [{"auto_continue": True, "continuation_owner": "state_machine"}]}

        def _continuation_parent(self, run_id):
            pytest.fail("State-machine-owned work reached legacy Run admission")

    assert client.portal.call(Continuations()._continue_board, "board") is False


def test_legacy_notification_run_ownership_is_present_from_admission(client):
    services = client.app.state.services
    sequence = services.operation_events.max_sequence()
    record = services.run_manager.store.create(agent_id="agent", runtime_provider_id="test", caller_kind="delegation",
        initial_lifecycle={"work_continuation": True})
    services.run_manager.store.update_status(record.run_id, RunStatus.RUNNING)
    events = [event for _, event in services.operation_events.after(sequence)]
    assert [event.payload["event"] for event in events] == ["run.created", "run.started"]
    assert all(event.payload["continuation_owner"] == "legacy" for event in events)


def test_recovered_work_uses_original_dispatch_identity_not_collecting_invocation(client):
    from backend.capabilities.events import publish_work_outcome
    from backend.plugins.execution import WorkOutcome
    from backend.tests.test_node_execution import setup_board
    services = client.app.state.services
    node, _, _, _ = setup_board(client)
    sequence = services.operation_events.max_sequence()
    original = {"invocation_id": "original-dispatch", "continuation_owner": "state_machine",
                "caller_object_id": "original-caller", "associations": []}
    with operation_context("later-collection", caller_object_id="collector"):
        for _ in range(2):
            publish_work_outcome(services, node["id"], WorkOutcome(item_id="a", status="interrupted"),
                                 operation=original, execution_id="stable-dispatch")
    events = [event for _, event in services.operation_events.after(sequence)]
    assert len(events) == 1
    assert events[0].payload["invocation_id"] == "original-dispatch"
    assert events[0].payload["caller_object_id"] == "original-caller"
    assert events[0].payload["continuation_owner"] == "state_machine"


def test_other_host_execution_types_retain_the_same_association_contract(client):
    from backend.sandbox import history
    from backend.tests.conftest import create_node
    services = client.app.state.services
    sandbox = create_node(client, "sandbox")

    async def scenario():
        release = asyncio.Event()

        async def work(operation_id):
            await release.wait()
            return {"ok": True}

        with operation_context("sandbox-invocation", caller_object_id="caller", target_object_id=sandbox["id"]) as invocation:
            result = await services.sandbox_operations.submit(None, sandbox["id"], "test", work, wait_seconds=0)
            assert result["status"] == "running"
            association, = invocation.payload()["associations"]
            assert association == {"kind": "sandbox_operation", "id": result["operation_id"],
                                   "object_id": sandbox["id"], "produced": True}
            receipt = next(item for item in history.read(services, sandbox["id"]) if item["id"] == result["operation_id"])
            assert receipt["operation"]["invocation_id"] == "sandbox-invocation"
            release.set()
            await services.sandbox_operations.wait(None, sandbox["id"], result["operation_id"], 1)

    client.portal.call(scenario)


@pytest.mark.parametrize("owner", ["state_machine", None])
def test_nested_capability_calls_have_independent_identities_and_associations(client, owner):
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)

    async def handler(context, capability, arguments):
        if arguments.get("outer"):
            for identity in ("first-object", "second-object"):
                await provider.invoke_tool(capability.agent_id, capability.id, {"object": identity})
        else:
            if active_operation.get().continuation_owner is None:
                active_operation.get().continuation_owner = "legacy"
            context.associate_execution("object", arguments["object"], produced=True)
        return {"ok": True}

    agent, target, _ = register_unknown_tool(client, handler)

    async def invoke():
        with operation_context("durable-action", caller_object_id=agent["id"], target_object_id=target["id"],
                               context_id="original-session", conversation_id="original-conversation",
                               continuation_owner=owner) as operation:
            await provider.invoke_tool(agent["id"], "operation:polish_surface",
                                       {"target": target["id"], "outer": True}, request_id="durable-action")
            assert [ref.id for ref in operation.associations] == ["first-object", "second-object"]
            assert operation.continuation_owner == (owner or "legacy")

    client.portal.call(invoke)
    completed = [event for _, event in services.operation_events.after(0, limit=10000)
                 if event.type is EventType.CAPABILITY_SUCCEEDED]
    assert len(completed) == 3
    first, second, parent = completed
    assert len({event.payload["invocation_id"] for event in completed}) == 3
    assert parent.payload["invocation_id"] == "durable-action"
    assert parent.payload["continuation_owner"] == (owner or "legacy")
    assert resolve_associated_objects(first, produced_only=True) == ["first-object"]
    assert resolve_associated_objects(second, produced_only=True) == ["second-object"]
    assert resolve_associated_objects(parent, produced_only=True) == ["first-object", "second-object"]
    for child in (first, second):
        assert child.payload["parent_invocation_id"] == "durable-action"
        assert child.payload["continuation_owner"] == (owner or "legacy")
        assert child.session_id == "original-session"
        assert child.conversation_id == "original-conversation"


def test_repeated_native_adapters_have_distinct_events_and_keep_parent_associations(client):
    from backend.node_documents import DocumentActionRequest, invoke_document_action
    from backend.tests.test_document_action_prepare import install_prepared_node

    services = client.app.state.services

    async def prepare(value, arguments):
        active_operation.get().associate("object", arguments["content"], produced=True)
        return arguments

    node, _ = install_prepared_node(client, prepare)

    async def handler(context, capability, arguments):
        for revision, content in enumerate(("first-object", "second-object")):
            await invoke_document_action(services, node["id"], "load",
                DocumentActionRequest(arguments={"content": content}, expected_revision=revision))
        return {"ok": True}

    agent, target, _ = register_unknown_tool(client, handler)
    client.portal.call(lambda: WorldAgentCapabilityProvider(services).invoke_tool(
        agent["id"], "operation:polish_surface", {"target": target["id"]}, request_id="parent-call"))
    events = [event for _, event in services.operation_events.after(0, limit=10000)]
    children = [event for event in events if event.type is EventType.OPERATION_SUCCEEDED]
    assert len(children) == 2
    assert len({event.payload["invocation_id"] for event in children}) == 2
    assert all(event.payload["parent_invocation_id"] == "parent-call" for event in children)
    assert [resolve_associated_objects(event, produced_only=True) for event in children] == [
        ["first-object"], ["second-object"]]
    parent = next(event for event in events if event.type is EventType.CAPABILITY_SUCCEEDED)
    assert resolve_associated_objects(parent, produced_only=True) == ["first-object", "second-object"]


def test_direct_node_action_keeps_explicit_durable_request_identity(client):
    from backend.node_documents import DocumentActionRequest, invoke_document_action
    from backend.tests.test_document_action_prepare import install_prepared_node

    async def prepare(value, arguments):
        active_operation.get().associate("object", "produced-object", produced=True)
        return {"content": "changed"}

    node, _ = install_prepared_node(client, prepare)
    services = client.app.state.services

    async def invoke():
        with operation_context("durable-node-action", continuation_owner="state_machine") as operation:
            await invoke_document_action(services, node["id"], "load", DocumentActionRequest(expected_revision=0),
                                         request_id="durable-node-action")
            assert [ref.id for ref in operation.associations] == ["produced-object"]

    client.portal.call(invoke)
    events = [event for _, event in services.operation_events.after(0, limit=10000)
              if event.type in {EventType.OPERATION_STARTED, EventType.OPERATION_SUCCEEDED}]
    assert len(events) == 2
    assert all(event.payload["invocation_id"] == "durable-node-action" for event in events)
    assert all(event.payload["continuation_owner"] == "state_machine" for event in events)
