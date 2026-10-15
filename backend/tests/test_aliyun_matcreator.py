"""Trusted orchestration protocol and remote-only tool routing, without a model API."""
from dataclasses import asdict
from importlib import import_module
import json
import os
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("e2b")
pytest.importorskip("authlib")

from backend.cloud.aliyun.app import approved_plugins
from backend.cloud.aliyun.sandbox import AliyunSandboxBackend, AliyunSandboxSettings
from backend.tests.test_aliyun_sandbox import FakeSDK
from open_agent_world.plugin_api import AgentConfig, RuntimeInput, RuntimeModelConnection
from plugins.matcreator.tests.test_native_runtime import scripted_model


@pytest.mark.asyncio
async def test_hosted_worker_keeps_control_credentials_out_and_routes_authorized_commands(tmp_path, monkeypatch):
    for name in ("OAW_ALIYUN_SANDBOX_API_KEY", "OAW_OIDC_CLIENT_SECRET", "OAW_SESSION_SECRET",
                 "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "UNRECOGNIZED_FUTURE_PROVIDER_SECRET", "HTTPS_PROXY",
                 "PYTHONPATH", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "control-secret-must-not-reach-worker")
    remote_root = tmp_path / "remote-files"
    remote_root.mkdir()
    sdk = FakeSDK(remote_root)
    remote = AliyunSandboxBackend(tmp_path / "remote-receipts",
        AliyunSandboxSettings("cn-hangzhou", "control-key", "template", "private-volume", "alice"), sdk=sdk)
    await remote.create("compute")
    calls = []

    async def list_tools(_):
        return [SimpleNamespace(capability_id="remote-compute", name="execute_command",
                                description="Managed compute", input_schema={"type": "object"})]

    async def invoke(_, capability_id, arguments):
        calls.append(capability_id)
        return asdict(await remote.execute("compute", ["/bin/sh", "-c", arguments["command"]], timeout_seconds=30))

    provider = SimpleNamespace(list_tools=list_tools, invoke_tool=invoke,
        resolve_model=lambda _: RuntimeModelConnection("test", "openai", "https://model.example", "user-model-key"),
        runtime_state_directory=lambda _: tmp_path / "state")
    runtime = approved_plugins().create_runtime_provider("matcreator.native", provider)
    base_module = import_module(runtime.__class__.__mro__[1].__module__)
    worker = tmp_path / "native_worker.py"
    worker.write_text('''import json, os, sys
assert "control-secret-must-not-reach-worker" not in os.environ.values()
assert sys.flags.isolated and sys.flags.no_site
def send(value):
    print(json.dumps(value), flush=True)
request = json.loads(sys.stdin.readline())
assert request["model"]["api_key"] == "user-model-key"
send({"type": "request", "id": "denied", "method": "invoke_tool", "capability_id": "host-shell", "arguments": {}})
denied = json.loads(sys.stdin.readline())
assert denied["value"]["ok"] is False
send({"type": "request", "id": "ok", "method": "invoke_tool", "capability_id": "remote-compute", "arguments": {"command": "printf cloud"}})
result = json.loads(sys.stdin.readline())
assert result["value"]["ok"] is True
send({"type": "completed", "text": "managed tool completed"})
''', encoding="utf-8")
    monkeypatch.setattr(base_module, "__file__", str(tmp_path / "native_runtime.py"))
    monkeypatch.setattr(base_module, "read_environment", lambda: (sys.executable, tmp_path / "dependencies"))
    config = AgentConfig("agent", "Hosted MatCreator")
    await runtime.create_agent(config)
    events = [event async for event in runtime.execute(config,
        SimpleNamespace(context_id="session", run_id="run"), RuntimeInput("test cloud compute"))]
    assert events[-1].payload["text"] == "managed tool completed"
    assert calls == ["remote-compute"]
    assert len(sdk.created) == 1 and len(sdk.commands) == 1
    assert sdk.killed == ["remote-1"]
    assert not runtime.active
    assert "control-secret-must-not-reach-worker" not in json.dumps(
        [(command, options["envs"]) for command, options in sdk.commands])


def test_hosted_worker_environment_does_not_change_personal_environment(monkeypatch):
    monkeypatch.setenv("OAW_LOCAL_EXTENSION_SETTING", "personal-value")
    hosted = approved_plugins().create_runtime_provider("matcreator.native", None)
    personal = hosted.__class__.__mro__[1](None)
    assert personal._worker_environment()["OAW_LOCAL_EXTENSION_SETTING"] == "personal-value"
    assert "OAW_LOCAL_EXTENSION_SETTING" not in hosted._worker_environment()
    assert os.environ["OAW_LOCAL_EXTENSION_SETTING"] == "personal-value"


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("OAW_MATCREATOR_MANAGED"), reason="requires Pack orchestration dependencies")
@pytest.mark.parametrize("scripted_model", [False, True], indirect=True, ids=["normal", "tool-failures"])
async def test_hosted_real_engine_retains_planning_checkpoint_and_tool_protocol(tmp_path, scripted_model, monkeypatch):
    # Exercise the maintained engine with its existing deterministic model
    # endpoint and assertions, changing only the deployment runtime factory.
    # This is neither a real external model nor an Alibaba service test.
    from plugins.matcreator.tests import test_native_runtime as contract
    registry = approved_plugins()
    monkeypatch.setattr(contract, "MatCreatorRuntime",
                        lambda provider: registry.create_runtime_provider("matcreator.native", provider))
    await contract.test_pack_engine_planner_checkpoint_executor_recursive_child_and_oaw_tool(tmp_path, scripted_model)
