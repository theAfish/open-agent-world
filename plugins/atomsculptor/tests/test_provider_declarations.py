"""Workflow declarations at the actual ADK/LiteLLM request boundary."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from google.adk.models.lite_llm import _get_completion_inputs
from google.adk.tools import FunctionTool
from google.genai import types
from jsonschema import Draft202012Validator

from oaw_atomsculptor.execution_steps import STEP_TOOL_MODES, typed_step_functions
from oaw_atomsculptor.workflow import validate_plan
from test_execution_actions import TOOLS, prepare, request
from test_execution_steps import prior, setup
from test_subtask_lifecycle import ACTION, MEASURED
from test_workflow import PLAN, evidence, tool


def action_contracts(schema):
    """Visit schema nodes, not arbitrary property names or literal values."""
    if not isinstance(schema, dict):
        return
    if {"input_paths", "scope", "artifact"} <= schema.get("properties", {}).keys():
        yield schema
    for keyword in ("properties", "$defs", "definitions", "patternProperties"):
        for child in schema.get(keyword, {}).values():
            yield from action_contracts(child)
    for keyword in ("items", "additionalProperties", "not", "if", "then", "else"):
        yield from action_contracts(schema.get(keyword))
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(keyword, []):
            yield from action_contracts(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["planner", "initial", "ready", "unassessed", "continuation", "build", "review", "deliver"])
async def test_workflow_schemas_survive_real_adapter_in_every_request_state(state):
    role = "structure_builder"
    if state == "planner":
        e = evidence()
        role = "planner"
    else:
        e, _, run = await setup(stdout="actual measurement" if state in {"build", "deliver"} else MEASURED)
        if state != "initial":
            await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
        if state in {"unassessed", "continuation", "build", "review", "deliver"}:
            assert (await run())["ok"]
        if state == "continuation":
            assert e.workflow.assess_previous(e, prior(e, next_experiment=ACTION))["ok"]
        if state in {"build", "deliver"}:
            assert e.workflow.assess_previous(e, prior(e))["ok"]
            assert e.workflow.advance(e, "probe", "box", "step.py")["ok"]
        if state == "deliver":
            assert (await run(script_path="build.py"))["ok"]
            assert e.workflow.assess_previous(e, prior(e))["ok"]
            assert e.workflow.advance(e, "build", "box", "build.py")["ok"]
        if state == "review":
            assert e.workflow.assess_previous(e, prior(e, assumption_invalid=True))["ok"]
            assert await tool(e, "finish_task", {"result": {
                "status": "needs_plan_review", "reason": "Measured inputs contradict the plan",
                "sandbox": "box", "script_path": "step.py"}}, "Task completed.") == "Task completed."
            role = "planner"

    if role == "planner":
        req = request({"finish_task"})
        e.before_model(role, req, SimpleNamespace(types=types), 16384)
    else:
        req = request(TOOLS | set(STEP_TOOL_MODES))
        sdk = {f.__name__: FunctionTool(f)._get_declaration()
               for f in typed_step_functions(e, [], None, asyncio.Event())}
        req.config.tools = [types.Tool(function_declarations=[sdk.get(d.name, d)
            for t in req.config.tools for d in t.function_declarations])]
        prepare(e, req)
    _, wire_tools, _, _, choice = await _get_completion_inputs(req, req.model)
    assert wire_tools
    if role == "structure_builder":
        assert choice == "required"
        assert "advance_modelling_stage" in {t["function"]["name"] for t in wire_tools}
    contracts = []
    for entry in wire_tools:
        schema = entry["function"]["parameters"]
        Draft202012Validator.check_schema(schema)
        contracts.extend(action_contracts(schema))
    assert contracts or state == "deliver"
    for contract in contracts:
        # Some constrained function-call decoders cannot express uniqueItems.
        # Host validation still enforces distinct paths before using the action.
        paths = contract["properties"]["input_paths"]
        assert "uniqueItems" not in paths
        assert paths["maxItems"] == 8
        assert paths["items"] == {"type": "string", "minLength": 1, "maxLength": 512}


@pytest.mark.asyncio
async def test_duplicate_inputs_remain_rejected_by_host_for_plans_and_continuations():
    duplicated = {**deepcopy(ACTION), "input_paths": ["same.npz", "same.npz"]}
    proposed = deepcopy(PLAN)
    proposed["stages"][0]["experiment"] = duplicated
    parsed, issues = validate_plan(proposed)
    assert parsed is None
    assert any(i["field"].endswith("experiment.input_paths") for i in issues)

    e, _, run = await setup(stdout=MEASURED)
    assert (await run())["ok"]
    rejected = e.workflow.assess_previous(e, prior(e, next_experiment=duplicated))
    assert rejected["error"]["code"] == "subtask_contract"
    assert not e.experiment_observations
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    rejected = await tool(e, "finish_task", {"result": {
        "status": "subtask_complete", "next_experiment": duplicated}}, "Task completed.")
    assert rejected["error"]["code"] == "subtask_contract"
    assert not e.workflow.subtask_history and not e.workflow.checkpoints
