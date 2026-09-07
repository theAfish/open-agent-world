"""Workspace API/security integration. Native execution is mocked unless opted in."""
import asyncio
import base64
import os
from pathlib import Path

import pytest

from backend.tests.test_skill_runtime import runtime_client, setup_skill, run
from backend.tests.conftest import create_node
from backend.tests.test_skill_packages import edit
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.sandbox.files import operate, pinned, PREVIEW_LIMIT, DIRECTORY_LIMIT
from backend.sandbox.models import SandboxSecurityError, SandboxValidationError
from backend.sandbox.linux import bubblewrap_command, service_command, _SERVICE_GUARD
from backend.sandbox.models import ResourceAccess, SandboxLimits


def local(client, node, variables):
    edit(client, node, "replace", {"variables": variables})


def connect_profile(client, profile, sandbox):
    return client.post("/api/edges", json={"source": profile["id"], "target": sandbox["id"], "relationship": "environment.default"})


def test_local_linked_invocation_precedence_manual_skill_and_disconnection(runtime_client):
    client, _, native = runtime_client
    agent, sandbox, skill, _, _ = setup_skill(client)
    profile = create_node(client, "environment")
    local(client, profile, {"REGION": "base", "BASE": "one"})
    edge = connect_profile(client, profile, sandbox)
    assert edge.status_code == 201, edge.text
    local(client, sandbox, {"REGION": "local"})
    base = f"/api/sandboxes/{sandbox['id']}"
    assert client.post(base + "/execute", json={"command": "echo manual"}).status_code == 200
    assert native.last_environment["REGION"] == "local" and native.last_environment["BASE"] == "one"
    run(client, agent, sandbox, skill)
    assert native.last_environment["REGION"] == "local" and native.last_environment["BASE"] == "one"
    local(client, profile, {"BASE": "live"})
    run(client, agent, sandbox, skill)
    assert native.last_environment["BASE"] == "live"
    replacement = create_node(client, "environment")
    local(client, replacement, {"BASE": "invocation", "REGION": "other"})
    assert client.post(base + "/execute", json={"argv": ["cmd.exe"], "environment_id": replacement["id"]}).status_code == 200
    assert native.last_environment["BASE"] == "invocation" and native.last_environment["REGION"] == "local"
    assert connect_profile(client, replacement, sandbox).status_code == 422
    assert client.delete(f"/api/edges/{edge.json()['id']}").status_code == 200
    run(client, agent, sandbox, skill)
    assert "BASE" not in native.last_environment
    summary = client.get(base + "/configuration").json()
    assert summary["profile_id"] is None and summary["variables"][0]["source"] == "local"


def test_local_secret_requirements_bound_privately_and_redacted_receipts(runtime_client, monkeypatch):
    client, _, native = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    local(client, sandbox, {"TOKEN": {"secret_ref": "token"}})
    base = f"/api/sandboxes/{sandbox['id']}"
    assert client.post(base + "/execute", json={"command": "echo hello"}).status_code == 422
    assert not client.get(base + "/configuration").json()["ready"]
    doc = client.get(f"/api/nodes/{sandbox['id']}/document").json()
    secret = "private-workspace-token"
    response = client.put(f"/api/nodes/{sandbox['id']}/credentials/token", json={"value": secret, "expected_revision": doc["revision"]})
    assert response.status_code == 200, response.text
    from dataclasses import replace
    original = native.run_appcontainer
    def echo(*args, **kwargs):
        result = original(*args, **kwargs)
        kwargs["on_stdout"](secret[:8]); kwargs["on_stdout"](secret[8:])
        return replace(result, stdout=secret)
    monkeypatch.setattr(native, "run_appcontainer", echo)
    result = client.post(base + "/execute", json={"command": "echo hello"})
    assert result.status_code == 200 and secret not in result.text
    assert secret not in client.get(base + "/history").text
    summary = client.get(base + "/configuration").json()
    assert summary["variables"][0]["value"] is None and summary["ready"]
    assert secret not in client.get("/api/world").text


def test_window_read_operations_no_lifecycle_side_effects_and_bounded_files(runtime_client):
    client, backend, _ = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    info = client.portal.call(backend.get, sandbox["id"])
    (info.workspace / "report.txt").write_text("report", encoding="utf-8")
    base = f"/api/sandboxes/{sandbox['id']}"
    roots = client.get(base + "/files").json()
    assert roots[0]["id"] == "workspace"
    assert client.get(base + "/files", params={"operation": "list"}).json()["entries"]
    assert client.get(base + "/files", params={"operation": "preview", "path": "report.txt"}).json()["text"] == "report"
    assert client.get(base + "/files", params={"operation": "download", "path": "report.txt"}).content == b"report"
    assert client.get(base + "/files", params={"operation": "preview", "path": "missing.txt"}).json()["state"] == "missing"
    assert client.get(base + "/files", params={"operation": "preview", "root": "cache", "path": "report.txt"}).status_code == 503
    assert client.get(base).json()["state"] == "ready"
    assert client.get(base + "/history").json() == []


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "a\\b", "a/../b", "a//b", "file:stream", "a.", "a "])
def test_paths_are_logical_relative_and_portable(tmp_path, path):
    with pytest.raises(SandboxValidationError):
        operate(tmp_path, path, "preview")


def test_file_bounds_readonly_and_explicit_overwrite(tmp_path):
    for index in range(DIRECTORY_LIMIT + 3):
        (tmp_path / str(index)).write_bytes(b"")
    listing = operate(tmp_path, "", "list")
    assert listing["truncated"] and len(listing["entries"]) == DIRECTORY_LIMIT
    (tmp_path / "large").write_bytes(b"x" * (PREVIEW_LIMIT + 1))
    assert operate(tmp_path, "large", "preview")["state"] == "oversized"
    (tmp_path / "binary").write_bytes(b"\0\xff")
    assert operate(tmp_path, "binary", "preview")["state"] == "unsupported"
    data = base64.b64encode(b"new").decode()
    with pytest.raises(SandboxSecurityError):
        operate(tmp_path, "copy", "write", read_only=True, data=data)
    operate(tmp_path, "copy", "write", data=data)
    with pytest.raises(FileExistsError):
        operate(tmp_path, "copy", "write", data=data)
    operate(tmp_path, "copy", "write", data=data, overwrite=True)
    assert (tmp_path / "copy").read_bytes() == b"new"


def test_links_and_junctions_cannot_escape(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "secret").write_text("private")
    link = workspace / "escape"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        assert operate(workspace, "", "list")["entries"][0]["blocked"]
        with pytest.raises((SandboxSecurityError, OSError)):
            operate(workspace, "escape/secret", "preview")
    finally:
        if os.name == "nt": link.rmdir()
        else: link.unlink()
    os.link(outside / "secret", workspace / "hardlink")
    with pytest.raises(SandboxSecurityError):
        operate(workspace, "hardlink", "preview")


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics")
def test_windows_pins_ancestors_against_rename_during_operation(tmp_path):
    folder = tmp_path / "folder"; folder.mkdir()
    source = folder / "file"; source.write_bytes(b"content")
    with pinned(source):
        with pytest.raises(OSError): folder.rename(tmp_path / "renamed")


@pytest.mark.asyncio
async def test_file_worker_does_not_recover_another_workers_live_cgroup(tmp_path, monkeypatch):
    from backend.sandbox.linux import LinuxSandboxBackend
    from backend.sandbox.models import SandboxState
    original = LinuxSandboxBackend(tmp_path)
    await original.create("busy")
    record = await original._record("busy")
    record.unit = "oaw-sandbox-" + "a" * 32 + ".scope"
    record.state = SandboxState.RUNNING
    original._save(record)
    reader = LinuxSandboxBackend(tmp_path)
    async def refuse_kill(unit): raise AssertionError("File browsing must not kill a cgroup")
    monkeypatch.setattr(reader, "kill_unit", refuse_kill)
    assert (await reader.file_operation("busy", "roots"))[0]["id"] == "workspace"
    assert (await reader._record("busy")).unit == record.unit


def test_skill_copy_uses_live_authorization_and_never_exposes_cache(runtime_client):
    client, backend, _ = runtime_client
    agent, sandbox, skill, _, edges = setup_skill(client)
    base = f"/api/sandboxes/{sandbox['id']}"
    status = client.get(base + f"/skills/{skill['id']}").json()
    assert status["status"] == "bundle available" and "templates/report.md" in status["files"]
    request = {"skill_id": skill["id"], "source": "templates/report.md", "destination": "report.md"}
    assert client.post(base + "/copy-skill-resource", json=request).status_code == 200
    assert client.post(base + "/copy-skill-resource", json=request).status_code == 422
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    arguments = {"sandbox": sandbox["id"], "skill": skill["id"], "source": "templates/report.md", "destination": "agent.md"}
    result = client.portal.call(provider.invoke_tool, agent["id"], "operation:copy_skill_resource", arguments)
    assert result["written"]
    client.delete(f"/api/edges/{edges[1]['id']}")
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent["id"], "operation:copy_skill_resource", arguments)
    assert client.post(base + "/reset-cache").status_code == 409
    client.post(base + "/stop")
    assert client.post(base + "/reset-cache").status_code == 200
    info = client.portal.call(backend.get, sandbox["id"])
    assert (info.workspace / "report.md").read_text() == "# Report"


def test_receipt_recovery_busy_owner_and_configuration_snapshot(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    local(client, sandbox, {"REGION": "before"})
    base = f"/api/sandboxes/{sandbox['id']}"
    services = client.app.state.services
    original = backend.execute
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set(); await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(backend, "execute", delayed)
        task = asyncio.create_task(services.execute_sandbox(sandbox["id"], ["cmd.exe"]))
        await started.wait()
        from backend.node_documents import read_document, write_document
        document = read_document(services, sandbox["id"])
        write_document(services, sandbox["id"], {"variables": {"REGION": "after"}}, document["revision"])
        from backend.sandbox.models import SandboxStateError
        with pytest.raises(SandboxStateError, match="busy: user owns"):
            await services.execute_sandbox(sandbox["id"], ["cmd.exe"])
        release.set(); await task
    client.portal.call(scenario)
    assert native.last_environment["REGION"] == "before"
    receipt = client.get(base + "/history").json()[-1]
    assert receipt["state"] == "finished" and receipt["caller"] == "user"
    from backend.sandbox.history import save
    receipt.update(state="running")
    save(services, sandbox["id"], receipt)
    assert client.get(base + "/history").json()[-1]["state"] == "interrupted"


def test_manual_disconnect_preserves_admitted_command(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    services = client.app.state.services
    original = backend.execute
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set(); await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(backend, "execute", delayed)
        task = asyncio.create_task(services.execute_sandbox(sandbox["id"], ["cmd.exe"], _keep_on_disconnect=True))
        await started.wait(); task.cancel(); await asyncio.sleep(0.02)
        assert services._sandbox_commands[sandbox["id"]]["state"] == "running"
        assert (await backend.get(sandbox["id"])).state.value == "ready"
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    client.portal.call(scenario)
    assert client.get(f"/api/sandboxes/{sandbox['id']}/history").json()[-1]["state"] == "finished"


def test_profile_sharing_and_configuration_authority(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client)
    profile = create_node(client, "environment", equipment={"owner_id": agent["id"], "relationship": "environment.use"})
    assert connect_profile(client, profile, sandbox).status_code == 422
    local(client, sandbox, {"TOKEN": {"secret_ref": "key"}})
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, agent["id"])
    assert all("bind" not in d.name and "configure" not in d.name for d in definitions)
    from backend.node_documents import DocumentActionRequest, invoke_document_action
    capability = client.app.state.services.capabilities.capability_for_id(agent["id"], f"sandbox.execute:{sandbox['id']}")
    async def attempt():
        with pytest.raises(PermissionDeniedError):
            await invoke_document_action(client.app.state.services, sandbox["id"], "replace",
                DocumentActionRequest(arguments={"variables": {}}, expected_revision=1), capability=capability)
    client.portal.call(attempt)


def test_sandbox_template_keeps_requirements_without_bindings_or_history(runtime_client):
    client, _, _ = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    local(client, sandbox, {"REGION": "local", "TOKEN": {"secret_ref": "key"}})
    doc = client.get(f"/api/nodes/{sandbox['id']}/document").json()
    assert client.put(f"/api/nodes/{sandbox['id']}/credentials/key", json={"value": "private-template-value", "expected_revision": doc["revision"]}).status_code == 200
    assert client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={"command": "echo hi"}).status_code == 200
    text = create_node(client, "text")
    captured = client.post("/api/legions", json={"name": "Sandbox defaults", "node_ids": [sandbox["id"], text["id"]]})
    assert captured.status_code == 201, captured.text
    assert "private-template-value" not in captured.text
    restored = client.post(f"/api/legions/{captured.json()['id']}/instances", json={"position": {"x": 0, "y": 0}})
    assert restored.status_code == 201, restored.text
    fresh = next(n for n in restored.json()["nodes"] if n["type"] == "sandbox")
    assert fresh["status"] == "stopped"
    assert client.get(f"/api/nodes/{fresh['id']}/credentials").json() == {"key": False}
    assert client.get(f"/api/sandboxes/{fresh['id']}/history").json() == []


def test_network_enforcement_preserves_other_boundaries(tmp_path):
    offline = bubblewrap_command(tmp_path, ResourceAccess.READ_ONLY, [], ["true"], {})
    assert "--share-net" not in offline
    for flag in ("--unshare-all", "--cap-drop", "--remount-ro", "--ro-bind"):
        assert flag in offline
    command = service_command(offline, "oaw-sandbox-" + "a" * 32 + ".scope", SandboxLimits(), 60)
    assert "--property=TasksMax=16" in command and "--property=MemoryMax=536870912" in command
    compile(_SERVICE_GUARD, "guard", "exec")


@pytest.mark.asyncio
async def test_enabled_policy_does_not_bypass_sandbox_identity(tmp_path, monkeypatch):
    from backend.sandbox.linux import LinuxSandboxBackend
    from backend.sandbox.wsl import WslSandboxBackend
    from backend.sandbox.models import SandboxNotFoundError
    async def missing(*args, **kwargs):
        raise SandboxNotFoundError("not-created")
    monkeypatch.setattr(WslSandboxBackend, "get", missing)
    for backend in (LinuxSandboxBackend(tmp_path / "linux"), WslSandboxBackend(tmp_path / "wsl", distribution="unused")):
        with pytest.raises(SandboxNotFoundError):
            await backend.execute("not-created", ["true"], execution_policy={"network_enabled": True})



@pytest.mark.parametrize("command", ["python", "ssh -tt server", "read answer", "set /p answer=", "pause", "vim report.txt"])
def test_explicit_interactive_requests_are_rejected(command):
    from backend.sandbox.commands import require_noninteractive
    with pytest.raises(SandboxValidationError, match="[Ii]nteractive"):
        require_noninteractive(command=command)


@pytest.mark.parametrize("exit_code,stdout,stderr,expected", [(0, "401", "", "authentication_failed"),
    (1, "", "execvp curl: No such file or directory", "missing_tool"), (7, "000", "connect failed", "connection_failed"),
    (6, "000", "Could not resolve host", "dns_failed"), (60, "000", "Certificate verification failed", "tls_verification_failed"),
    (0, "503", "", "http_error"), (0, "200", "", "connected")])
def test_connectivity_diagnostics_classify_results(runtime_client, monkeypatch, exit_code, stdout, stderr, expected):
    from dataclasses import replace
    from backend.sandbox.models import CommandResult
    from backend.services import ApplicationServices
    client, _, _ = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    services = client.app.state.services
    info = client.portal.call(services.get_sandbox, sandbox["id"])
    async def network_info(self, sandbox_id): return replace(info, network_enabled=True, platform="linux")
    async def execute(self, sandbox_id, argv=None, **kwargs):
        assert argv[0] == "curl" and argv[-1] == "https://example.com"
        return CommandResult(sandbox_id, tuple(argv), exit_code, stdout, stderr, 0.1)
    monkeypatch.setattr(ApplicationServices, "get_sandbox", network_info)
    monkeypatch.setattr(ApplicationServices, "execute_sandbox", execute)
    response = client.post(f"/api/sandboxes/{sandbox['id']}/diagnostics", json={"destination": "https://example.com"})
    assert response.status_code == 200 and response.json()["status"] == expected
    assert client.post(f"/api/sandboxes/{sandbox['id']}/diagnostics", json={"destination": "https://user:secret@example.com"}).status_code == 422


@pytest.mark.skipif(not os.environ.get("OAW_TEST_WSL_DISTRO"), reason="Select an installed WSL2 distribution")
@pytest.mark.asyncio
async def test_real_wsl_network_policy_blocks_control_plane_access(tmp_path):
    from backend.sandbox.wsl import WslSandboxBackend
    backend = WslSandboxBackend(tmp_path, distribution=os.environ["OAW_TEST_WSL_DISTRO"])
    await backend.create("network-check")
    await backend.start("network-check")
    try:
        script = "import socket; socket.socket(socket.AF_INET); print('IP available')"
        offline = await backend.execute("network-check", ["python3", "-c", script])
        assert offline.exit_code != 0
        enabled = await backend.execute("network-check", ["python3", "-c", script], execution_policy={"network_enabled": True})
        assert enabled.exit_code == 0, enabled
        unix = await backend.execute("network-check", ["python3", "-c", "import socket; socket.socket(socket.AF_UNIX)"])
        assert unix.exit_code != 0
        isolated = await backend.execute("network-check", ["python3", "-c", "from pathlib import Path; assert not Path('/run/WSL').exists(); assert not Path('/mnt/c').exists(); print('isolated')"])
        assert isolated.exit_code == 0, isolated
    finally:
        await backend.destroy("network-check")


@pytest.mark.skipif(not os.environ.get("OAW_TEST_SANDBOX_RUNTIME"), reason="Select a real Windows/Linux/WSL runtime explicitly")
def test_real_local_workspace_scenario(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    runtime = os.environ["OAW_TEST_SANDBOX_RUNTIME"]
    folder = tmp_path / "external project"; folder.mkdir()
    settings = replace(Settings.for_data_root(tmp_path / "application"), sandbox_runtime=runtime, agent_runtime="core.mock")
    with TestClient(create_app(settings)) as client:
        agent = create_node(client, "agent")
        sandbox = create_node(client, "sandbox", config={"runtime": runtime, "workspace_path": str(folder)})
        profile = create_node(client, "environment")
        local(client, profile, {"REGION": "base", "BASE": "shared"})
        assert connect_profile(client, profile, sandbox).status_code == 201
        local(client, sandbox, {"REGION": "local"})
        skill = create_node(client, "oaw.skills.skill")
        windows = runtime == "windows"
        script = "scripts/report.cmd" if windows else "scripts/report.sh"
        content = "@echo off\necho %REGION%-%BASE%>skill-result.txt\n" if windows else 'printf "%s-%s\\n" "$REGION" "$BASE" > skill-result.txt\n'
        edit(client, skill, "replace", {"files": {script: content}})
        for target, relationship in [(sandbox, "execute"), (skill, "oaw.skills.skill.use")]:
            assert client.post("/api/edges", json={"source": agent["id"], "target": target["id"], "relationship": relationship}).status_code == 201
        base = f"/api/sandboxes/{sandbox['id']}"
        assert client.get(base + "/files").json()[0]["id"] == "workspace"
        assert client.post(base + "/start").status_code == 200
        checked = client.post(base + "/diagnostics", json={})
        assert checked.status_code == 200, checked.text
        assert checked.json()["configuration"]["ready"]
        manual = 'echo %REGION%-%BASE%>manual-result.txt' if windows else 'printf "%s-%s\\n" "$REGION" "$BASE" > manual-result.txt'
        result = client.post(base + "/execute", json={"command": manual})
        assert result.status_code == 200 and result.json()["exit_code"] == 0, result.text
        result = run(client, agent, sandbox, skill, script_path=script, interpreter=["cmd.exe", "/d", "/c", "call"] if windows else ["/bin/sh"])
        assert result["exit_code"] == 0, result
        listing = client.get(base + "/files", params={"operation": "list"}).json()
        if "entries" not in listing:
            operate(folder, "", "list")  # Preserve the native failure for diagnosis.
        assert {"skill-result.txt", "manual-result.txt"} <= {e["name"] for e in listing["entries"]}
        for path in ("skill-result.txt", "manual-result.txt"):
            assert client.get(base + "/files", params={"operation": "preview", "path": path}).json()["text"].strip() == "local-shared"
            assert client.get(base + "/files", params={"operation": "download", "path": path}).content.strip() == b"local-shared"
        history = client.get(base + "/history").json()
        assert [h["caller"] for h in history] == ["user", "user", agent["id"]]
        # These are the reads performed by reopening a window: no lifecycle call.
        assert client.get(base).json()["state"] == "ready"
        assert client.get(base + "/history").json() == history
        services = client.app.state.services
        async def cancel_current():
            argv = ["cmd.exe", "/d", "/c", "for /l %i in (1,1,1000000000) do @rem waiting"] if windows else ["/bin/sleep", "30"]
            task = asyncio.create_task(services.execute_sandbox(sandbox["id"], argv))
            for _ in range(300):
                if (await services.get_sandbox(sandbox["id"])).state.value == "running":
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.5)
            assert (await services.sandbox_backend.file_operation(sandbox["id"], "list"))["entries"]
            assert not task.done(), "Browsing must not reclaim or stop a live command"
            await services.sandbox_backend.cancel(sandbox["id"])
            result = await task
            assert result.cancelled
            assert (await services.get_sandbox(sandbox["id"])).state.value == "ready"
        client.portal.call(cancel_current)
        assert client.post(base + "/stop").status_code == 200
        assert client.post(base + "/reset-cache").status_code == 200
        assert (folder / "skill-result.txt").read_text().strip() == "local-shared"
