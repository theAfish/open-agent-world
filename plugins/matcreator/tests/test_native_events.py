"""Internal reports and user replies have different publication boundaries."""
from types import SimpleNamespace

import pytest
from google.adk.events import Event
from google.genai import types

from plugins.matcreator.oaw_matcreator.native_events import is_conversation_author, public_events


@pytest.mark.parametrize("author,child", [
    ("execution_orchestrator", False), ("step_executor", True),
    ("thinking_agent", True), ("unknown", False),
])
def test_internal_text_stays_private_but_tool_evidence_is_published(author, child):
    event = Event(author=author, content=types.Content(role="model", parts=[
        types.Part.from_text(text="Please approve this internal report."),
        types.Part(function_call=types.FunctionCall(id="handoff", name="to_planner", args={"reason": "Needs a decision"})),
    ]))
    emitted = list(public_events(event, child=child))
    assert [item["event"] for item in emitted] == ["tool_started"]
    assert emitted[0]["payload"]["author"] == author
    assert emitted[0]["payload"]["subagent"] is child
    assert not is_conversation_author(event, child=child)
    assert event.content.parts[0].text == "Please approve this internal report."


def test_planner_reply_preserves_identity_and_keeps_thinking_in_activity():
    event = Event(id="planner-message", author="thinking_agent", content=types.Content(role="model", parts=[
        types.Part(text="Provider-exposed thinking", thought=True),
        types.Part.from_text(text="Revised plan ready. Please confirm."),
    ]))
    emitted = list(public_events(event))
    assert [item["event"] for item in emitted] == ["agent_progress", "agent_message"]
    assert emitted[0]["payload"]["kind"] == "model_reasoning"
    assert emitted[0]["payload"]["provider_message_id"] == "planner-message:0"
    assert emitted[1]["payload"]["provider_message_id"] == "planner-message:1"
    assert emitted[1]["payload"]["text"] == "Revised plan ready. Please confirm."
    assert emitted[1]["payload"]["final"] is True
    assert is_conversation_author(event)


@pytest.mark.parametrize("author,child", [
    ("thinking_agent", False), ("execution_orchestrator", False),
    ("step_executor", True), ("thinking_agent", True),
])
def test_thinking_precedes_tools_with_stable_identity_and_actor(author, child):
    event = Event(id="model-response", author=author, content=types.Content(role="model", parts=[
        types.Part(text="Check the existing result first.", thought=True),
        types.Part(function_call=types.FunctionCall(id="inspect", name="oaw_list_tools", args={})),
    ]))
    emitted = list(public_events(event, child=child))
    assert [item["event"] for item in emitted] == ["agent_progress", "tool_started"]
    assert emitted[0]["payload"] == {
        "author": author, "subagent": child, "role": author, "kind": "model_reasoning",
        "provider_message_id": "model-response:0", "text": "Check the existing result first.",
        "streaming": False, "interrupted": False,
    }
    assert list(public_events(event, child=child)) == emitted
    event.id = "another-response"
    assert list(public_events(event, child=child))[0]["payload"]["provider_message_id"] != "model-response:0"


def test_empty_thinking_and_opaque_signatures_do_not_create_activity():
    event = Event(author="thinking_agent", content=types.Content(role="model", parts=[
        types.Part(text=" \n\t", thought=True), types.Part(thought=True, thought_signature=b"opaque"),
    ]))
    assert list(public_events(event)) == []


@pytest.mark.parametrize("termination", [{"finish_reason": "MAX_TOKENS"}, {"error_code": "provider_error"}])
def test_failed_model_response_retains_thinking_as_incomplete_activity(termination):
    event = Event(author="thinking_agent", **termination, content=types.Content(role="model", parts=[
        types.Part(text="Received before termination", thought=True)]))
    [emitted] = public_events(event)
    assert emitted["event"] == "agent_progress"
    assert emitted["payload"]["text"] == "Received before termination"
    assert emitted["payload"]["interrupted"] and emitted["payload"]["streaming"] is False


def test_internal_tool_failure_does_not_publish_its_raw_report():
    event = Event(author="execution_orchestrator", content=types.Content(role="user", parts=[
        types.Part(function_response=types.FunctionResponse(id="validate", name="validate_graph",
            response={"status": "error", "errors": "private report"})),
    ]))
    emitted = list(public_events(event))
    assert emitted == [{"type": "event", "event": "tool_completed", "payload": {
        "author": "execution_orchestrator", "subagent": False, "name": "validate_graph",
        "call_id": "validate", "ok": False, "success": False,
        "response": {"success": False, "status": "error"}, "response_visibility": "summary"}}]


def test_resource_output_is_public_bounded_and_sanitized_without_changing_native_result():
    result = {"ok": True, "result": {"exit_code": 0, "api_key": "private-token", "stdout": "x" * 100_000}}
    event = Event(author="step_executor", content=types.Content(role="user", parts=[
        types.Part(function_response=types.FunctionResponse(id="command", name="oaw_invoke_tool", response=result)),
    ]))
    payload = list(public_events(event, child=True))[0]['payload']
    assert payload['response']['result']['api_key'] == '[REDACTED]'
    assert payload['response']['result']['exit_code'] == 0
    assert len(payload['response']['result']['stdout']) < 17_000
    assert payload['truncated'] and payload['success'] and payload['subagent']
    assert payload['response_visibility'] == 'full'
    assert result['result']['api_key'] == 'private-token'


@pytest.mark.asyncio
async def test_executor_cannot_bypass_planner_using_remembered_conversation_tools(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from oaw_matcreator_engine import resources

    calls = []
    catalog = [
        {"name": "send_conversation_message", "capability_id": "opaque:send"},
        {"name": "request_conversation_turn", "capability_id": "opaque:turn"},
        {"name": "publish_artifact", "capability_id": "opaque:publish"},
    ]

    async def rpc(method, **args):
        if method == "list_tools":
            return list(catalog)
        calls.append(args)
        return {"ok": True, "result": []}

    monkeypatch.setattr(resources, "_bridge", resources.ResourceBridge(rpc, lambda *args: None))
    internal = {tool.name: tool for tool in resources.resource_tools()}
    planner = {tool.name: tool for tool in resources.resource_tools(conversation_owner=True)}

    async def invoke(tools, name, **args):
        return await tools[name].run_async(args=args, tool_context=SimpleNamespace())

    assert (await invoke(internal, "oaw_list_tools"))["tools"] == [catalog[-1]]
    assert (await invoke(planner, "oaw_list_tools"))["tools"] == catalog
    for capability in ("opaque:send", "opaque:turn", "stale:send"):
        result = await invoke(internal, "oaw_invoke_tool", capability_id=capability, arguments={})
        assert result["error"]["side_effects"] == "none"
    assert calls == []
    assert (await invoke(internal, "oaw_invoke_tool", capability_id="opaque:publish", arguments={}))["ok"]
    assert (await invoke(planner, "oaw_invoke_tool", capability_id="opaque:send", arguments={}))["ok"]
    assert [call["capability_id"] for call in calls] == ["opaque:publish", "opaque:send"]
    # Do not cache the allowed catalog, or share the planner's privileges with
    # concurrent executors. A remembered ID must be checked at invocation time.
    catalog[-1] = {"name": "send_conversation_message", "capability_id": "opaque:publish"}
    assert not (await invoke(internal, "oaw_invoke_tool", capability_id="opaque:publish", arguments={}))["ok"]
    assert len(calls) == 2
