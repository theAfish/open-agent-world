"""Project environment edits preserve runtime defaults and private boundaries."""
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.execution_config import EnvironmentProfile, resolve_sandbox_configuration
from backend.sandbox.environment import folder_environment, minimal_windows_environment
from backend.sandbox.linux import minimal_linux_environment
from backend.sandbox.darwin_seatbelt import minimal_darwin_environment
from backend.sandbox.macos_container import minimal_container_environment
from backend.sandbox.models import CommandResult, FolderMount, ResourceAccess, SandboxInfo, SandboxState, SandboxValidationError
from backend.tests.conftest import create_node
from backend.tests.test_skill_packages import edit
from backend.tests.test_tool_projection import connect, tools, invoke
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.errors import ResourceValidationError


def extension(value, operation="append", separator=None):
    return {"value": value, "operation": operation, **({"separator": separator} if separator is not None else {})}


@pytest.mark.parametrize("operation", ["append", "prepend"])
def test_path_extends_each_runtime_without_changing_host(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("PATH", "private-host-path")
    for builder, value, separator in (
        (lambda env: minimal_windows_environment(tmp_path, invocation_env=env), "D:\\Tools\nD:\\Other tools", ";"),
        (lambda env: minimal_linux_environment(invocation_env=env), "/workspace/bin\n/opt/tools", ":"),
    ):
        base = builder({})["PATH"]
        request = {"pAtH": [extension(value, operation)]}
        snapshot = json.dumps(request)
        result = builder(request)
        addition = value.replace("\n", separator)
        assert result["PATH"] == (base + separator + addition if operation == "append" else addition + separator + base)
        assert "pAtH" not in result and "private-host-path" not in result["PATH"]
        assert json.dumps(request) == snapshot
    assert os.environ["PATH"] == "private-host-path"


def test_generic_operations_handle_inherited_values_and_case_without_extra_separators():
    result = minimal_linux_environment(invocation_env={
        "greeting": [extension("hello", "replace"), extension("world", separator=" "), extension("say", "prepend", ": ")],
        "EMPTY_BASE": [extension("only", separator=",")],
        "lang": [extension("custom", "prepend", ":")],
    })
    assert result["greeting"] == "say: hello world"
    assert result["EMPTY_BASE"] == "only"
    assert result["lang"] == "custom:C.UTF-8" and "LANG" not in result


def test_macos_runtimes_share_operation_semantics_and_preserve_their_base_paths():
    for builder in (lambda env: minimal_darwin_environment(Path("/home"), Path("/tmp"), invocation_env=env),
                    lambda env: minimal_container_environment(invocation_env=env)):
        assert builder({"PATH": [extension("/workspace/bin")]})["PATH"] == builder({})["PATH"] + ":/workspace/bin"


@pytest.mark.parametrize("variables", [
    {"PATH": "/workspace/bin"},
    {"PATH": extension("/workspace/bin", "replace")},
    {"PATH": extension("/workspace/bin", separator=":")},
    {"PATH": extension("")},
    {"HOME": extension("/workspace")},
    {"LD_PRELOAD": extension("/workspace/loader.so")},
    {"OAW_TARGET_CONFIG_JSON": extension("{}")},
    {"PATH": {"secret_ref": "path", "operation": "append"}},
    {"PATH": {"file_ref": "path", "operation": "append"}},
    {"APP": extension("a\0b")},
    {"APP": extension("ok", separator="\0")},
    {"APP": {"value": "ok", "operation": "unknown"}},
])
def test_operation_validation_does_not_bypass_reserved_names_or_string_policy(variables):
    with pytest.raises((ValueError, SandboxValidationError)):
        EnvironmentProfile.model_validate({"variables": variables})


@pytest.mark.parametrize("value", ["/tools:", ":/tools", "/tools::/other", ".", "relative/bin", "/tools\n\n/other"])
def test_path_rejects_empty_and_relative_entries(value):
    with pytest.raises(SandboxValidationError, match="absolute directories"):
        minimal_linux_environment(invocation_env={"PATH": [extension(value)]})


def test_final_materialized_environment_enforces_size_limit():
    with pytest.raises(SandboxValidationError, match="24 KB"):
        minimal_linux_environment(invocation_env={"LANG": [extension("x" * 23992)]})


def test_project_layers_compose_live_and_replacement_discards_lower_secrets(client, monkeypatch):
    sandbox = create_node(client, "sandbox")
    profile = create_node(client, "environment")
    assert client.put("/api/settings/sandbox", json={"environment_variables": {"FLAGS": "global"}}).status_code == 200
    edit(client, profile, "replace", {"variables": {"flags": extension("profile", separator=",")}})
    connect(client, profile, sandbox, "environment.default")
    edit(client, sandbox, "replace", {"variables": {"Flags": extension("local", "prepend", ",")}})
    services = client.app.state.services
    env, _ = resolve_sandbox_configuration(services, sandbox["id"])
    assert minimal_linux_environment(invocation_env=env)["Flags"] == "local,global,profile"
    context = client.get(f"/api/nodes/{sandbox['id']}/environment-context")
    assert context.json() == {"PATH": "runtime", "FLAGS": "local"}
    assert client.put("/api/settings/sandbox", json={"environment_variables": {"FLAGS": "changed"}}).status_code == 200
    env, _ = resolve_sandbox_configuration(services, sandbox["id"])
    assert minimal_linux_environment(invocation_env=env)["Flags"] == "local,changed,profile"
    edit(client, profile, "replace", {"variables": {"FLAGS": {"secret_ref": "unbound"}}})
    summary = client.get(f"/api/sandboxes/{sandbox['id']}/configuration").json()
    assert not summary["ready"] and summary["variables"][0]["steps"][0]["value"] is None
    with pytest.raises(ResourceValidationError, match="unbound"):
        resolve_sandbox_configuration(services, sandbox["id"])
    edit(client, sandbox, "replace", {"variables": {"FLAGS": "replacement"}})
    def forbidden(*args):
        raise AssertionError("Replaced secrets must not be resolved")
    monkeypatch.setattr(services.execution_credentials, "resolve", forbidden)
    assert resolve_sandbox_configuration(services, sandbox["id"]) == ({"FLAGS": "replacement"}, ())


def test_path_folder_extensions_reuse_bindings_and_map_each_layer_independently(client, tmp_path):
    sandbox = create_node(client, "sandbox")
    profile = create_node(client, "environment")
    for node, label, operation in ((profile, "first", "append"), (sandbox, "second", "prepend")):
        path = tmp_path / label
        path.mkdir()
        response = client.put(f"/api/nodes/{node['id']}/environment", json={
            "value": {"variables": {"PATH": {"path_ref": label, "operation": operation}}},
            "secrets": {}, "folders": {label: {"path": str(path)}}, "expected_revision": 0})
        assert response.status_code == 200, response.text
        assert response.json()["value"]["variables"]["PATH"] == {"path_ref": label, "operation": operation}
    connect(client, profile, sandbox, "environment.default")
    mounts = []
    env, _ = resolve_sandbox_configuration(client.app.state.services, sandbox["id"], folder_mounts=mounts)
    mapped = folder_environment(env, mounts, tmp_path / "project", ResourceAccess.READ_WRITE)
    assert minimal_linux_environment(invocation_env=mapped)["PATH"] == mounts[1].linux_path + ":" + minimal_linux_environment()["PATH"] + ":" + mounts[0].linux_path
    assert mounts[0].linux_path != mounts[1].linux_path
    assert env["PATH"][0]["value"] == str(tmp_path / "first")
    # Bindings survive re-reading the persisted document.
    assert client.get(f"/api/nodes/{profile['id']}/environment-folders").json()["first"]["path"] == str(tmp_path / "first")


def test_path_folder_rejects_files_before_saving_authority(client, tmp_path):
    sandbox = create_node(client, "sandbox")
    file = tmp_path / "tool.exe"
    file.write_text("not-a-directory")
    response = client.put(f"/api/nodes/{sandbox['id']}/environment", json={
        "value": {"variables": {"PATH": {"path_ref": "tool", "operation": "append"}}},
        "secrets": {}, "folders": {"tool": {"path": str(file)}}, "expected_revision": 0})
    assert response.status_code == 422
    assert client.get(f"/api/nodes/{sandbox['id']}/environment-folders").json() == {}


def test_dispatch_scopes_operations_and_keeps_inherited_secrets_private(runtime_client, monkeypatch):
    client, _, native = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    monkeypatch.setenv("HOST_ONLY_ENVIRONMENT", "private-host-setting")
    secret = "private-inherited-token-843"
    response = client.put("/api/settings/sandbox", json={
        "environment_variables": {"FLAGS": {"secret_ref": "base"}}, "secrets": {"base": secret}})
    assert response.status_code == 200, response.text
    edit(client, sandbox, "replace", {"variables": {
        "flags": extension("local", separator=":"), "PATH": extension("D:\\Project tools", "prepend")}})
    context = client.get(f"/api/nodes/{sandbox['id']}/environment-context")
    assert context.json()["COMSPEC"] == "runtime" and "HOST_ONLY_ENVIRONMENT" not in context.json()
    assert secret not in context.text
    summary = client.get(f"/api/sandboxes/{sandbox['id']}/configuration")
    assert secret not in summary.text and summary.json()["ready"]
    original = native.run_appcontainer
    def echo_flags(*args, **kwargs):
        return replace(original(*args, **kwargs), stdout=kwargs["environment"].get("flags", ""))
    monkeypatch.setattr(native, "run_appcontainer", echo_flags)
    provider, definitions = tools(client, agent)
    result = invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"])
    assert result["stdout"] == "[REDACTED]:local"
    assert native.last_environment["flags"] == secret + ":local"
    assert native.last_environment["PATH"].startswith("D:\\Project tools;")
    assert "HOST_ONLY_ENVIRONMENT" not in native.last_environment
    edit(client, sandbox, "replace", {"variables": {}})
    assert client.put("/api/settings/sandbox", json={"environment_variables": {}}).status_code == 200
    invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"])
    assert "flags" not in native.last_environment and "D:\\Project tools" not in native.last_environment["PATH"]


@pytest.mark.asyncio
async def test_wsl_transports_operations_and_maps_host_folders_before_composing(tmp_path, monkeypatch):
    from backend.sandbox.wsl import WslSandboxBackend
    from backend.sandbox import linux_worker
    backend = WslSandboxBackend(tmp_path / "managed", distribution="Ubuntu")
    monkeypatch.setattr(backend, "get", AsyncMock(return_value=SandboxInfo("box", SandboxState.READY, Path("/workspace"))))
    monkeypatch.setattr(backend, "prepare_python", AsyncMock())
    monkeypatch.setattr(linux_worker, "_linux_path", lambda value: "/mnt/d/Project tools")
    async def receive(payload, **kwargs):
        wire = json.loads(json.dumps(payload))
        mounts = linux_worker._folder_mounts(wire)
        assert mounts[0].environment_index == 0 and mounts[0].source == "/mnt/d/Project tools"
        mapped = folder_environment(wire["invocation_env"], mounts, "/workspace", ResourceAccess.READ_WRITE)
        environment = minimal_linux_environment(invocation_env=mapped)
        return asdict(CommandResult("box", ("true",), 0, environment["PATH"], "", 0))
    monkeypatch.setattr(backend, "_request", receive)
    result = await backend.execute("box", ["true"],
        invocation_env={"PATH": [extension("D:\\Project tools")]},
        folder_mounts=(FolderMount("PATH", "D:\\Project tools", environment_index=0),))
    assert result.stdout == minimal_linux_environment()["PATH"] + ":/sandbox/folders/PATH/operation-0"


def test_wsl_shipped_worker_loads_and_applies_operations_without_site_packages():
    from backend.sandbox.wsl import _BOOTSTRAP, _envelope
    payload = json.loads(_envelope({}))
    payload["modules"].append(["environment_probe", """
from .environment import apply_invocation_environment
environment = {'PATH': '/usr/bin:/bin'}
apply_invocation_environment(environment, {'PATH': [{'value': '/workspace/bin', 'operation': 'append'}]})
print(environment['PATH'], flush=True)
raise SystemExit(0)
"""])
    result = subprocess.run([sys.executable, "-I", "-S", "-c", _BOOTSTRAP],
        input=json.dumps(payload) + "\n", text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/usr/bin:/bin:/workspace/bin"
