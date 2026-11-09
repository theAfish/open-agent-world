"""Advertised contracts at native/SDK/Skill boundaries; no model or network calls."""
import json
import re
import runpy
from types import SimpleNamespace as NS

import pytest
from jsonschema import Draft202012Validator

from backend.plugins.builtin import create_builtin_registry
from backend.capabilities.projection import ProjectedOperation
from backend.agents.models import ScopedToolDefinition
from open_agent_world.plugin_api.sandbox import TEXT_EDITS_SCHEMA
from oaw_atomsculptor import AtomSculptorPlugin
from oaw_atomsculptor.execution_steps import step_tool_schema
from oaw_atomsculptor.runtime import _role_definitions
from oaw_atomsculptor.skill_package import atomsculptor_skills
from skill_materialization import skill_scripts
from test_typed_execution import COMMON, native_sdk_fixture
from test_execution_steps import prior


def test_validation_description_and_sdk_batch_policy_agree():
    import asyncio
    from google.adk.tools import FunctionTool
    from google.genai import types
    from oaw_atomsculptor.delivery import validation_step_function
    from test_execution_actions import setup, request
    e = setup()
    function = validation_step_function(e, [], None, asyncio.Event())
    declaration = FunctionTool(function)._get_declaration()
    assert 'Call alone' in declaration.description
    req = request({'read_tool_result', declaration.name})
    req.config.tools[0].function_declarations = [
        types.FunctionDeclaration(name='read_tool_result'), declaration]
    read = types.Part(function_call=types.FunctionCall(id='read', name='read_tool_result', args={}))
    validate = types.Part(function_call=types.FunctionCall(id='validate', name=declaration.name, args={}))
    e.actions.prepare_response(e, [read, validate], req)
    rejection = e.actions.rejected_call('structure_builder', declaration.name, 'validate')
    assert rejection['error']['code'] == 'non_readonly_tool_batch'
    assert rejection['error']['command_dispatched'] is False
    assert e.actions.rejected_call('structure_builder', 'read_tool_result', 'read') is None
    e.actions.prepare_response(e, [validate], req)
    assert e.actions.rejected_call('structure_builder', declaration.name, 'validate') is None


@pytest.mark.asyncio
async def test_patch_schema_reuses_native_bounds_and_rejection_leaves_source_untouched():
    registry = create_builtin_registry()
    native = registry.capability_definition("sandbox.write_text_file").input_schema["properties"]["edits"]
    schema = step_tool_schema("patch_modelling_step")
    assert schema["properties"]["edits"] == native == TEXT_EDITS_SCHEMA
    e, files, calls, invoke = await native_sdk_fixture()
    source = "\n".join(f"v{i:02} = {i}" for i in range(21))
    assert (await invoke("run_modelling_step", dict(COMMON, source=source)))["ok"]
    edits = [{"old": f"v{i:02} = {i}", "new": f"v{i:02} = {i + 100}"} for i in range(21)]
    args = dict(COMMON, edits=edits, previous_assessment=prior(e))
    assert not Draft202012Validator(schema).is_valid(args)
    rejected = await invoke("patch_modelling_step", args)
    assert rejected["error"]["invalid_fields"] == ["edits"]
    assert files["probe.py"] == source and len(calls) == 2
    assert not Draft202012Validator(schema).is_valid(dict(COMMON, edits=[{"old": "", "new": "x"}]))
    args["edits"] = edits[:20]
    Draft202012Validator(schema).validate(args)
    assert (await invoke("patch_modelling_step", args))["ok"]
    assert "v19 = 119" in files["probe.py"] and "v20 = 20" in files["probe.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize("inputs", [None, []])
async def test_omitted_and_empty_input_lists_both_mean_no_inputs(inputs):
    e, files, calls, invoke = await native_sdk_fixture()
    args = dict(COMMON, source="print(1)")
    if inputs is not None:
        args["input_artifacts"] = inputs
    Draft202012Validator(step_tool_schema("run_modelling_step")).validate(args)
    result = await invoke("run_modelling_step", args)
    assert result["ok"] and files["probe.py"] == "print(1)" and len(calls) == 2
    assert not result["step"]["input_artifacts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["question", "expected_output", "stop_condition"])
async def test_step_prose_boundary_is_visible_before_dispatch(field):
    _, files, calls, invoke = await native_sdk_fixture()
    args = dict(COMMON, source="print(1)", **{field: "x" * 12001})
    assert not Draft202012Validator(step_tool_schema("run_modelling_step")).is_valid(args)
    result = await invoke("run_modelling_step", args)
    assert result["error"]["invalid_fields"] == [field] and not files and not calls


@pytest.mark.asyncio
async def test_missing_assessment_field_is_located_and_retained_source_can_resume():
    e, files, calls, invoke = await native_sdk_fixture()
    assert (await invoke("run_modelling_step", dict(COMMON, source="print(1)")))["ok"]
    assessment = prior(e)
    assessment.pop("outcome")
    rejected = await invoke("rewrite_modelling_step", dict(COMMON, source="print(2)", previous_assessment=assessment))
    error = rejected["error"]
    assert error["code"] == "assessment_fields" and error["missing_fields"] == ["outcome"]
    assert error["field_paths"] == ["previous_assessment.outcome"]
    assert files["probe.py"] == "print(1)" and len(calls) == 2
    recovery = e.steps.target_recovery(e)
    result = await invoke("resume_modelling_step", {
        "subtask_id": recovery["subtask_id"], "script_path": recovery["expected_script_path"],
        "reason": "Supply the missing assessment outcome", "corrections": {"previous_assessment": prior(e)}})
    assert result["ok"] and result["target_correction"]["code_reused"]
    assert files["probe.py"] == "print(2)" and len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,path", [
    ({"objective": {"name": "metric", "value": True, "direction": "minimize"}}, "objective.value"),
    ({"objective": {"name": "metric", "direction": "minimize"}}, "objective.value"),
    ({"objective": {"name": "metric", "value": 1, "direction": "minimize", "unit": None}}, "objective.unit"),
    ({"complete_stage": None}, "complete_stage"),
])
async def test_invalid_assessment_identifies_the_leaf_before_another_execution(extra, path):
    e, files, calls, invoke = await native_sdk_fixture()
    assert (await invoke("run_modelling_step", dict(COMMON, source="print(1)")))["ok"]
    result = await invoke("patch_modelling_step", dict(COMMON, edits=[{"old": "1", "new": "2"}],
                                                       previous_assessment=prior(e, **extra)))
    assert result["error"]["code"] == "assessment_fields"
    assert result["error"]["field_paths"] == ["previous_assessment." + path]
    assert files["probe.py"] == "print(1)" and len(calls) == 2


def test_actual_task_board_registration_is_assigned_to_planner_even_with_visible_aliases():
    registry = create_builtin_registry()
    operations = [d for d in registry._capabilities.values() if d.kind.startswith("oaw.tasks.")]
    definitions = [ScopedToolDefinition("operation:" + d.tool_name, "visible_" + d.tool_name, d.description,
                                        input_schema=d.input_schema) for d in operations]
    assert len(operations) == 5
    assert tuple(definitions) == _role_definitions(definitions, "planner")
    assert not _role_definitions(definitions, "structure_builder")
    assert not _role_definitions(definitions, "materials_project")


@pytest.mark.parametrize("option,value,count", [("cubic", "false", 1), ("cubic", "true", 4),
                                                ("orthorhombic", "false", 1), ("orthorhombic", "true", 2)])
def test_materialized_crystal_cli_preserves_explicit_boolean_values(option, value, count, tmp_path, monkeypatch, capsys):
    scripts = skill_scripts("crystal_builder")
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.chdir(tmp_path)
    namespace = runpy.run_path(str(scripts / "crystal_builder.py"))
    assert namespace["_run_cli"](["build_bulk_crystal", "--element", "Cu", "--crystalstructure", "fcc",
                                  "--a", "3.6", "--" + option, value]) == 0
    assert json.loads(capsys.readouterr().out)["num_atoms"] == count


def test_shipped_json_call_examples_match_native_selectors_and_files():
    registry = create_builtin_registry()
    registry.install(AtomSculptorPlugin())
    skills = {s.id: s for s in atomsculptor_skills().skills}
    toolbox = NS(id="toolbox", name="AtomSculptor Skills")
    sandbox = NS(id="box", name="New Sandbox")
    checked = 0
    for skill_id in ("crystal_builder", "surface_creation"):
        for name, content in skills[skill_id].files.items():
            if not name.endswith(".md"):
                continue
            for encoded in re.findall(r'`(\{[^`]+\})`', content):
                args = json.loads(encoded)
                direct = "skill" in args
                kind = "atomsculptor.skills.skill.read" if direct else "atomsculptor.skills.read"
                definition = registry.capability_definition(kind)
                resources = {skill_id: NS(id=skill_id, name=skill_id)} if direct else {toolbox.id: toolbox}
                schema = ProjectedOperation(definition, [], resources, {}).schema()
                Draft202012Validator(schema).validate(args)
                assert args["file_path"] in skills[skill_id].files
                checked += 1
    assert checked == 6
    doc = skills["nanotube_builder"].instructions
    args = json.loads(re.search(r'```json\n(.*?)\n```', doc, re.S)[1])
    definition = registry.capability_definition("sandbox.run_skill_script")
    targets = {"skill": {"nanotube_builder": NS(id="nanotube_builder", name="nanotube_builder")},
               "environment": {}, "target": {}}
    schema = ProjectedOperation(definition, [], {sandbox.id: sandbox}, targets).schema()
    Draft202012Validator(schema).validate(args)
    assert args["script"] in skills["nanotube_builder"].files and "--output-file" in args["argv"]
