import json

from backend.runs.trace import public_tool_payload


def test_tool_arguments_results_and_errors_are_retained():
    payload = public_tool_payload({
        "name": "read_file", "call_id": "call-1", "arguments": {"path": "notes.txt"},
        "response": {"content": "hello"}, "success": False, "error": "permission denied",
        "runtime_config": {"api_key": "must-not-persist"},
    })
    assert payload["arguments"] == {"path": "notes.txt"}
    assert payload["response"] == {"content": "hello"}
    assert payload["success"] is False
    assert payload["error"] == "permission denied"
    assert "runtime_config" not in payload


def test_sensitive_fields_are_masked_recursively():
    payload = public_tool_payload({"name": "request", "arguments": {
        "headers": {"Authorization": "Bearer secret", "content-type": "application/json"},
        "api_key": "private", "password": "pass", "accessToken": "access",
        "data": [{"privateKey": "key", "filename": "safe.txt"}],
    }})
    raw = json.dumps(payload)
    for value in ("Bearer secret", '"private"', '"pass"', '"access"', '"key"'):
        assert value not in raw
    assert "safe.txt" in raw
    assert "application/json" in raw


def test_json_text_results_are_sanitized_too():
    payload = public_tool_payload({"name": "request", "response": '{"api_key":"secret","ok":true}'})
    assert payload["response"] == {"api_key": "[REDACTED]", "ok": True}


def test_large_result_is_bounded_and_marked():
    payload = public_tool_payload({"name": "read", "response": "x" * 500_000})
    assert payload["truncated"] is True
    assert len(json.dumps(payload)) < 17_000


def test_collection_traversal_is_bounded():
    payload = public_tool_payload({"name": "read", "response": list(range(10_000))})
    assert payload["truncated"] is True
    assert len(payload["response"]) <= 101


def test_depth_and_cycles_are_bounded():
    value = {}
    value["self"] = value
    payload = public_tool_payload({"name": "read", "response": value})
    assert payload["truncated"] is True
    assert len(json.dumps(payload)) < 1000


def test_explicit_failure_survives_large_output():
    payload = public_tool_payload({"name": "command", "success": False,
        "error": "failed", "response": "x" * 500_000})
    assert payload["success"] is False
    assert payload["error"] == "failed"


def test_unsupported_sdk_objects_do_not_leak_repr():
    class Credential:
        def __repr__(self):
            raise AssertionError("Do not inspect SDK internals")
    payload = public_tool_payload({"name": "read", "response": Credential()})
    assert payload["response"] == "[unsupported value]"


def test_source_payload_is_not_mutated():
    source = {"name": "tool", "arguments": {"password": "secret"}, "response": [1, 2]}
    before = json.dumps(source)
    public_tool_payload(source)
    assert json.dumps(source) == before
