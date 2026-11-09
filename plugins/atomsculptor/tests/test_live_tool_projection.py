"""Live equipment/edge grants through the specialist's actual adapter schema."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from google.adk.models.lite_llm import _get_completion_inputs
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import FunctionTool
from google.genai import types
from jsonschema import Draft202012Validator

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.tests.conftest import create_node, desktop_test_client_peer
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from open_agent_world.plugin_api.model_runtime import build_scoped_adk_tools
from oaw_atomsculptor.execution_steps import typed_step_functions
from oaw_atomsculptor.runtime import _role_definitions
from test_workflow import PLAN, evidence, tool


@pytest.mark.parametrize("equipment", [False, True])
def test_live_equipment_survives_role_and_stage_projection_and_executes(runtime_client, equipment):
    client, _, native = runtime_client
    agent, sandbox, skill, toolbox, edges = setup_skill(client, equipment=equipment, toolbox=True)
    hidden = create_node(client, "oaw.skills.skill", name="Hidden")
    provider = WorldAgentCapabilityProvider(client.app.state.services)

    async def boundary():
        definitions = await provider.list_tools(agent["id"])
        e = evidence()
        e.configure_tools(definitions)
        await tool(e, "finish_task", {"result": {**PLAN, "delivery": "report", "stages": PLAN["stages"][1:]}},
                   "Task completed.", role="planner")
        assert e.workflow.before_delegation("structure_builder") is None
        await tool(e, "inspect_sandbox", {"sandbox": sandbox["id"]}, {"state": "ready"})
        sdk = build_scoped_adk_tools(provider, agent["id"], _role_definitions(definitions, "structure_builder"))
        e.steps.enable_helpers()
        e.steps.available = True
        steps = [FunctionTool(f) for f in typed_step_functions(e, definitions,
            lambda capability, arguments: provider.invoke_tool(agent["id"], capability, arguments), asyncio.Event())]
        req = LlmRequest(model="openai/offline-projection", contents=[], config=types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[t._get_declaration() for t in [*sdk, *steps]])]))
        e.before_model("structure_builder", req, NS(types=types), 16384)
        _, wire_tools, _, _, _ = await _get_completion_inputs(req, req.model)
        wire = {t["function"]["name"]: t["function"]["parameters"] for t in wire_tools}
        assert {"read_skills", "run_skill_script", "run_modelling_step"} <= wire.keys()
        for schema in wire.values():
            Draft202012Validator.check_schema(schema)
        assert {"sandbox", "skill", "script"} <= set(wire["run_skill_script"]["required"])
        assert "toolbox" in wire["read_skills"]["required"]
        assert hidden["id"] not in str(wire)
        functions = {t.name: t for t in sdk}
        listing = await functions["read_skills"].run_async(args={"toolbox": toolbox["id"]}, tool_context=None)
        assert listing["skills"][0]["id"] == skill["id"]
        result = await functions["run_skill_script"].run_async(args={
            "sandbox": sandbox["id"], "skill": skill["id"], "script": "scripts/check.py"}, tool_context=None)
        assert result["exit_code"] == 0 and skill["id"] in native.last_argv[1]
        return functions

    functions = client.portal.call(boundary)
    if equipment:
        assert client.patch(f"/api/nodes/{toolbox['id']}", json={"equipment": None}).status_code == 200
    else:
        assert client.delete(f"/api/edges/{edges[1]['id']}").status_code == 200

    async def revoked():
        definitions = await provider.list_tools(agent["id"])
        assert "run_skill_script" not in {d.name for d in definitions}
        result = await functions["run_skill_script"].run_async(args={
            "sandbox": sandbox["id"], "skill": skill["id"], "script": "scripts/check.py"}, tool_context=None)
        assert result["ok"] is False and result["error"]["code"] == "permission_denied"
    client.portal.call(revoked)
