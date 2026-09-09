"""Configuration selection uses live graph scopes and the existing OS backends."""
import asyncio
import json
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from backend.config import Settings
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.main import create_app
from backend.sandbox.environment import minimal_windows_environment
from backend.sandbox.linux import minimal_linux_environment, bubblewrap_command, _host_control_environment
from backend.sandbox.models import SandboxError, SandboxValidationError, ResourceAccess
from backend.sandbox.wsl import _transport_environment
from backend.tests.conftest import create_node
from backend.tests.test_skill_packages import edit
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.tests.test_summoning import equip, stock, invoke as summon, settle
from backend.tests.test_tool_projection import connect, tools, invoke
from open_agent_world.plugin_api import ComputeTarget, PluginDefinition, PluginDescriptor, NodeDocumentAction


def profile(client, variables=None, name="Environment"):
    card = create_node(client, "environment", name=name)
    edit(client, card, "replace", {"variables": variables or {"REGION": "test", "API_TOKEN": {"secret_ref": "token"}}})
    return card


@pytest.mark.parametrize("node_type", ["environment", "compute-target"])
def test_execution_configuration_nodes_accept_their_default_status(client, node_type):
    card = create_node(client, node_type, status="available")
    assert card["status"] == "available"
    assert card["config"]["status"] == "available"


def bind(client, card, value="test-private-token-742"):
    doc = client.get(f"/api/nodes/{card['id']}/document").json()
    response = client.put(f"/api/nodes/{card['id']}/credentials/token", json={"value": value, "expected_revision": doc["revision"]})
    assert response.status_code == 200, response.text
    return value


@pytest.mark.parametrize("node_type", ["environment", "sandbox"])
def test_save_environment_binds_secret_without_exposing_it(client, node_type):
    card = create_node(client, node_type)
    node_id = card["id"]
    path = f"/api/nodes/{node_id}"
    before = client.get(path + "/document").json()
    value = {"variables": {"REGION": "test", "API_TOKEN": {"secret_ref": "token"}}}
    secret = "private-save-environment-742"
    payload = {"value": value, "secrets": {"token": secret}, "expected_revision": before["revision"]}
    response = client.put(path + "/environment", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["value"] == value
    assert secret not in response.text
    assert secret not in client.get(path + "/document").text
    assert client.get(path + "/credentials").json() == {"token": True}
    assert client.app.state.services.execution_credentials.resolve(node_id, "token") == secret
    # A stale save must not replace the stored credential.
    payload["secrets"]["token"] = "stale-replacement"
    assert client.put(path + "/environment", json=payload).status_code == 409
    assert client.app.state.services.execution_credentials.resolve(node_id, "token") == secret
    # Leaving an existing secret untouched preserves it while other values change.
    payload.update(secrets={}, expected_revision=response.json()["revision"])
    payload["value"]["variables"]["REGION"] = "updated"
    assert client.put(path + "/environment", json=payload).status_code == 200
    assert client.app.state.services.execution_credentials.resolve(node_id, "token") == secret


def test_save_environment_rolls_back_bindings_when_document_write_fails(client, monkeypatch):
    card = profile(client)
    original = bind(client, card)
    path = f"/api/nodes/{card['id']}"
    before = client.get(path + "/document").json()
    def fail(*args, **kwargs):
        raise ResourceValidationError("Document write failed")
    monkeypatch.setattr("backend.node_documents.write_document", fail)
    response = client.put(path + "/environment", json={"value": before["value"],
        "secrets": {"token": "replacement"}, "expected_revision": before["revision"]})
    assert response.status_code == 422
    assert client.app.state.services.execution_credentials.resolve(card["id"], "token") == original
    assert client.get(path + "/document").json() == before


def test_save_environment_rejects_invalid_secret_without_echo(client):
    card = profile(client)
    path = f"/api/nodes/{card['id']}"
    before = client.get(path + "/document").json()
    secret = "private-invalid-secret\0"
    response = client.put(path + "/environment", json={"value": before["value"],
        "secrets": {"token": secret}, "expected_revision": before["revision"]})
    assert response.status_code == 422
    assert "private-invalid-secret" not in response.text
    assert client.get(path + "/credentials").json() == {"token": False}


def test_saved_sandbox_secret_reaches_agent_and_skill_commands(runtime_client):
    client, _, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    path = f"/api/nodes/{sandbox['id']}"
    document = client.get(path + "/document").json()
    response = client.put(path + "/environment", json={
        "value": {"variables": {"API_TOKEN": {"secret_ref": "automatic"}}},
        "secrets": {"automatic": "saved-command-secret"}, "expected_revision": document["revision"],
    })
    assert response.status_code == 200, response.text
    provider, definitions = tools(client, agent)
    invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"])
    assert native.last_environment["API_TOKEN"] == "saved-command-secret"
    invoke(client, provider, agent, definitions["run_skill_script"], sandbox=sandbox["id"], skill=skill["id"],
        script="scripts/check.py", interpreter=["python"])
    assert native.last_environment["API_TOKEN"] == "saved-command-secret"


def test_unbound_secret_error_identifies_variable_and_owner(client):
    from backend.execution_config import resolve_execution_configuration
    card = profile(client)
    with pytest.raises(ResourceValidationError, match="Secret variable 'API_TOKEN' is unbound on 'Environment'"):
        resolve_execution_configuration(client.app.state.services, card["id"], None)


@pytest.mark.parametrize("equipped", [False, True])
def test_optional_resources_live_selection_and_isolation(runtime_client, equipped, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    provider, initial = tools(client, agent)
    execute = initial["execute_command"]
    assert execute.input_schema["required"] == ["sandbox", "argv"]
    assert "environment" not in execute.input_schema["properties"]
    assert "target" not in execute.input_schema["properties"]
    assert initial["run_skill_script"].input_schema["required"] == ["sandbox", "skill", "script"]
    invoke(client, provider, agent, execute, sandbox=sandbox["id"], argv=["cmd.exe", "/c", "echo ok"])
    assert "REGION" not in native.last_environment
    env = profile(client)
    secret = bind(client, env)
    second = profile(client, {"REGION": "second"}, name="Second")
    target = create_node(client, "compute-target", name="Destination")
    destination = {"name": "Test", "provider_id": "example", "config": {"queue": ["local"], "count": 3}}
    edit(client, target, "replace", destination)
    for card, relationship in [(env, "environment.use"), (second, "environment.use"), (target, "compute_target.read")]:
        if equipped:
            equip(client, card, agent)
        else:
            edge = connect(client, agent, card, relationship)
            if card == env:
                env_edge = edge
    monkeypatch.setenv("UNRELATED_HOST_SECRET", "do-not-inherit")
    provider, definitions = tools(client, agent)
    assert len([name for name in definitions if name.startswith("execute_command")]) == 1
    assert set(definitions["execute_command"].input_schema["properties"]["environment"]["enum"]) == {"environment", "second"}
    assert invoke(client, provider, agent, definitions["read_compute_target"], target="destination")["value"] == destination
    inspected = invoke(client, provider, agent, definitions["inspect_environment_profile"], target="environment")
    assert secret not in json.dumps(inspected)
    invoke(client, provider, agent, execute, sandbox=sandbox["id"], argv=["cmd.exe"], environment="environment", target="destination")
    assert native.last_environment["API_TOKEN"] == secret
    assert json.loads(native.last_environment["OAW_TARGET_CONFIG_JSON"]) == destination
    assert "UNRELATED_HOST_SECRET" not in native.last_environment and "API_TOKEN" not in os.environ
    invoke(client, provider, agent, initial["run_skill_script"], sandbox=sandbox["id"], skill=skill["id"],
        script="scripts/check.py", interpreter=["python"], environment="second")
    assert native.last_environment["REGION"] == "second"
    assert "API_TOKEN" not in native.last_environment and "OAW_TARGET_CONFIG_JSON" not in native.last_environment
    invoke(client, provider, agent, execute, sandbox=sandbox["id"], argv=["cmd.exe"])
    assert "REGION" not in native.last_environment
    if equipped:
        assert client.patch(f"/api/nodes/{env['id']}", json={"equipment": None}).status_code == 200
    else:
        assert client.delete(f"/api/edges/{env_edge['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        invoke(client, provider, agent, execute, sandbox=sandbox["id"], argv=["cmd.exe"], environment=env["id"])
    for bad in (None, [], [second["id"], env["id"]], 1):
        with pytest.raises(ResourceValidationError):
            invoke(client, provider, agent, execute, sandbox=sandbox["id"], argv=["cmd.exe"], environment=bad)


@pytest.mark.parametrize("revoked", ["sandbox", "skill", "environment", "target"])
def test_all_scopes_rechecked_after_handler_exposure_before_secret_resolution(runtime_client, monkeypatch, revoked):
    client, _, native = runtime_client
    services = client.app.state.services
    agent, sandbox, skill, _, edges = setup_skill(client)
    env = profile(client)
    bind(client, env)
    target = create_node(client, "compute-target")
    env_edge = connect(client, agent, env, "environment.use")
    target_edge = connect(client, agent, target, "compute_target.read")
    provider, definitions = tools(client, agent)
    original = services.execute_sandbox
    selected_edge = {"sandbox": edges[0], "skill": edges[1], "environment": env_edge, "target": target_edge}[revoked]
    async def revoke_then_execute(self, *args, **kwargs):
        await services.delete_edge(selected_edge["id"])
        return await original(*args, **kwargs)
    monkeypatch.setattr(type(services), "execute_sandbox", revoke_then_execute)
    def forbidden_read(*args):
        raise AssertionError("Secrets must not be resolved before checking every scope")
    monkeypatch.setattr("backend.security.execution_credentials.ExecutionCredentialStore.resolve", forbidden_read)
    with pytest.raises(PermissionDeniedError):
        invoke(client, provider, agent, definitions["run_skill_script"], sandbox=sandbox["id"], skill=skill["id"],
            script="scripts/check.py", environment=env["id"], target=target["id"])
    assert native.last_argv == ()


@pytest.mark.parametrize("key", ["OAW_TARGET_CONFIG_JSON", "oaw_target_config_json", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "PATH", "home", "COMSPEC", "SystemRoot", "BASH_ENV", "ENV", "PYTHONPATH", "NODE_OPTIONS", "WSLENV", "SANDBOX_RESOURCES"])
def test_reserved_profile_variables_rejected_before_storage(client, key):
    env = create_node(client, "environment")
    response = client.post(f"/api/nodes/{env['id']}/actions/replace", json={"arguments": {"variables": {key: "unsafe"}}, "expected_revision": 0})
    assert response.status_code == 422
    assert client.get(f"/api/nodes/{env['id']}/document").json()["value"] == {"variables": {}}


@pytest.mark.parametrize("values", [[], "", {"A": "one", "a": "two"}, {"BAD-NAME": "v"}, {"VALID": "\0"}, {"VALID": 1}, {"VALID": "x" * 25000}])
def test_unsupported_environment_fails_on_both_builders(tmp_path, values):
    for builder in (lambda: minimal_windows_environment(tmp_path, invocation_env=values), lambda: minimal_linux_environment(invocation_env=values)):
        with pytest.raises(SandboxValidationError):
            builder()


def test_backend_carriers_leave_helpers_and_existing_allowlists_intact(tmp_path):
    values = {"REGION": "test", "API_TOKEN": "isolated", "OAW_TARGET_CONFIG_JSON": '{"config":{"x":1}}'}
    windows = minimal_windows_environment(tmp_path, invocation_env=values)
    linux = minimal_linux_environment(invocation_env=values)
    assert all(windows[key] == linux[key] == value for key, value in values.items())
    assert not set(values) & _transport_environment().keys()
    if os.name != "nt":
        assert not set(values) & _host_control_environment().keys()
    command = bubblewrap_command(Path("/host/work"), ResourceAccess.READ_WRITE, [], ["/bin/true"], linux)
    assert "--unshare-all" in command and "--clearenv" in command
    assert command[command.index("API_TOKEN") - 1:command.index("API_TOKEN") + 2] == ["--setenv", "API_TOKEN", "isolated"]
    for builder in (lambda: minimal_windows_environment(tmp_path, {"API_TOKEN": "no"}), lambda: minimal_linux_environment({"API_TOKEN": "no"}),
                    lambda: minimal_windows_environment(tmp_path, {"LANG": "en"}, invocation_env={"LANG": "fr"}),
                    lambda: minimal_linux_environment({"LANG": "en"}, invocation_env={"LANG": "fr"})):
        with pytest.raises(SandboxValidationError):
            builder()


@pytest.mark.parametrize("operation", ["duplicate", "summon", "template"])
def test_secret_encryption_inspection_portability_and_explicit_rebinding(runtime_client, operation):
    client, _, _ = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client, equipment=True)
    env = profile(client)
    equip(client, env, agent)
    secret = bind(client, env)
    services = client.app.state.services
    assert client.get(f"/api/nodes/{env['id']}/credentials").json() == {"token": True}
    with services.database.locked() as connection:
        rows = connection.execute("SELECT value_json FROM application_settings").fetchall()
    assert secret not in str([tuple(row) for row in rows])
    assert secret not in client.get("/api/world").text
    if operation == "duplicate":
        response = client.post(f"/api/nodes/{agent['id']}/duplicate")
        assert response.status_code == 200, response.text
        nodes = response.json()["nodes"]
    elif operation == "summon":
        box = create_node(client, "oaw.barracks")
        stock(client, box, agent)
        instance = settle(client, box, summon(client, box, action="summon", agent_id=agent["id"], prompt="Work"))
        nodes = [client.get(f"/api/nodes/{key}").json() for key in instance["node_ids"]]
    else:
        response = client.post("/api/legions", json={"name": "Config", "node_ids": [agent["id"], sandbox["id"], env["id"]]})
        assert response.status_code == 201, response.text
        legion = response.json()
        blueprint = services.legions.get(legion["id"])
        assert secret not in str(blueprint) and "execution_credential:" not in str(blueprint)
        response = client.post(f"/api/legions/{legion['id']}/instances", json={"position": {"x": 0, "y": 0}})
        assert response.status_code == 201, response.text
        nodes = response.json()["nodes"]
    fresh = next(node for node in nodes if node["type"] == "environment")
    assert client.get(f"/api/nodes/{fresh['id']}/credentials").json() == {"token": False}
    document = client.get(f"/api/nodes/{fresh['id']}/document").json()["value"]
    assert document["variables"]["API_TOKEN"] == {"secret_ref": "token"}
    with pytest.raises(ResourceValidationError, match="unbound"):
        services.execution_credentials.resolve(fresh["id"], "token")
    bind(client, fresh, "fresh-value")
    assert services.execution_credentials.resolve(fresh["id"], "token") == "fresh-value"
    assert services.execution_credentials.resolve(env["id"], "token") == secret


def test_deleted_id_restoration_cannot_reclaim_binding(client):
    env = profile(client)
    bind(client, env)
    assert client.delete(f"/api/nodes/{env['id']}").status_code == 200
    restored = create_node(client, "environment", id=env["id"])
    edit(client, restored, "replace", {"variables": {"API_TOKEN": {"secret_ref": "token"}}})
    assert client.get(f"/api/nodes/{restored['id']}/credentials").json() == {"token": False}


def test_secret_output_redacts_split_chunks_events_and_errors(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    services = client.app.state.services
    backend._event_sink = services.publish_sandbox_event
    agent, sandbox, _, _, _ = setup_skill(client)
    env = profile(client)
    secret = bind(client, env)
    connect(client, agent, env, "environment.use")
    provider, definitions = tools(client, agent)
    events = []
    original_publish = services.events.publish
    async def capture(*args, **kwargs):
        events.append((args, kwargs))
        return await original_publish(*args, **kwargs)
    monkeypatch.setattr(services.events, "publish", capture)
    original_native = native.run_appcontainer
    def emitting(*args, **kwargs):
        result = original_native(*args, **kwargs)
        kwargs["on_stdout"](secret[:8])
        kwargs["on_stdout"](secret[8:])
        return replace(result, stdout="before " + secret + " after", stderr=secret)
    monkeypatch.setattr(native, "run_appcontainer", emitting)
    result = invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"], environment=env["id"])
    assert result["stdout"] == "before [REDACTED] after" and result["stderr"] == "[REDACTED]"
    assert secret not in str(events) and secret[:8] not in str(events)
    assert "[REDACTED]" in str(events)
    def failing(*args, **kwargs):
        raise RuntimeError("failure: " + secret)
    monkeypatch.setattr(native, "run_appcontainer", failing)
    with pytest.raises(SandboxError, match=r"failure: \[REDACTED\]"):
        invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"], environment=env["id"])
    assert secret not in str(events)


def test_plugin_target_uses_its_document_model_validation(client):
    registry = client.app.state.services.plugins
    class Fields(BaseModel):
        model_config = ConfigDict(extra="forbid")
        replicas: int = Field(default=1, ge=1, le=4)
    class Target(ComputeTarget):
        config: Fields = Field(default_factory=Fields)
    def register(registration):
        original = registry.node_type("compute-target")
        registration.register_node_type(replace(original, id="example.target", document=replace(original.document, model=Target)))
    registry.install(PluginDefinition(PluginDescriptor(id="example.targets", version="1", plugin_api_version="1.11"), register))
    target = create_node(client, "example.target")
    edit(client, target, "replace", {"provider_id": "descriptive", "config": {"replicas": 3}})
    doc = client.get(f"/api/nodes/{target['id']}/document").json()
    response = client.post(f"/api/nodes/{target['id']}/actions/replace", json={"expected_revision": doc["revision"], "arguments": {"config": {"replicas": 7}}})
    assert response.status_code == 422
    agent = create_node(client, "agent")
    connect(client, agent, target, "compute_target.read")
    provider, definitions = tools(client, agent)
    assert invoke(client, provider, agent, definitions["read_compute_target"], target=target["id"])["value"]["config"] == {"replicas": 3}


@pytest.mark.parametrize("read_only,kind", [(False, "compute_target.read"), (True, "text.edit")])
def test_shared_document_actions_cannot_adopt_foreign_write_operations(client, read_only, kind):
    registry = client.app.state.services.plugins
    def register(registration):
        original = registry.node_type("compute-target")
        action = NodeDocumentAction(lambda value, arguments: value, capability_kind=kind, read_only=read_only)
        registration.register_node_type(replace(original, id="example.unsafe", document=replace(original.document, actions={"read": action})))
    with pytest.raises(ValueError, match="same plugin"):
        registry.install(PluginDefinition(PluginDescriptor(id="example.unsafe", version="1", plugin_api_version="1.11"), register))
    assert not registry.has_plugin("example.unsafe")


def test_unsupported_backend_rejects_selection_before_resolving_secrets(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client)
    env = profile(client)
    connect(client, agent, env, "environment.use")
    provider, definitions = tools(client, agent)
    monkeypatch.setattr(backend, "supports_invocation_environment", False)
    with pytest.raises(SandboxValidationError, match="does not support"):
        invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"], environment=env["id"])
    assert native.last_argv == ()
    invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"])
    assert native.last_argv == ("cmd.exe",)


def test_concurrent_commands_have_separate_secret_environments_and_redaction(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    services = client.app.state.services
    backend._event_sink = services.publish_sandbox_event
    agent, first, _, _, _ = setup_skill(client)
    second = create_node(client, "sandbox")
    connect(client, agent, second, "execute")
    assert client.post(f"/api/sandboxes/{second['id']}/start").status_code == 200
    envs = [profile(client, name=name) for name in ("First", "Second")]
    for index, env in enumerate(envs):
        bind(client, env, f"private-value-{index}")
        connect(client, agent, env, "environment.use")
    provider, definitions = tools(client, agent)
    barrier = threading.Barrier(2)
    observed = []
    original = native.run_appcontainer
    def overlapping(*args, **kwargs):
        own_secret = kwargs["environment"]["API_TOKEN"]
        observed.append(own_secret)
        barrier.wait(timeout=5)
        result = original(*args, **kwargs)
        kwargs["on_stdout"](own_secret)
        return replace(result, stdout=own_secret)
    monkeypatch.setattr(native, "run_appcontainer", overlapping)
    async def run_both():
        return await asyncio.gather(*(provider.invoke_tool(agent["id"], definitions["execute_command"].capability_id,
            {"sandbox": box["id"], "argv": ["cmd.exe"], "environment": env["id"]})
            for box, env in zip((first, second), envs)))
    results = client.portal.call(run_both)
    assert set(observed) == {"private-value-0", "private-value-1"}
    assert [result["stdout"] for result in results] == ["[REDACTED]", "[REDACTED]"]
    monkeypatch.setattr(native, "run_appcontainer", original)
    invoke(client, provider, agent, definitions["execute_command"], sandbox=first["id"], argv=["cmd.exe"])
    assert "API_TOKEN" not in native.last_environment


def test_binding_request_validation_never_echoes_secret_and_requires_current_document(client):
    env = profile(client)
    url = f"/api/nodes/{env['id']}/credentials/token"
    for payload in ({"value": {"secret": "private"}, "expected_revision": 1}, {"value": "private", "unexpected": "private"}):
        response = client.put(url, json=payload)
        assert response.status_code == 422 and "private" not in response.text
    response = client.put(url, json={"value": "private", "expected_revision": -1})
    assert response.status_code == 409 and "private" not in response.text
    doc = client.get(f"/api/nodes/{env['id']}/document").json()
    response = client.put(f"/api/nodes/{env['id']}/credentials/missing", json={"value": "private", "expected_revision": doc["revision"]})
    assert response.status_code == 422 and "private" not in response.text
    assert client.get(f"/api/nodes/{env['id']}/credentials").json() == {"token": False}


@pytest.mark.skipif(not os.environ.get("OAW_TEST_SANDBOX_RUNTIME"), reason="requires selected real OS runtime")
def test_real_execution_environment_reaches_only_command_and_children(tmp_path):
    runtime = os.environ["OAW_TEST_SANDBOX_RUNTIME"]
    settings = replace(Settings.for_data_root(tmp_path / "managed"), sandbox_runtime=runtime, agent_runtime="core.mock")
    with TestClient(create_app(settings)) as client:
        agent, sandbox, skill, _, _ = setup_skill(client)
        env = profile(client)
        bind(client, env)
        connect(client, agent, env, "environment.use")
        target = create_node(client, "compute-target")
        edit(client, target, "replace", {"name": "Example", "provider_id": "demo", "config": {"count": 2}})
        connect(client, agent, target, "compute_target.read")
        provider, definitions = tools(client, agent)
        if runtime == "windows":
            argv = ["cmd.exe", "/d", "/c", 'echo %REGION% & echo %API_TOKEN% & echo %OAW_TARGET_CONFIG_JSON% & cmd.exe /d /c set REGION']
            omitted = ["cmd.exe", "/d", "/c", "set REGION & set API_TOKEN & set OAW_TARGET_CONFIG_JSON"]
        else:
            argv = ["/usr/bin/python3", "-c", "import os,json,subprocess; print(os.environ['REGION']); print(os.environ['API_TOKEN']); print(json.loads(os.environ['OAW_TARGET_CONFIG_JSON'])['config']['count']); subprocess.run(['/usr/bin/printenv','REGION'],check=True)"]
            omitted = ["/usr/bin/python3", "-c", "import os; assert not {'REGION','API_TOKEN','OAW_TARGET_CONFIG_JSON'} & os.environ.keys()"]
        result = invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=argv, environment=env["id"], target=target["id"])
        assert result["exit_code"] == 0 and result["stdout"].count("test") >= 2
        assert "[REDACTED]" in result["stdout"] and "test-private-token-742" not in result["stdout"]
        if runtime == "windows":
            script_path = "scripts/check.cmd"
            script = "@echo off\necho %REGION%\necho %OAW_TARGET_CONFIG_JSON%\necho %API_TOKEN%\n"
            interpreter = ["cmd.exe", "/d", "/c", "call"]
        else:
            script_path = "scripts/describe.py"
            script = (Path(__file__).parents[2] / "examples/skills/execution-configuration/scripts/describe.py").read_text()
            interpreter = ["/usr/bin/python3"]
        edit(client, skill, "replace", {"files": {script_path: script}})
        result = invoke(client, provider, agent, definitions["run_skill_script"], sandbox=sandbox["id"], skill=skill["id"],
            script=script_path, interpreter=interpreter, environment=env["id"], target=target["id"])
        assert result["exit_code"] == 0 and "demo" in result["stdout"], result
        assert "test-private-token-742" not in result["stdout"]
        assert client.get(f"/api/sandboxes/{sandbox['id']}").json()["network_enabled"] is False
        result = invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=omitted)
        assert "test-private-token-742" not in result["stdout"]
        assert result["exit_code"] != 0 if runtime == "windows" else result["exit_code"] == 0
        assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200
