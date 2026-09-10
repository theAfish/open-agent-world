"""Live graph -> ordinary Sandbox backend, plus opt-in real OS acceptance."""
import base64
import asyncio
from dataclasses import replace
import os
import threading
from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.main import create_app
from backend.plugins.loader import load_plugin_registry
from backend.sandbox.linux import bubblewrap_command
from backend.sandbox.materialization import (
    RuntimeBundle, RuntimeMount, materialize_bundle, cleanup_materializations,
)
from backend.sandbox.models import ResourceAccess, SandboxSecurityError, SandboxState, SandboxValidationError
from backend.sandbox.windows import WindowsSandboxBackend
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.tests.test_sandbox_runtime import FakeWindowsNativeApi
from backend.tests.test_skill_packages import edit
from backend.tests.test_summoning import equip, stock, invoke, settle
from open_agent_world.skill_packages import Skill, SkillPackage, SkillPackagePlugin


@pytest.fixture
def runtime_client(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path / "managed"), agent_runtime="core.mock")
    native = FakeWindowsNativeApi()
    original_run = native.run_appcontainer
    def record_run(profile, argv, **options):
        native.last_cwd = options["cwd"]
        return original_run(profile, argv, **options)
    native.run_appcontainer = record_run
    native.grant_runtime_path = lambda path, sid: native.grant_path(path, sid, read_only=True)
    native.grant_runtime_traverse = lambda path, sid: None
    backend = WindowsSandboxBackend(settings.data_root, native_api=native)
    registry = load_plugin_registry()
    registry.install(SkillPackagePlugin(SkillPackage(package_id="example.runtime", skills=[
        Skill(name="Plugin script", files={"scripts/check.py": "print('plugin')"})])))
    services = create_services(settings, sandbox_backend=backend, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        yield client, backend, native


def setup_skill(client, *, equipment=False, toolbox=False):
    agent = create_node(client, "agent")
    sandbox = create_node(client, "sandbox")
    skill = create_node(client, "oaw.skills.skill")
    edit(client, skill, "replace", {"name": "Example", "instructions": "Run the check.",
        "files": {"scripts/check.py": "print('ok')", "templates/report.md": "# Report",
                  "assets/data.bin": {"data_base64": base64.b64encode(b"\0\xff").decode()}},
        "defaults": {"not_a_runtime_file": "private setting"}})
    access = skill
    if toolbox:
        access = create_node(client, "oaw.skills")
        assert client.patch(f"/api/nodes/{skill['id']}", json={"parent_id": access["id"]}).status_code == 200
    edges = []
    for target, relationship in [(sandbox, "execute"), (access, access["type"] + ".use")]:
        if equipment:
            equip(client, target, agent)
        else:
            response = client.post("/api/edges", json={"source": agent["id"], "target": target["id"], "relationship": relationship})
            assert response.status_code == 201, response.text
            edges.append(response.json())
    started = client.post(f"/api/sandboxes/{sandbox['id']}/start")
    assert started.status_code == 200, started.text
    return agent, sandbox, skill, access, edges


def run(client, agent, sandbox, skill, **arguments):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    if "script_path" in arguments:
        arguments["script"] = arguments.pop("script_path")
    return client.portal.call(provider.invoke_tool, agent["id"], "operation:run_skill_script",
        {"sandbox": sandbox["id"], "skill": skill["id"], "script": "scripts/check.py",
         "interpreter": ["python"], **arguments})


@pytest.mark.parametrize("equipment,toolbox", [(False, False), (False, True), (True, False), (True, True)])
def test_lazy_current_bundle_uses_normal_native_backend_and_workspace(runtime_client, equipment, toolbox):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client, equipment=equipment, toolbox=toolbox)
    root = backend._sandboxes_root / sandbox["id"]
    assert not (root / ".oaw").exists()
    before = client.get(f"/api/nodes/{skill['id']}/document").json()
    result = run(client, agent, sandbox, skill, argv=["", "a b", "$(literal)"])
    folder = root / ".oaw" / "skills" / skill["id"]
    script = folder / "scripts/check.py"
    assert result["exit_code"] == 0
    assert native.last_argv == ("python", str(script), "", "a b", "$(literal)")
    assert native.last_cwd == root / "workspace"
    assert script.read_text() == "print('ok')"
    assert (folder / "assets/data.bin").read_bytes() == b"\0\xff"
    assert not (root / "workspace" / ".oaw").exists()
    assert client.get(f"/api/nodes/{skill['id']}/document").json() == before
    assert (script, True) in native.grants and script in native.protected and script in native.revocations
    timestamp = script.stat().st_mtime_ns
    run(client, agent, sandbox, skill)
    assert script.stat().st_mtime_ns == timestamp
    assert list((root / ".oaw/skills").iterdir()) == [folder]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "SKILL.md", "assets/data.bin", "scripts/check.py", "templates/report.md"]
    edit(client, skill, "replace", {**before["value"], "files": {"scripts/check.py": "print('updated')"}})
    run(client, agent, sandbox, skill)
    assert script.read_text() == "print('updated')"
    assert not (folder / "assets").exists()
    assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200
    assert not root.exists()


@pytest.mark.parametrize("revoke", ["skill", "sandbox"])
@pytest.mark.parametrize("equipment", [False, True])
def test_live_revocation_and_independent_authorization(runtime_client, revoke, equipment):
    client, backend, native = runtime_client
    agent, sandbox, skill, access, edges = setup_skill(client, equipment=equipment)
    stranger = create_node(client, "agent")
    with pytest.raises(PermissionDeniedError):
        run(client, stranger, sandbox, skill)
    unrelated = create_node(client, "oaw.skills.skill")
    with pytest.raises(PermissionDeniedError):
        run(client, agent, sandbox, unrelated)
    run(client, agent, sandbox, skill)
    previous = native.last_argv
    if equipment:
        target = access if revoke == "skill" else sandbox
        assert client.patch(f"/api/nodes/{target['id']}", json={"equipment": None}).status_code == 200
    else:
        assert client.delete(f"/api/edges/{edges[1 if revoke == 'skill' else 0]['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        run(client, agent, sandbox, skill)
    assert native.last_argv == previous
    if revoke == "skill":
        native.grants.clear()
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        client.portal.call(provider.invoke_tool, agent["id"], f"sandbox.execute:{sandbox['id']}", {"argv": ["python", previous[1]]})
        assert not any(".oaw" in str(path) for path, _ in native.grants)


def test_toolbox_membership_is_not_authorization_and_moves_revoke_access(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, skill, toolbox, _ = setup_skill(client, toolbox=True)
    run(client, agent, sandbox, skill)
    assert client.patch(f"/api/nodes/{skill['id']}", json={"parent_id": None}).status_code == 200
    with pytest.raises(PermissionDeniedError):
        run(client, agent, sandbox, skill)


def test_plugin_skill_package_uses_the_same_runtime_path(runtime_client):
    client, backend, native = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client)
    box = create_node(client, "example.runtime.toolbox")
    response = client.post("/api/edges", json={"source": agent["id"], "target": box["id"],
        "relationship": "example.runtime.toolbox.use"})
    assert response.status_code == 201, response.text
    value = client.get(f"/api/nodes/{box['id']}/document").json()["value"]
    skill = {"id": value["skills"][0]["node_id"]}
    run(client, agent, sandbox, skill)
    script = backend._sandboxes_root / sandbox["id"] / ".oaw/skills" / skill["id"] / "scripts/check.py"
    assert script.read_text() == "print('plugin')" and native.last_argv[-1] == str(script)


def test_partial_runtime_grant_failure_revokes_and_never_launches(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    granted = []
    def fail_after_grant(path, sid):
        native.grant_path(path, sid, read_only=True)
        granted.append(path)
        raise SandboxSecurityError("injected grant failure")
    monkeypatch.setattr(native, "grant_runtime_path", fail_after_grant)
    with pytest.raises(SandboxSecurityError, match="injected grant failure"):
        run(client, agent, sandbox, skill)
    assert native.last_argv == () and granted
    assert all(path in native.revocations for path in granted)
    assert client.portal.call(backend.get, sandbox["id"]).state == SandboxState.ERROR


def test_runtime_native_failure_revokes_mount_access(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    def fail(*args, **kwargs):
        raise SandboxSecurityError("injected launch failure")
    monkeypatch.setattr(native, "run_appcontainer", fail)
    with pytest.raises(SandboxSecurityError, match="injected launch failure"):
        run(client, agent, sandbox, skill)
    script = backend._sandboxes_root / sandbox["id"] / ".oaw/skills" / skill["id"] / "scripts/check.py"
    assert (script, True) in native.grants and script in native.revocations


def test_cancelled_skill_command_revokes_runtime_before_return(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    started = threading.Event()
    original = native.run_appcontainer
    def blocking(profile, argv, **options):
        started.set()
        assert options["cancel_event"].wait(5), "Cancellation did not reach native execution"
        return original(profile, argv, **options)
    monkeypatch.setattr(native, "run_appcontainer", blocking)
    async def cancel():
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        task = asyncio.create_task(provider.invoke_tool(agent["id"], f"sandbox.run_skill_script:{sandbox['id']}",
            {"skill_id": skill["id"], "script_path": "scripts/check.py", "interpreter": ["python"]}))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    client.portal.call(cancel)
    script = backend._sandboxes_root / sandbox["id"] / ".oaw/skills" / skill["id"] / "scripts/check.py"
    assert (script, True) in native.grants and script in native.revocations
    assert client.portal.call(backend.get, sandbox["id"]).state == SandboxState.STOPPED


def test_runtime_cache_rejects_hardlinks_before_reuse_or_cleanup(tmp_path):
    bundle = RuntimeBundle("skills/test", (("script", b"data"),))
    folder = materialize_bundle(tmp_path, bundle)
    os.link(folder / "script", tmp_path / "alias")
    with pytest.raises(SandboxSecurityError, match="hard link"):
        materialize_bundle(tmp_path, bundle)
    with pytest.raises(SandboxSecurityError, match="hard link"):
        cleanup_materializations(tmp_path)
    assert (tmp_path / "alias").read_bytes() == b"data"
    (tmp_path / "alias").unlink()
    cleanup_materializations(tmp_path)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "scripts/../../secret", "scripts\\check.py",
    "C:/secret", "scripts//check.py", "scripts/./check.py", "scripts/check.py:stream", "scripts/check.py.", "NUL", "a\0b",
    "missing.py"])
def test_script_paths_cannot_escape_or_select_unlisted_files(runtime_client, path):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    with pytest.raises((ResourceValidationError, SandboxValidationError)):
        run(client, agent, sandbox, skill, script_path=path)
    assert native.last_argv == ()
    assert not (backend._sandboxes_root / sandbox["id"] / ".oaw").exists()


def test_agent_receives_late_bundle_validation_error_and_can_retry(runtime_client, monkeypatch):
    from backend.agents.tools import build_scoped_tool_callables
    import backend.skill_runtime as skill_runtime

    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, agent["id"])
    definition = next(d for d in definitions if d.capability_id == "operation:run_skill_script")
    tool = build_scoped_tool_callables(provider, agent["id"], [definition])[0]
    original = skill_runtime.resolve_skill_mount

    def invalid_bundle(*args, **kwargs):
        return RuntimeBundle("skills/example", (("scripts\\check.py", b""),))

    monkeypatch.setattr(skill_runtime, "resolve_skill_mount", invalid_bundle)

    async def call_tool():
        return await tool(sandbox=sandbox["id"], skill=skill["id"],
                          script="scripts/check.py", interpreter=["python"])

    result = client.portal.call(call_tool)
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_resource"
    assert "safe portable relative paths" in result["error"]["message"]
    assert "scripts" in result["error"]["message"]
    assert native.last_argv == ()
    monkeypatch.setattr(skill_runtime, "resolve_skill_mount", original)
    assert client.portal.call(call_tool)["exit_code"] == 0


@pytest.mark.parametrize("operation", ["duplicate", "summon"])
def test_copied_agents_have_fresh_runtime_and_workspace(runtime_client, operation):
    client, backend, _ = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client, equipment=True)
    run(client, agent, sandbox, skill)
    old_root = backend._sandboxes_root / sandbox["id"]
    (old_root / "workspace/output.txt").write_text("mutable result")
    if operation == "duplicate":
        response = client.post(f"/api/nodes/{agent['id']}/duplicate")
        assert response.status_code == 200, response.text
        nodes = response.json()["nodes"]
    else:
        box = create_node(client, "oaw.barracks")
        stock(client, box, agent)
        instance = settle(client, box, invoke(client, box, action="summon", agent_id=agent["id"], prompt="Work"))
        nodes = [client.get(f"/api/nodes/{key}").json() for key in instance["node_ids"]]
    fresh = {node["type"]: node for node in nodes}
    root = backend._sandboxes_root / fresh["sandbox"]["id"]
    assert not (root / ".oaw").exists() and not (root / "workspace/output.txt").exists()
    assert client.post(f"/api/sandboxes/{fresh['sandbox']['id']}/start").status_code == 200
    run(client, fresh["agent"], fresh["sandbox"], fresh["oaw.skills.skill"])
    assert (root / ".oaw/skills" / fresh["oaw.skills.skill"]["id"] / "scripts/check.py").exists()
    assert (old_root / "workspace/output.txt").read_text() == "mutable result"


def test_runtime_mount_transport_readonly_policy_and_idempotent_cleanup(tmp_path):
    bundle = RuntimeBundle("skills/test", (("scripts/a.py", b"print(1)"),), ("templates",))
    mount = RuntimeMount(bundle, 1)
    assert RuntimeMount.from_wire(mount.to_wire()) == mount
    folder = materialize_bundle(tmp_path, bundle)
    argv = mount.command(["python3", "scripts/a.py"], PurePosixPath("/.oaw/skills/test"))
    command = bubblewrap_command(Path("/host/workspace"), ResourceAccess.READ_WRITE, [], argv, {}, (folder, bundle.key))
    index = command.index(str(folder))
    assert command[index - 1:index + 2] == ["--ro-bind", str(folder), "/.oaw/skills/test"]
    assert command[command.index("--chdir") + 1] == "/workspace"
    assert "/.oaw/skills/test" not in bubblewrap_command(Path("/host/workspace"), ResourceAccess.READ_WRITE, [], ["ls"], {})
    cleanup_materializations(tmp_path)
    cleanup_materializations(tmp_path)
    assert not (tmp_path / ".oaw").exists()


@pytest.mark.parametrize("files", [(("a", b""), ("A", b"")), (("scripts/a", b""), ("Scripts/b", b"")),
    (("SKILL.md", b""), ("skill.md/child", b"")), (("COM1.txt", b""),),
    (("COM¹.txt", b""),), (("LPT²", b""),), (("CONOUT$", b""),)])
def test_bundle_rejects_filesystem_aliases(files):
    with pytest.raises(SandboxValidationError):
        RuntimeBundle("skills/test", files)


@pytest.mark.skipif(not os.environ.get("OAW_TEST_SANDBOX_RUNTIME"), reason="requires selected native runtime")
def test_real_skill_execution_immutability_outputs_and_scoped_visibility(tmp_path):
    runtime = os.environ["OAW_TEST_SANDBOX_RUNTIME"]
    settings = replace(Settings.for_data_root(tmp_path / "managed"), sandbox_runtime=runtime, agent_runtime="core.mock")
    with TestClient(create_app(settings)) as client:
        agent, sandbox, skill, _, edges = setup_skill(client)
        windows = runtime == "windows"
        path = "scripts/check.cmd" if windows else "scripts/check.py"
        content = ('@echo off\necho %CD%\necho result>output.txt\ntype "%~dp0..\\references\\keep.txt"\n'
                   'echo corrupt>"%~dp0..\\references\\keep.txt"\n'
                   'set /p value=<"%~dp0..\\references\\keep.txt"\n'
                   'if "%value%"=="original" (echo IMMUTABLE) else (exit /b 3)\n'
                   'exit /b 0\n') if windows else (
            "from pathlib import Path\nprint(Path.cwd())\nPath('output.txt').write_text('result')\n"
            "ref = Path(__file__).parent.parent / 'references/keep.txt'\nprint(ref.read_text())\n"
            "try:\n ref.write_text('corrupt')\nexcept OSError:\n print('IMMUTABLE')\nelse:\n raise RuntimeError('writable bundle')\n")
        edit(client, skill, "replace", {"instructions": "Test", "files": {path: content, "references/keep.txt": "original"}})
        interpreter = ["cmd.exe", "/d", "/c", "call"] if windows else ["python3"]
        before = client.get(f"/api/nodes/{skill['id']}/document").json()
        result = run(client, agent, sandbox, skill, script_path=path, interpreter=interpreter)
        assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
        assert "IMMUTABLE" in result["stdout"] and "original" in result["stdout"]
        info = client.get(f"/api/sandboxes/{sandbox['id']}").json()
        assert info["workspace"] in result["stdout"]
        assert client.get(f"/api/nodes/{skill['id']}/document").json() == before
        assert run(client, agent, sandbox, skill, script_path=path, interpreter=interpreter)["exit_code"] == 0
        assert client.delete(f"/api/edges/{edges[1]['id']}").status_code == 200
        with pytest.raises(PermissionDeniedError):
            run(client, agent, sandbox, skill, script_path=path, interpreter=interpreter)
        # A normal command may read workspace output but cannot reuse the cached path.
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        command = ["cmd.exe", "/d", "/c", "type output.txt"] if windows else ["cat", "output.txt"]
        output = client.portal.call(provider.invoke_tool, agent["id"], f"sandbox.execute:{sandbox['id']}", {"argv": command})
        assert output["exit_code"] == 0 and "result" in output["stdout"]
        refused = client.portal.call(provider.invoke_tool, agent["id"], f"sandbox.execute:{sandbox['id']}", {"argv": list(result["argv"])})
        assert refused["exit_code"] != 0
        assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200


@pytest.mark.parametrize("failures,winerror", [(1, 5), (1, 32), (9, 5), (1, None)])
def test_materialization_publication_retries_only_bounded_windows_denials(tmp_path, monkeypatch, failures, winerror):
    original = Path.replace
    calls = []
    def replace(path, target):
        calls.append(path)
        if len(calls) <= failures:
            error = PermissionError("publication denied")
            if winerror is not None:
                error.winerror = winerror
            raise error
        return original(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("backend.sandbox.materialization.time.sleep", lambda delay: None)
    bundle = RuntimeBundle("skills/test", (("script.py", b"print('ok')"),))
    if failures == 1 and winerror is not None:
        folder = materialize_bundle(tmp_path, bundle)
        assert (folder / "script.py").read_bytes() == b"print('ok')"
        assert len(calls) == 2
    else:
        with pytest.raises(PermissionError):
            materialize_bundle(tmp_path, bundle)
        assert len(calls) == (5 if winerror is not None else 1)
        assert not (tmp_path / ".oaw/skills/test").exists()
    assert not list((tmp_path / ".oaw").glob(".materializing-*"))


def test_agent_and_skill_timeout_reach_backend(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    observed = []
    original = native.run_appcontainer
    def capture(*args, **kwargs):
        observed.append(kwargs["timeout_seconds"])
        return original(*args, **kwargs)
    monkeypatch.setattr(native, "run_appcontainer", capture)
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    result = client.portal.call(provider.invoke_tool, agent["id"], "operation:execute_command",
        {"sandbox": sandbox["id"], "argv": ["python", "-V"], "timeout_seconds": 1200})
    assert result["exit_code"] == 0
    run(client, agent, sandbox, skill, timeout_seconds=900)
    assert observed == [1200, 900]


@pytest.mark.parametrize("value", [0, -1, 3601, float("inf"), float("nan"), True])
def test_invalid_agent_timeout_rejected(runtime_client, value):
    client, backend, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    from functools import partial
    with pytest.raises((SandboxValidationError, ResourceValidationError)):
        client.portal.call(partial(client.app.state.services.execute_sandbox,
            sandbox["id"], ["python", "-V"], agent_id=agent["id"], timeout_seconds=value))
