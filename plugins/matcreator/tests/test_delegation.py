"""Real host/capability dispatch with controlled providers; no external model calls."""
import asyncio
from dataclasses import replace

import pytest
import pytest_asyncio

from backend.agents import AgentEvent, AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.capabilities.projection import authorized_resources
from backend.card_state import state_session
from backend.conversations.models import ConversationSessionCreate
from backend.skill_runtime import SKILL_SELECTOR
from backend.config import Settings
from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError
from backend.legions.models import LegionInstantiate
from backend.legions.presets import preset_record
from backend.node_documents import DocumentActionRequest, invoke_document_action, read_document, write_document
from backend.plugins.loader import load_plugin_registry
from backend.runs.models import RunStatus
from backend.services import create_services
from backend.plugins.summoning import SummoningAction
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_runs import RecordingProvider
from backend.world.models import CardCreate


class ResearchRuntime(RecordingProvider):
    def __init__(self):
        super().__init__()
        self.script = None
        self.children = {}
        self.errors = []

    async def execute(self, config, context, runtime_input):
        self.contexts.append(context)
        if context.caller.kind != "summon":
            try:
                await self.script(context)
            except Exception as error:
                self.errors.append(error)
                raise
            text = "Coordinator finished"
        else:
            gate = asyncio.get_running_loop().create_future()
            self.children[context.run_id] = (context, runtime_input.prompt, gate)
            text = await gate
            if text == "fail":
                raise RuntimeError("Calculation failed: missing package")
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE, {"text": text})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {"text": text}, run_status=RunStatus.SUCCEEDED)


@pytest_asyncio.fixture
async def research(tmp_path):
    registry = load_plugin_registry()
    runtime = ResearchRuntime()
    install_test_plugin(registry, "test.research", lambda r: r.register_runtime_provider("test.research", lambda _: runtime))
    services = create_services(replace(Settings.for_data_root(tmp_path), agent_runtime="test.research"), plugins=registry)
    instance = await services.instantiate_legion("matcreator.research", LegionInstantiate(), record=preset_record("matcreator.research", registry))
    ids = instance.node_ids
    board = read_document(services, ids["tasks"])
    await invoke_document_action(services, ids["tasks"], "create_plan", DocumentActionRequest(expected_revision=board["revision"], arguments={
        "title": "Independent structures", "goal": "Compare two structures", "tasks": [
            {"id": "a", "title": "Build A", "acceptance": "32 atoms"},
            {"id": "b", "title": "Build B", "acceptance": "108 atoms"},
            {"id": "compare", "title": "Compare", "depends_on": ["a", "b"]}]}))
    provider = WorldAgentCapabilityProvider(services)
    async def execute(action, **args):
        return await provider.invoke_tool(ids["agent"], "operation:task_board_execute", {"board": ids["tasks"], "action": action, **args})
    yield services, ids, runtime, execute
    await services.shutdown()


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.005)


def dispatch_args(ids, snapshot, index, request_id):
    return dict(item_id=snapshot["execution"]["items"][index]["id"], library_id=ids["barracks"], agent_id=ids["executor"],
        expected_revision=snapshot["document"]["revision"], request_id=request_id)


@pytest.mark.asyncio
async def test_parallel_dispatch_dedup_private_context_wait_and_review(research):
    services, ids, runtime, execute = research
    launched = asyncio.Event()
    captured = {}
    async def script(context):
        snapshot = await execute("collect")
        with pytest.raises(ResourceValidationError, match="not ready"):
            await execute("delegate", **dispatch_args(ids, snapshot, 2, "too-early"))
        args = dispatch_args(ids, snapshot, 0, "dispatch-a")
        first = await execute("delegate", **args)
        again = await execute("delegate", **args)
        assert again["attempt"]["instance_id"] == first["attempt"]["instance_id"]
        with pytest.raises(ConflictError):
            await execute("delegate", **{**args, "agent_id": "another-template"})
        with pytest.raises(ConflictError, match="uncollected"):
            await execute("delegate", **dispatch_args(ids, first, 0, "duplicate-task"))
        second = await execute("delegate", **dispatch_args(ids, first, 1, "dispatch-b"))
        handles = [first["attempt"]["instance_id"], second["attempt"]["instance_id"]]
        captured.update(first=first, second=second)
        launched.set()
        polled = await execute("wait", instance_ids=handles, timeout_seconds=0)
        assert polled["execution"]["active"]
        finished = await execute("wait", instance_ids=handles, timeout_seconds=5)
        assert [t["status"] for t in finished["document"]["value"]["plans"][0]["tasks"]] == ["review", "review", "pending"]
        assert not finished["execution"]["items"][2]["ready"]
        assert all(e["applied"] for e in finished["execution"]["attempts"])
        assert "32 atoms" in finished["execution"]["attempts"][0]["text"]
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Research", context_id=services.card_state.default_session(ids["tasks"]))
    await until(lambda: launched.is_set() or runtime.errors)
    assert not runtime.errors
    await until(lambda: len(runtime.children) == 2)
    contexts = [entry[0] for entry in runtime.children.values()]
    assert all(c.parent_run_id == root.run_id and c.context_id != root.context_id for c in contexts)
    assert len({c.context_id for c in contexts}) == 2
    assert len({c.task_id for c in contexts}) == 2
    assert captured["first"]["attempt"]["output_directory"] != captured["second"]["attempt"]["output_directory"]
    for index, (context, prompt, gate) in enumerate(runtime.children.values()):
        assert "acceptance_criteria" in prompt
        capabilities = services.capabilities.derive(context.agent_id).capabilities
        assert {ids[key] for key in ("sandbox", "knowledge")} <= {cap.target_id for cap in capabilities}
        graph = read_document(services, ids["knowledge"])["value"]
        assert {skill["node_id"] for skill in graph["skills"]} <= authorized_resources(services, capabilities, SKILL_SELECTOR).keys()
        assert not any(cap.target_id in {ids["tasks"], ids["barracks"], ids["conversation"]} for cap in capabilities)
        gate.set_result("Verified 32 atoms" if index == 0 else "Verified 108 atoms")
    await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), 5)
    assert not runtime.errors
    assert services.run_manager.get_run(root.run_id).status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_parent_stop_cancels_both_children_and_collect_preserves_attempts(research):
    services, ids, runtime, execute = research
    launched = asyncio.Event()
    async def script(context):
        first = await execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "a"))
        second = await execute("delegate", **dispatch_args(ids, first, 1, "b"))
        launched.set()
        await execute("wait", instance_ids=[e["instance_id"] for e in second["execution"]["attempts"]], timeout_seconds=60)
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Research")
    await until(lambda: launched.is_set() or runtime.errors)
    assert not runtime.errors
    await until(lambda: len(runtime.children) == 2)
    await services.run_manager.cancel_run(root.run_id)
    result = await services.node_execution.delegation_action(ids["tasks"], "collect", {})
    assert all(e["status"] == "cancelled" for e in result["execution"]["attempts"])
    assert [t["status"] for t in result["document"]["value"]["plans"][0]["tasks"]] == ["blocked", "blocked", "pending"]
    assert len(services.summoning.records()) == 2


@pytest.mark.asyncio
async def test_revoked_summoning_does_not_dispatch_or_return_wait_results(research):
    services, ids, runtime, execute = research
    ready = asyncio.Event()
    edge = next(e for e in services.world.list_edges() if e.source == ids["summoning"])
    async def script(context):
        first = await execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "a"))
        ready.set()
        with pytest.raises(PermissionDeniedError):
            await execute("wait", instance_ids=[first["attempt"]["instance_id"]], timeout_seconds=5)
        with pytest.raises(PermissionDeniedError):
            await execute("delegate", **dispatch_args(ids, first, 1, "b"))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Research")
    await until(lambda: ready.is_set() or runtime.errors)
    assert not runtime.errors
    await until(lambda: len(runtime.children) == 1)
    await services.delete_edge(edge.id)
    next(iter(runtime.children.values()))[2].set_result("Finished")
    await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), 5)
    assert not runtime.errors
    assert len(services.summoning.records()) == 1


@pytest.mark.asyncio
async def test_failure_retry_acceptance_and_durable_reconciliation(research):
    services, ids, runtime, execute = research
    ready = asyncio.Event()
    captured = {}
    async def script(context):
        # An unrelated command can already be running in the shared Sandbox.
        async with services._portable_state_gate.execution():
            first = await asyncio.wait_for(execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "first")), 2)
        captured["first"] = first["attempt"]
        ready.set()
        failed = await execute("wait", instance_ids=[first["attempt"]["instance_id"]], timeout_seconds=5)
        assert failed["execution"]["attempts"][0]["status"] == "failed"
        assert failed["document"]["value"]["plans"][0]["tasks"][0]["status"] == "blocked"
        retry = await execute("delegate", **dispatch_args(ids, failed, 0, "retry"))
        captured["retry"] = retry["attempt"]
        complete = await execute("wait", instance_ids=[retry["attempt"]["instance_id"]], timeout_seconds=5)
        assert complete["document"]["value"]["plans"][0]["tasks"][0]["status"] == "review"
        assert captured["retry"]["output_directory"] != captured["first"]["output_directory"]
        plan = complete["document"]["value"]["plans"][0]
        await invoke_document_action(services, ids["tasks"], "update_task", DocumentActionRequest(
            expected_revision=complete["document"]["revision"], arguments={"plan_id": plan["id"], "task_id": "a",
                "status": "done", "result": "Read file and verified 32 atoms", "outputs": [retry["attempt"]["output_directory"] + "/copper.xyz"]}))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Retry study")
    await until(lambda: ready.is_set() or runtime.errors)
    assert not runtime.errors
    await until(lambda: len(runtime.children) == 1)
    next(iter(runtime.children.values()))[2].set_result("fail")
    await until(lambda: len(runtime.children) == 2 or runtime.errors)
    assert not runtime.errors
    list(runtime.children.values())[1][2].set_result("32 atoms; copper.xyz")
    await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), 5)
    assert not runtime.errors
    # Simulate a crash after Run admission but before saving its board/instance handle.
    state = services.node_execution.state(ids["tasks"])
    state["attempts"][0].update(instance_id=None, run_id=None, applied=False)
    services.node_execution.save(ids["tasks"], state)
    # Reconciliation of old attempts must not overwrite a later accepted attempt.
    recovered = await services.node_execution.delegation_action(ids["tasks"], "collect", {})
    assert recovered["execution"]["attempts"][0]["run_id"] == captured["first"]["run_id"]
    assert recovered["document"]["value"]["plans"][0]["tasks"][0]["status"] == "done"
    await services.shutdown()
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        saved = await restarted.node_execution.delegation_action(ids["tasks"], "collect", {})
        assert len(saved["execution"]["attempts"]) == 2
        assert saved["document"]["value"]["plans"][0]["tasks"][0]["status"] == "done"
        assert len(restarted.summoning.records()) == 2
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_summoning_wait_timeout_and_followup_retain_private_context(research):
    services, ids, runtime, execute = research
    ready = asyncio.Event()
    captured = {}
    async def script(context):
        summon = services.summoning
        cap = services.node_execution.summoning_capability(ids["agent"], ids["barracks"])
        first = await summon.action(ids["barracks"], SummoningAction(action="summon", agent_id=ids["executor"], prompt="Inspect inputs", wait=False), capability=cap)
        captured.update(first=first)
        ready.set()
        pending = await summon.action(ids["barracks"], SummoningAction(action="wait", instance_ids=[first["id"]], timeout_seconds=0), capability=cap)
        assert pending["pending_instance_ids"] == [first["id"]]
        done = await summon.action(ids["barracks"], SummoningAction(action="wait", instance_ids=[first["id"]], timeout_seconds=5), capability=cap)
        assert done["pending_instance_ids"] == []
        second = await summon.action(ids["barracks"], SummoningAction(action="message", instance_id=first["id"], prompt="Refine result", wait=False), capability=cap)
        captured.update(second=second)
        await summon.action(ids["barracks"], SummoningAction(action="wait", instance_ids=[first["id"]], timeout_seconds=5), capability=cap)
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Research", context_id="main")
    await until(lambda: ready.is_set() or runtime.errors)
    assert not runtime.errors
    await until(lambda: len(runtime.children) == 1)
    next(iter(runtime.children.values()))[2].set_result("Found inputs")
    await until(lambda: len(runtime.children) == 2 or runtime.errors)
    assert not runtime.errors
    list(runtime.children.values())[1][2].set_result("Refined result")
    await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), 5)
    assert not runtime.errors
    contexts = [c[0] for c in runtime.children.values()]
    assert contexts[0].context_id == contexts[1].context_id != "main"


@pytest.mark.asyncio
async def test_research_board_adopts_legacy_data_once_and_session_creation_stays_lazy(research):
    services, ids, _, _ = research
    board = await services.create_card(CardCreate(type="matcreator.tasks", parent_id=ids["group"], name="Existing research"))
    value = read_document(services, ids["tasks"])["value"]
    value["plans"][0]["session_id"] = "old-descriptive-label"
    legacy = services.state.ensure_scope("node_document", board.id, schema_id="core.node_document")
    services.state.set(legacy, "document", value)
    a = services.card_state.default_session(board.id)
    b = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="B"))
    assert services.card_state.existing(board.id) == []
    with state_session(b.id):
        assert read_document(services, board.id)["value"] == {"plans": []}
    with state_session(a):
        restored = read_document(services, board.id)
        assert restored["revision"] == 1 and restored["value"] == value
    current = services.world.get_card(board.id)
    assert current.config == board.config and current.state_scope == "session"
    assert current.state_scope_override is None
    assert services.plugins.node_type(board.type).state.user_configurable is False
    # A legacy descriptive label survives, but new tools never request a session key.
    from oaw_matcreator.tasks import CreatePlan
    assert "session_id" not in CreatePlan.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_delegated_results_and_deletion_guards_follow_the_origin_session(research):
    services, ids, runtime, execute = research
    a = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="A"))
    b = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="B"))
    initial = read_document(services, ids["tasks"])["value"]
    with state_session(a.id):
        write_document(services, ids["tasks"], initial, 0)
    ready = asyncio.Event()
    captured = {}

    async def script(context):
        first = await execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "same-request"))
        captured["first"] = first
        ready.set()
        captured["finished"] = await execute("wait", instance_ids=[first["attempt"]["instance_id"]], timeout_seconds=5)

    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Research A", context_id=a.id)
    await until(lambda: ready.is_set() or runtime.errors)
    assert not runtime.errors
    await until(lambda: len(runtime.children) == 1)
    with state_session(b.id):
        empty = await services.node_execution.delegation_action(ids["tasks"], "collect", {})
        assert empty["document"]["value"] == {"plans": []}
        assert empty["execution"]["attempts"] == [] and not empty["execution"]["active"]
        # Same task IDs in B must not receive A's report or execution attempts.
        write_document(services, ids["tasks"], initial, 0)
        await services.node_execution.stop(ids["tasks"])
        assert not next(iter(runtime.children.values()))[2].done()
        with pytest.raises(ResourceValidationError, match="not assigned"):
            await services.node_execution.delegation_action(ids["tasks"], "stop", {"instance_id": captured["first"]["attempt"]["instance_id"]})
        with pytest.raises(ConflictError):
            await services.delete_cards([ids["tasks"]])
        with pytest.raises(ConflictError):
            await services.delete_conversation_session(ids["conversation"], a.id)
        with pytest.raises(ConflictError):
            await services.delete_conversation_group(ids["conversation"], a.group_id)
        next(iter(runtime.children.values()))[2].set_result("A verified output")
        await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), 5)
        assert not runtime.errors
        assert read_document(services, ids["tasks"])["value"] == initial
        assert services.node_execution.state(ids["tasks"])["attempts"] == []
    assert captured["finished"]["document"]["value"]["plans"][0]["tasks"][0]["status"] == "review"
    with state_session(a.id):
        attempt = services.node_execution.state(ids["tasks"])["attempts"][0]
        assert attempt["text"] == "A verified output" and attempt["applied"]
        # Recover an unapplied terminal result in an inactive session on restart.
        ledger = services.node_execution.state(ids["tasks"])
        ledger["attempts"][0]["applied"] = False
        services.node_execution.save(ids["tasks"], ledger)
    namespaces = services.card_state.existing(ids["tasks"])
    await services.shutdown()
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        assert restarted.card_state.existing(ids["tasks"]) == namespaces
        with state_session(a.id):
            assert restarted.node_execution.state(ids["tasks"])["attempts"][0]["applied"]
            assert read_document(restarted, ids["tasks"])["value"]["plans"][0]["tasks"][0]["status"] == "review"
        with state_session(b.id):
            assert read_document(restarted, ids["tasks"])["value"] == initial
            await restarted.delete_conversation_session(ids["conversation"], a.id)
            assert ("session", a.id) not in restarted.card_state.existing(ids["tasks"])
            assert read_document(restarted, ids["tasks"])["value"] == initial
    finally:
        await restarted.shutdown()
