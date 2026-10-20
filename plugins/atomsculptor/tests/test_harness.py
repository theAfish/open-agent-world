"""Regressions for evidence preservation and loops observed in the CNT run."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from google.genai import types

from open_agent_world.plugin_api import AgentRuntimeError
from oaw_atomsculptor.harness import HarnessLimits, RunEvidence
from oaw_atomsculptor.runtime import _ModelTrace, _model_callbacks, _trim_specialist_history


def evidence(**options):
    return RunEvidence(_ModelTrace(asyncio.Queue()), "Use 80 carbon atoms; do not call a nanotube generator.",
                       HarnessLimits(**options))


async def invoke(guard, name, args, response, *, call_id="call"):
    hooks = guard.tool_callbacks("structure_builder")
    tool, context = SimpleNamespace(name=name), SimpleNamespace(function_call_id=call_id)
    rejected = await hooks["before_tool_callback"](tool, args, context)
    if rejected is not None:
        return rejected
    await hooks["after_tool_callback"](tool, args, context, response)
    return response


def test_real_adk_parts_keep_source_ids_signatures_and_failed_execution():
    source = "print('real script')\n"
    request = SimpleNamespace(contents=[
        types.Content(role="model", parts=[types.Part(thought_signature=b"signed", function_call=types.FunctionCall(
            id="save", name="write_sandbox_text_file", args={"path": "build.py", "content": source}))]),
        types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="save", name="write_sandbox_text_file", response={"written": len(source)}))]),
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
            id="execute", name="execute_command", args={"argv": ["python3", "build.py"]}))]),
        types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="execute", name="execute_command", response={"exit_code": 1, "stderr": "55 != 80"}))]),
    ])
    before = [item.model_dump(mode="json") for item in request.contents]
    _trim_specialist_history(SimpleNamespace(types=types), _ModelTrace(asyncio.Queue()), "structure_builder", request)
    assert [item.model_dump(mode="json") for item in request.contents] == before


@pytest.mark.asyncio
async def test_repeated_successful_writes_require_execution_then_stop():
    guard = evidence()
    args = {"sandbox": "box", "path": "build.py", "content": "print(1)\n"}
    for _ in range(2):
        assert await invoke(guard, "write_sandbox_text_file", args, {"written": 9}) == {"written": 9}
    blocked = await invoke(guard, "write_sandbox_text_file", args, {"written": 9})
    assert blocked["error"]["code"] == "execution_required"
    assert guard.scripts[("box", "build.py")].version == 1
    with pytest.raises(AgentRuntimeError, match="repeated overwrite"):
        await invoke(guard, "write_sandbox_text_file", args, {"written": 9})


@pytest.mark.asyncio
async def test_probe_and_rejected_execute_do_not_clear_unexecuted_writes():
    guard = evidence()
    args = {"sandbox": "box", "path": "build.py", "content": "print(1)\n"}
    await invoke(guard, "write_sandbox_text_file", args, {"written": 9})
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["wc", "-c", "build.py"]}, {"exit_code": 0})
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"], "wait_seconds": 120},
                 {"ok": False, "error": {"message": "wait_seconds <= 60"}})
    assert guard.scripts[("box", "build.py")].phase == "written"
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "./build.py"]},
                 {"exit_code": 1, "stderr": "55 != 80", "command_id": "executed"})
    item = guard.scripts[("box", "build.py")]
    assert item.phase == "executed" and item.exit_code == 1 and item.unexecuted_writes == 0
    # Real failure feedback permits correction; writing invalidates old evidence.
    await invoke(guard, "write_sandbox_text_file", {**args, "content": "print(80)\n"}, {"written": 10})
    assert item.version != guard.scripts[("box", "build.py")].version
    assert guard.scripts[("box", "build.py")].phase == "written"


@pytest.mark.asyncio
async def test_running_command_blocks_overwrite_and_wait_correlates_result():
    guard = evidence()
    args = {"sandbox": "box", "path": "build.py", "content": "print(1)\n"}
    await invoke(guard, "write_sandbox_text_file", args, {"written": 9})
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]},
                 {"status": "running", "operation_id": "op"})
    assert (await invoke(guard, "write_sandbox_text_file", args, {"written": 9}))["ok"] is False
    await invoke(guard, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "op"},
                 {"exit_code": 0, "command_id": "op"})
    item = guard.scripts[("box", "build.py")]
    assert item.phase == "executed" and item.command_id == "op"
    assert await invoke(guard, "write_sandbox_text_file", args, {"written": 9}) == {"written": 9}


@pytest.mark.asyncio
async def test_parallel_write_is_blocked_while_execute_tool_is_in_flight():
    guard = evidence()
    args = {"sandbox": "box", "path": "build.py", "content": "print(1)\n"}
    await invoke(guard, "write_sandbox_text_file", args, {"written": 9})
    hooks = guard.tool_callbacks("structure_builder")
    await hooks["before_tool_callback"](SimpleNamespace(name="execute_command"),
        {"sandbox": "box", "argv": ["python3", "build.py"]}, SimpleNamespace(function_call_id="in-flight"))
    assert (await invoke(guard, "write_sandbox_text_file", args, {"written": 9}))["error"]["code"] == "execution_required"


@pytest.mark.asyncio
async def test_specialist_receives_original_request_and_configured_output_limit():
    guard = evidence()
    request = SimpleNamespace(contents=[], config=SimpleNamespace(max_output_tokens=None))
    callbacks = _model_callbacks(guard.trace, "structure_builder", None, SimpleNamespace(types=types),
                                 evidence=guard, output_limit=2048)
    await callbacks["before_model_callback"](None, request)
    assert request.config.max_output_tokens == 2048
    assert any(guard.original_request in p.text for c in request.contents for p in c.parts if p.text)
    assert not hasattr(guard, "request_sha256")
    request.config.max_output_tokens = 512
    await callbacks["before_model_callback"](None, request)
    assert request.config.max_output_tokens == 512


def test_optional_request_time_brakes_and_retired_cumulative_output_budget():
    request = SimpleNamespace(contents=[], config=SimpleNamespace(max_output_tokens=None))
    guard = evidence(max_model_requests=4)
    for _ in range(4):
        guard.before_model("planner", request, None, 1024)
    with pytest.raises(AgentRuntimeError, match="request budget"):
        guard.before_model("planner", request, None, 1024)
    guard = evidence(max_output_tokens=1024)
    guard.after_model(SimpleNamespace(partial=True, usage_metadata=SimpleNamespace(candidates_token_count=1024)))
    assert guard.output_tokens == 0
    guard.after_model(SimpleNamespace(partial=False, usage_metadata=SimpleNamespace(candidates_token_count=1024)))
    guard.before_model("planner", request, None, 1024)
    assert guard.output_tokens == 1024 and "max_output_tokens" not in type(guard.limits).model_fields
    assert request.config.max_output_tokens == 1024 and guard.closing_reason is None
    guard = evidence(max_run_seconds=30)
    guard.started -= 31
    with pytest.raises(AgentRuntimeError, match="time budget"):
        guard.before_model("planner", request, None, 1024)


@pytest.mark.asyncio
async def test_identical_dependency_failures_stop_before_another_model_request():
    guard = evidence()
    error = {"ok": False, "error": {"code": "permission_denied", "message": "Sandbox grant revoked"}}
    for _ in range(3):
        await invoke(guard, "start_sandbox", {"sandbox": "box"}, error)
    with pytest.raises(AgentRuntimeError, match="dependency error"):
        guard.before_model("structure_builder", SimpleNamespace(contents=[]), None, 2048)


@pytest.mark.asyncio
async def test_import_requires_matching_read_back_before_confirmed_notice():
    guard = evidence()
    await invoke(guard, "import_atom_structure_file", {"target": "structure"}, {"revision": 4})
    await invoke(guard, "inspect_atom_structure", {"target": "structure"}, {"revision": 3})
    notices = []
    while not guard.trace.queue.empty():
        notices.append(guard.trace.queue.get_nowait()[1])
    assert [n["stage"] for n in notices] == ["imported"]
    with pytest.raises(AgentRuntimeError, match="read-back"):
        guard.require_read_back()
    await invoke(guard, "inspect_atom_structure", {"target": "structure"}, {"revision": 4})
    assert guard.trace.queue.get_nowait()[1]["stage"] == "confirmed"
    guard.require_read_back()


@pytest.mark.asyncio
async def test_data_file_edits_do_not_require_python_execution():
    guard = evidence()
    for _ in range(4):
        assert await invoke(guard, "write_sandbox_text_file", {
            "sandbox": "box", "path": "notes.json", "content": "{}"}, {"written": 2}) == {"written": 2}
    assert not guard.scripts


@pytest.mark.asyncio
async def test_validation_export_hashes_and_async_skill_result():
    import json
    guard = evidence()
    args = {"sandbox": "box", "script": "scripts/structure_inspect.py", "argv": ["validate_nanotube"]}
    await invoke(guard, "run_skill_script", args, {"status": "running", "operation_id": "validation"})
    await invoke(guard, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "validation"}, {
        "exit_code": 0, "stdout": json.dumps({"passed": True, "file": "cnt.extxyz"}),
    })
    assert guard.outputs[("box", "cnt.extxyz")]["phase"] == "validated"
    export_args = {**args, "argv": ["to_atomsculptor_document"]}
    report = {"input_structure_file": "cnt.extxyz", 
              "output_document_file": "cnt.json"}
    await invoke(guard, "run_skill_script", export_args, {"exit_code": 0, "stdout": json.dumps(report)})
    assert guard.outputs[("box", "cnt.json")]["phase"] == "exported"
    await invoke(guard, "run_skill_script", export_args, {"exit_code": 0, "stdout": json.dumps(report)})
    assert guard.outputs[("box", "cnt.json")]["phase"] == "exported"


@pytest.mark.asyncio
async def test_process_failures_are_scoped_to_command_and_stop_repetition():
    guard = evidence(repeated_error_limit=2)
    args = {"sandbox": "box", "script": "scripts/structure_inspect.py", "argv": ["validate_structure"]}
    failure = {"exit_code": 71, "stderr": "execvp: no such file", "command_id": "first"}
    await invoke(guard, "run_skill_script", args, failure)
    # A different command's success must not erase the failed dependency.
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["ls"]}, {"exit_code": 0})
    await invoke(guard, "run_skill_script", args, {**failure, "command_id": "second"})
    with pytest.raises(AgentRuntimeError, match="same dependency error"):
        guard.before_model("structure_builder", SimpleNamespace(contents=[]), None, 2048)


@pytest.mark.asyncio
async def test_custom_output_requires_trusted_validation_and_conversion():
    import json
    guard = evidence()
    write_args = {"sandbox": "box", "path": "build.py", "content": "print(80)\n"}
    await invoke(guard, "write_sandbox_text_file", write_args, {"written": 10})
    report = {"passed": True, "file": "cnt.extxyz"}
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "copied.py"]},
                 {"exit_code": 0, "stdout": json.dumps(report)})
    imports = {"sandbox": "box", "file_name": "cnt.json", "target": "structure"}
    assert (await invoke(guard, "import_atom_structure_file", imports, {}))["error"]["code"] == "independent_validation_required"
    args = {"sandbox": "box", "script": "scripts/structure_inspect.py", "argv": ["validate_nanotube"]}
    await invoke(guard, "run_skill_script", args, {"exit_code": 0, "stdout": json.dumps(report)})
    conversion = {"input_structure_file": "cnt.extxyz", 
                  "output_document_file": "cnt.json"}
    await invoke(guard, "run_skill_script", {**args, "argv": ["to_atomsculptor_document"]},
                 {"exit_code": 0, "stdout": json.dumps(conversion)})
    assert await invoke(guard, "import_atom_structure_file", {**imports}, {"revision": 2}) == {"revision": 2}
    await invoke(guard, "write_sandbox_text_file", {**write_args, "content": "print(81)\n"}, {"written": 10})
    # Source edits alone do not change already validated/exported bytes.
    assert guard.outputs[("box", "cnt.json")]["phase"] == "exported"
    # A native write to the structure invalidates its validation and exports.
    await invoke(guard, "write_sandbox_text_file", {"sandbox": "box", "path": "cnt.extxyz", "content": "changed"}, {"written": 7})
    await invoke(guard, "run_skill_script", {**args, "argv": ["to_atomsculptor_document"]},
                 {"exit_code": 0, "stdout": json.dumps({**conversion})})
    rejected = await invoke(guard, "import_atom_structure_file", {**imports}, {})
    assert rejected["error"]["code"] == "independent_validation_required"


@pytest.mark.asyncio
async def test_skill_candidate_cannot_bypass_independent_validation_without_custom_python():
    import json
    guard = evidence()
    args = {"sandbox": "box", "script": "scripts/interface_builder.py", "argv": ["create_interface"]}
    await invoke(guard, "run_skill_script", args, {"exit_code": 0, "stdout": json.dumps({
        "interface_candidates": [{"id": 1, "file_name": "candidate.extxyz"}]})})
    assert not guard.scripts and guard.outputs[("box", "candidate.extxyz")]["phase"] == "candidate"
    inspector = {**args, "script": "scripts/structure_inspect.py"}
    convert = {"input_structure_file": "candidate.extxyz", "output_document_file": "candidate.json"}
    await invoke(guard, "run_skill_script", {**inspector, "argv": ["to_atomsculptor_document"]},
                 {"exit_code": 0, "stdout": json.dumps(convert)})
    imported = {"sandbox": "box", "target": "structure", "file_name": "candidate.json"}
    rejected = await invoke(guard, "import_atom_structure_file", imported, {"revision": 2})
    assert rejected["error"]["code"] == "independent_validation_required" and not guard.imports
    await invoke(guard, "run_skill_script", {**inspector, "argv": ["validate_structure"]},
                 {"exit_code": 0, "stdout": json.dumps({"file": "candidate.extxyz", "passed": True})})
    await invoke(guard, "run_skill_script", {**inspector, "argv": ["to_atomsculptor_document"]},
                 {"exit_code": 0, "stdout": json.dumps(convert)})
    assert await invoke(guard, "import_atom_structure_file", imported, {"revision": 2}) == {"revision": 2}


@pytest.mark.asyncio
async def test_read_only_verifier_preserves_validated_export_and_hash_requirement():
    import json
    guard = evidence()
    args = {"sandbox": "box", "script": "scripts/structure_inspect.py", "argv": ["validate_structure"]}
    report = {"passed": True, "file": "cnt.extxyz"}
    await invoke(guard, "run_skill_script", args, {"exit_code": 0, "stdout": json.dumps(report)})
    await invoke(guard, "write_sandbox_text_file", {"sandbox": "box", "path": "verify.py", "content": "print('read only')"}, {"written": 18})
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "verify.py"]}, {"exit_code": 0})
    assert guard.outputs[("box", "cnt.extxyz")]["phase"] == "validated"
    conversion = {"input_structure_file": "cnt.extxyz", 
                  "output_document_file": "cnt.json"}
    await invoke(guard, "run_skill_script", {**args, "argv": ["to_atomsculptor_document"]},
                 {"exit_code": 0, "stdout": json.dumps(conversion)})
    imports = {"sandbox": "box", "file_name": "cnt.json"}
    assert await invoke(guard, "import_atom_structure_file", imports, {"revision": 2}) == {"revision": 2}
    # Revalidation of changed bytes removes only exports derived from that file.
    guard.outputs[("other", "unrelated.json")] = {"phase": "exported"}
    await invoke(guard, "write_sandbox_text_file", {"sandbox": "box", "path": "cnt.extxyz", "content": "changed"}, {"written": 7})
    await invoke(guard, "run_skill_script", args, {"exit_code": 0, "stdout": json.dumps({**report})})
    assert ("box", "cnt.json") not in guard.outputs
    assert ("other", "unrelated.json") in guard.outputs


@pytest.mark.asyncio
async def test_output_file_write_invalidates_only_changed_receipts():
    import json
    guard = evidence()
    raw = "saved coordinates"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    args = {"sandbox": "box", "script": "scripts/structure_inspect.py", "argv": ["validate_structure"]}
    await invoke(guard, "run_skill_script", args, {"exit_code": 0, "stdout": json.dumps({"passed": True, "file": "cnt.extxyz"})})
    write = {"sandbox": "box", "path": "cnt.extxyz", "content": raw}
    await invoke(guard, "write_sandbox_text_file", write, {"written": len(raw)})
    assert ("box", "cnt.extxyz") not in guard.outputs
    await invoke(guard, "write_sandbox_text_file", {**write, "content": "changed"}, {"written": 7})
    assert ("box", "cnt.extxyz") not in guard.outputs


@pytest.mark.asyncio
async def test_exact_patch_tracks_new_script_hash_and_requires_execution():
    guard = evidence()
    write = {"sandbox": "box", "path": "build.py", "content": "print(1)"}
    await invoke(guard, "write_sandbox_text_file", write, {"written": 8})
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]}, {"exit_code": 1})
    args = {"sandbox": "box", "path": "build.py", "overwrite": True,
            
            "edits": [{"old": "1", "new": "80"}]}
    digest = hashlib.sha256(b"print(80)").hexdigest()
    await invoke(guard, "write_sandbox_text_file", args, {"written": 9})
    item = guard.scripts[("box", "build.py")]
    assert item.version == 2 and item.phase == "written" and not hasattr(item, "sha256")
    await invoke(guard, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]}, {"exit_code": 0})
    assert item.phase == "executed" and item.exit_code == 0


@pytest.mark.asyncio
async def test_environment_metadata_survives_between_model_requests():
    guard = evidence()
    environment = {"python_version": "3.12.14", "package_versions": {"numpy": "2.4.2"}}
    await invoke(guard, "inspect_sandbox", {"sandbox": "box"}, {"python_environment": environment})
    request = SimpleNamespace(contents=[], config=SimpleNamespace(max_output_tokens=None))
    guard.before_model("structure_builder", request, SimpleNamespace(types=types), 2048)
    stable = [p.text for c in request.contents for p in c.parts if p.text and p.text.startswith("HOST STABLE CONTEXT")]
    assert len(stable) == 1 and '"numpy": "2.4.2"' in stable[0]


def test_role_budgets_preserve_direct_builder_capacity():
    for role, direct, expected in [("atom_sculptor", False, 4096), ("atom_sculptor", True, 65536), ("structure_builder", False, 65536)]:
        guard = RunEvidence(_ModelTrace(asyncio.Queue()), "Build a structure", direct_builder=direct)
        request = SimpleNamespace(contents=[], config=SimpleNamespace(max_output_tokens=None))
        guard.before_model(role, request, None, 65536)
        assert request.config.max_output_tokens == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_writes", [False, True])
async def test_real_adk_runner_preserves_history_and_ends_write_loop(monkeypatch, repeat_writes):
    """Exercise real ADK declarations/callbacks, with an in-memory model/provider.

    No model service, user document or Sandbox is touched. The non-loop case
    reproduces a parameter refusal and script failure, then executes a repair.
    """
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from backend.agents.models import ScopedToolDefinition, ToolParameter
    from backend.errors import ResourceValidationError
    from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
    from oaw_atomsculptor.runtime import AtomSculptorRuntime

    steps = 0
    invocations = []
    source = "print(1)\n"
    fixed = "print(80)\n"
    original = "Run my small Python probe, repair failures and report the result."
    class FixtureModel(BaseLlm):
        model: str = "openai/harness-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal steps
            steps += 1
            assert request.config.max_output_tokens == 2048
            assert any(original in (getattr(part, "text", "") or "")
                       for item in request.contents for part in item.parts)
            calls = [part.function_call for item in request.contents for part in item.parts if part.function_call]
            if steps > 1:
                assert any(call.name == "write_sandbox_text_file" and call.args["content"] == source for call in calls)
            declarations = [declaration for tool in request.config.tools for declaration in tool.function_declarations or []]
            execute = next(item for item in declarations if item.name == "execute_command")
            assert execute.parameters_json_schema["properties"]["wait_seconds"]["maximum"] == 60
            if repeat_writes or steps in (1, 4):
                name = "write_sandbox_text_file"
                args = {"sandbox": "box", "path": "build.py", "content": fixed if steps == 4 else source}
            elif steps in (2, 3, 5):
                name = "execute_command"
                args = {"sandbox": "box", "argv": ["python3", "build.py"], "wait_seconds": 120 if steps == 2 else 60}
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Probe executed successfully.")]))
                return
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                id=f"call-{steps}", name=name, args=args))]))

    class Provider:
        async def list_tools(self, _agent):
            return [ScopedToolDefinition("write", "write_sandbox_text_file", "Write source", (
                ToolParameter("sandbox"), ToolParameter("path"), ToolParameter("content"))),
                ScopedToolDefinition("execute", "execute_command", "Execute source", (
                    ToolParameter("sandbox"), ToolParameter("argv", list),
                    ToolParameter("wait_seconds", float, required=False, default=1)), input_schema={
                        "type": "object", "properties": {"sandbox": {"type": "string", "enum": ["box"]},
                        "argv": {"type": "array", "items": {"type": "string"}},
                        "wait_seconds": {"type": "number", "minimum": 0, "maximum": 60}},
                        "required": ["sandbox", "argv"]})]

        async def invoke_tool(self, _agent, capability, args):
            invocations.append((capability, args))
            if capability == "write":
                return {"written": len(args["content"])}
            if args["wait_seconds"] > 60:
                raise ResourceValidationError("wait_seconds must be between 0 and 60")
            return {"exit_code": 1 if steps == 3 else 0, "command_id": f"command-{steps}", "stderr": "first script failed" if steps == 3 else ""}

    resolver = SimpleNamespace(
        resolve_runtime=lambda _model: RuntimeModelConnection("openai", "harness-fixture", "https://model.invalid", None),
        context_limits=lambda _model: (32768, 2048))
    runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=resolver)
    monkeypatch.setattr(runtime, "_model", lambda *_args: FixtureModel())
    config = AgentConfig("fixture-agent", "Atom", provider_config={"execution_mode": "builder"})
    await runtime.create_agent(config)
    async def run():
        return [item async for item in runtime.execute(config, SimpleNamespace(run_id="fixture-run", context_id="fixture"),
                                                       SimpleNamespace(prompt=original))]
    if repeat_writes:
        events = await run()
        assert events[-1].run_status == "failed" and "repeated overwrite" in events[-1].payload["error"]
        assert len(invocations) == 2
        assert steps == 4
    else:
        events = await run()
        assert events[-1].run_status == "succeeded"
        assert [item[0] for item in invocations] == ["write", "execute", "execute", "write", "execute"]
        stages = [item.payload.get("stage") for item in events]
        assert "written" in stages and "executed" in stages


@pytest.mark.asyncio
async def test_run_deadline_drains_a_live_model_without_network_retry(monkeypatch):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
    from oaw_atomsculptor.runtime import AtomSculptorRuntime
    entered, closed = [], []

    class HangingModel(BaseLlm):
        model: str = "openai/deadline-fixture"

        async def generate_content_async(self, request, stream=False):
            entered.append(True)
            try:
                yield LlmResponse(partial=True, content=types.Content(role="model", parts=[
                    types.Part(text="Still delivering content", thought=True)]))
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    provider = SimpleNamespace(list_tools=lambda _agent: asyncio.sleep(0, result=[]))
    resolver = SimpleNamespace(
        resolve_runtime=lambda _model: RuntimeModelConnection("openai", "deadline-fixture", "https://model.invalid", None),
        context_limits=lambda _model: (32768, 2048))
    runtime = AtomSculptorRuntime(provider, model_connection_resolver=resolver)
    monkeypatch.setattr(runtime, "_model", lambda *_args: HangingModel())
    monkeypatch.setattr(RunEvidence, "remaining_seconds", property(lambda _self: .25))
    config = AgentConfig("deadline-agent", "Atom")
    await runtime.create_agent(config)
    events = [item async for item in runtime.execute(config,
             SimpleNamespace(run_id="deadline-run", context_id="deadline"), SimpleNamespace(prompt="Build a structure"))]
    assert events[-1].run_status == "failed" and "run time budget" in events[-1].payload["error"]
    assert len(entered) == len(closed) == 1
    assert not runtime.active


def test_unlimited_total_budget_survives_elapsed_time_and_serializes_null(monkeypatch):
    import json
    from google.adk.models.llm_request import LlmRequest
    from oaw_atomsculptor import AtomSculptorAgentConfig
    original = {"max_run_seconds": 1200, "max_model_requests": 45}
    limits = HarnessLimits.model_validate(original)
    assert limits.max_run_seconds is None
    assert original["max_run_seconds"] == 1200
    assert HarnessLimits().max_run_seconds is None
    assert HarnessLimits(max_run_seconds=1800).max_run_seconds == 1800
    assert AtomSculptorAgentConfig().run_execution_deadline_seconds == 0
    e = evidence()
    monkeypatch.setattr("oaw_atomsculptor.harness.time.monotonic", lambda: e.started + 7200)
    assert e.remaining_seconds is None
    budget = e.time_budget()
    assert budget["phase"] == "experiment" and budget["construction_seconds"] is None
    assert budget["closeout_reserve_seconds"] == 0
    json.dumps(budget, allow_nan=False)
    request = LlmRequest(contents=[], config=types.GenerateContentConfig(max_output_tokens=81920))
    e.before_model("structure_builder", request, SimpleNamespace(types=types), 81920)
    assert request.config.max_output_tokens == 81920
    assert '"remaining_seconds": null' in request.contents[-1].parts[0].text


@pytest.mark.asyncio
async def test_unlimited_run_still_drains_on_manual_stop(monkeypatch):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
    from oaw_atomsculptor.runtime import AtomSculptorRuntime
    entered = asyncio.Event()
    closed = []

    class LiveModel(BaseLlm):
        model: str = "openai/unlimited-fixture"

        async def generate_content_async(self, request, stream=False):
            entered.set()
            try:
                yield LlmResponse(partial=True, content=types.Content(role="model", parts=[
                    types.Part(text="Still delivering content", thought=True)]))
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    provider = SimpleNamespace(list_tools=lambda _agent: asyncio.sleep(0, result=[]))
    resolver = SimpleNamespace(
        resolve_runtime=lambda _model: RuntimeModelConnection("openai", "unlimited-fixture", "https://model.invalid", None),
        context_limits=lambda _model: (32768, 2048))
    runtime = AtomSculptorRuntime(provider, model_connection_resolver=resolver)
    monkeypatch.setattr(runtime, "_model", lambda *_args: LiveModel())
    config = AgentConfig("unlimited-agent", "Atom")
    await runtime.create_agent(config)

    async def consume():
        return [item async for item in runtime.execute(config,
            SimpleNamespace(run_id="unlimited-run", context_id="unlimited"), SimpleNamespace(prompt="Build a structure"))]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 3)
    await runtime.stop("unlimited-run")
    with pytest.raises(AgentRuntimeError, match="Run was stopped"):
        await asyncio.wait_for(task, 3)
    assert closed == [True] and not runtime.active
