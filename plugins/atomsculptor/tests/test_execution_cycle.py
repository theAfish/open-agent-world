"""Cross-step execution, replanning, working history and budget closeout contracts."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from google.genai import types
from google.adk.models.llm_request import LlmRequest

from oaw_atomsculptor.harness import HarnessLimits, HarnessBudgetReached
from test_execution_steps import setup, prior


async def begin_optimization(e, objective):
    from test_workflow import execute
    await execute(e, "input.py")
    assert e.workflow.advance(e, "probe", "box", "input.py")["ok"]
    e.workflow.plan[1]["experiment"]["objective"] = objective


@pytest.mark.asyncio
@pytest.mark.parametrize("direction,values", [("maximize", [5, 5]), ("minimize", [5, 5])])
async def test_measured_stagnation_across_sources_is_advisory(direction, values):
    readings = iter(values)
    e, calls, run = await setup(stdout=lambda: json.dumps({"oaw_measurements": {"objective": {"name": "measured objective", "value": next(readings), "direction": direction, "unit": "count"}}}))
    await begin_optimization(e, {"name": "measured objective", "direction": direction, "unit": "count"})
    for i, value in enumerate(values):
        await run(script_path=f"trial{i}.py", source=f"print({value})")
        a = prior(e, objective={"name": "measured objective", "value": value, "direction": direction, "unit": "count"})
        result = e.workflow.assess_previous(e, a)
        assert result["ok"]
        assert result["observation"]["outcome"] == "continue"
    assert len(calls) == 4 and {c["stage"] for c in e.workflow.checkpoints} == {"probe"}
    action, allowed = e.actions.next_action(e)
    assert allowed is None or "run_modelling_step" in allowed
    assert e.workflow.host_signal["advisory"] is True
    assert e.workflow.before_tool("structure_builder", "run_modelling_step", {}, e) is None


@pytest.mark.asyncio
async def test_improvement_resets_stagnation_and_invalid_numbers_do_not_commit_assessments():
    readings = iter([5, 6, 4, 7, 8, 9])
    e, calls, run = await setup(stdout=lambda: json.dumps({"oaw_measurements": {"objective": {"name": "score", "value": next(readings), "direction": "maximize"}}}))
    await begin_optimization(e, {"name": "score", "direction": "maximize"})
    for i, value in enumerate([5, 6, 4, 7, 8, 9]):
        await run(script_path=f"trial{i}.py", source=f"print({value})")
        if i == 0:
            assert not e.workflow.assess_previous(e, prior(e, objective={"name": "score", "value": float('nan'),
                                                                         "direction": "maximize"}))["ok"]
            assert len(e.experiment_history) == 1  # only the prior probe assessment
        assert e.workflow.assess_previous(e, prior(e, objective={"name": "score", "value": value,
                                                               "direction": "maximize"}))["observation"]["outcome"] == "continue"


@pytest.mark.asyncio
async def test_repeated_unchanged_code_failure_stays_in_local_repair_without_an_objective():
    e, calls, run = await setup(exit_code=1)
    for i in range(2):
        if i == 0:
            assert (await run())["step"]["state"] == "finished"
        else:
            assert (await run(source="", edits=[{"old":"measurement","new":"fixed"}]))["step"]["state"] == "finished"
        result = e.workflow.assess_previous(e, prior(e))
    assert result["observation"]["outcome"] == "continue" and e.workflow.host_signal is None
    assert e.workflow.local_repair(e)["script_path"] == "step.py"
    assert len(calls) == 4 and not e.outputs


def history(first, count=16, *, signed=False):
    contents = [types.Content(role="user", parts=[types.Part(text=first)])]
    for i in range(count):
        contents.extend([
            types.Content(role="model", parts=[types.Part(thought_signature=b"signed" if signed and i == 0 else None,
                function_call=types.FunctionCall(id=str(i), name="execute_command", args={"source": "x" * 1800}))]),
            types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=str(i), name="execute_command", response={"exit_code": 0, "stdout": f"measured-{i}"}))])])
    return contents


@pytest.mark.asyncio
async def test_host_frames_do_not_apply_a_second_character_history_limit():
    e, calls, run = await setup()
    await run()
    e.limits = HarnessLimits(working_history_chars=8000)
    req = LlmRequest(contents=history(e.original_request), config=types.GenerateContentConfig(max_output_tokens=81920))
    e.before_model("structure_builder", req, NS(types=types), 81920)
    assert len(req.contents) == 35 and req.contents[0].parts[0].text == e.original_request
    checkpoints = [p.text for c in req.contents for p in c.parts if p.text and 'HOST working-context checkpoint' in p.text]
    assert not checkpoints
    assert 'working_history_chars' not in e.limits.model_dump()  # Legacy cards still load.
    assert req.config.max_output_tokens == 81920
    pairs = [(p.function_call.id if p.function_call else p.function_response.id) for c in req.contents for p in c.parts
             if p.function_call or p.function_response]
    assert all(pairs[i] == pairs[i+1] for i in range(0, len(pairs), 2))
    host = json.loads(req.contents[-1].parts[0].text.split('\n', 1)[1])
    assert host["current_saved_source"]["source_head"] == "print('measurement')"
    stable = [p.text for c in req.contents for p in c.parts if p.text and p.text.startswith("HOST STABLE CONTEXT")]
    assert len(stable) == 1 and json.loads(stable[0].split('\n', 1)[1])['original_user_request'] == e.original_request
    assert json.loads(stable[0].split('\n', 1)[1])['modelling_plan_context']['plan'] is not None
    # Host frames replace themselves; native input accounting owns compaction.
    e.before_model("structure_builder", req, NS(types=types), 81920)
    assert not any('HOST working-context checkpoint' in (p.text or '') for c in req.contents for p in c.parts)
    assert 'measured-15' in req.model_dump_json() and 'measured-0' in req.model_dump_json()


@pytest.mark.asyncio
async def test_signed_history_and_unresolved_suffix_are_never_dropped_or_fabricated():
    e, calls, run = await setup()
    e.limits = HarnessLimits(working_history_chars=8000)
    req = LlmRequest(contents=history(e.original_request, signed=True), config=types.GenerateContentConfig())
    prefix = [c.model_dump_json(exclude_none=True) for c in req.contents]
    e.before_model("structure_builder", req, NS(types=types), 81920)
    # Append host frames without changing any part of the signed prefix.
    assert [c.model_dump_json(exclude_none=True) for c in req.contents[:len(prefix)]] == prefix
    assert len(req.contents) == 35 and req.contents[-2].parts[0].text.startswith("HOST STABLE CONTEXT")
    assert req.contents[1].parts[0].thought_signature == b"signed"
    req = LlmRequest(contents=history(e.original_request), config=types.GenerateContentConfig())
    req.contents.append(types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
        id="pending", name="execute_command", args={}))]))
    e.before_model("structure_builder", req, NS(types=types), 81920)
    assert any(p.function_call and p.function_call.id == "pending" for c in req.contents for p in c.parts)
    assert not any(p.function_response and p.function_response.id == "pending" for c in req.contents for p in c.parts)


def test_default_request_budget_migrates_without_mutation_and_explicit_budget_gets_closeout():
    old = {"max_model_requests": 32}
    assert HarnessLimits.model_validate(old).max_model_requests is None and old['max_model_requests'] == 32
    assert HarnessLimits().max_model_requests is None
    assert HarnessLimits(max_model_requests=45).max_model_requests == 45


@pytest.mark.asyncio
async def test_budget_closeout_blocks_new_construction_and_preserves_unknown_outcome():
    e, calls, run = await setup()
    await run()
    e.limits = HarnessLimits(max_model_requests=4)
    req = LlmRequest(contents=[], config=types.GenerateContentConfig(max_output_tokens=81920))
    e.model_requests = 2
    e.before_model("structure_builder", req, NS(types=types), 81920)
    assert e.actions.allowed == {"finish_task"} and e.closing_reason
    e.executions['unknown'] = [(('box', 'step.py'), e.latest_experiment().version)]
    checkpoint = e.closeout_checkpoint('budget')
    assert checkpoint['status'] == 'unfinished' and checkpoint['unknown_executions'] == ['unknown']
    assert checkpoint['scripts'][0]['command_id'] == 'native-op'
    e.model_requests = 4
    with pytest.raises(HarnessBudgetReached):
        e.before_model("structure_builder", req, NS(types=types), 81920)


@pytest.mark.asyncio
async def test_real_adk_budget_after_execution_saves_native_checkpoint_and_returns_failed_outcome(tmp_path):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from backend.agents.models import ScopedToolDefinition
    from backend.persistence.database import Database
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    from backend.state import StateStore
    from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
    from oaw_atomsculptor.runtime import AtomSculptorRuntime
    from test_workflow import PLAN, REQUEST
    import hashlib

    class Fixture(BaseLlm):
        model: str = 'openai/cycle-fixture'
        async def generate_content_async(self, request, stream=False):
            host = next(json.loads(p.text.split('\n', 1)[1]) for c in reversed(request.contents) for p in c.parts
                        if p.text and p.text.startswith('HOST RUN EVIDENCE'))
            role = host['execution_role']
            if role == 'planner':
                name, args = 'finish_task', {'result': {**PLAN, 'delivery': 'report'}}
            elif role == 'atom_sculptor':
                name = 'planner' if host['modelling_workflow']['plan'] is None else 'structure_builder'
                args = {'request': REQUEST}
            else:
                name, args = 'run_modelling_step', {'sandbox': 'box', 'script_path': 'sample.py', 'source': 'print(1)',
                    'question': 'Measure one value', 'expected_output': 'Print that value', 'stop_condition': 'One sample'}
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
                name=name, id=f'{role}-call', args=args))]), usage_metadata=types.GenerateContentResponseUsageMetadata(
                    candidates_token_count=1024) if role == 'structure_builder' else None)

    calls = []
    class Provider:
        async def list_tools(self, agent_id):
            return [ScopedToolDefinition('operation:'+name, name, 'native') for name in ('write_sandbox_text_file', 'execute_command')]
        async def invoke_tool(self, agent_id, capability, args):
            calls.append(capability)
            if capability.endswith('write_sandbox_text_file'):
                return {'written': len(args['content'])}
            return {'exit_code': 0, 'command_id': 'sample-command', 'stdout': '1'}

    database = Database(tmp_path/'world.sqlite3')
    try:
        RunStore(database).create(run_id='cycle-run', agent_id='fixture', runtime_provider_id='atomsculptor.adk-team', caller_kind='user')
        state = StateStore(database, create_builtin_registry())
        runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=NS(
            resolve_runtime=lambda _: RuntimeModelConnection('openai', 'cycle-fixture', 'https://model.invalid', None),
            context_limits=lambda _: (128000, 81920)))
        runtime.reasoning_store = state
        runtime._model = lambda *_: Fixture()
        config = AgentConfig('fixture', 'Fixture', provider_config={'harness': {'max_model_requests': 6}})
        await runtime.create_agent(config)
        events = [event async for event in runtime.execute(config, NS(run_id='cycle-run', context_id='cycle'),
                                                           NS(prompt=REQUEST, task_prompt=REQUEST))]
        assert events[-1].run_status == 'failed' and 'Task unfinished' in events[-1].payload['text']
        checkpoint = state.get(state.get_scope('run', 'cycle-run'), 'execution_checkpoint')
        assert checkpoint['scripts'][0]['command_id'] == 'sample-command'
        assert checkpoint['scripts'][0]['exit_code'] == 0 and checkpoint['status'] == 'unfinished'
        assert calls == ['operation:write_sandbox_text_file', 'operation:write_sandbox_text_file', 'operation:execute_command']
        assert runtime.active == {}
    finally:
        database.close()
