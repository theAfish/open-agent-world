"""Policy routing and opt-in real manual/Agent/Skill networking acceptance.

OAW_TEST_NETWORK_RUNTIME=windows|linux|wsl:<installed distro> selects a real
runtime. HTTPS uses example.com unless OAW_TEST_HTTPS_URL names a controlled
public endpoint. OS-specific tests separately exercise raw non-HTTP TCP.
"""
import asyncio
import json
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.sandbox.manager import SandboxManager
from backend.sandbox.models import SandboxNetworkError, SandboxSecurityError, SandboxStateError, SandboxValidationError
from backend.sandbox.registry import SandboxRuntime, SandboxRuntimeRegistration, SandboxRuntimeRegistry
from backend.tests.test_sandbox_manager import RecordingBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("provisioned", [False, True])
@pytest.mark.parametrize("failure", ["false", "network_error", "os_error", "unexpected", "timeout"])
async def test_failed_start_refreshes_network_status_and_recovers(tmp_path, monkeypatch, provisioned, failure):
    backend = RecordingBackend(tmp_path / "runtime")
    registry = SandboxRuntimeRegistry()
    failing = False

    async def probe():
        return True, None

    async def network_probe():
        if not failing:
            return True, None
        if failure == "false":
            return False, "broker unavailable"
        if failure == "timeout":
            await asyncio.Event().wait()
        errors = {"network_error": SandboxNetworkError, "os_error": OSError, "unexpected": RuntimeError}
        raise errors[failure]("broker unavailable")

    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("isolated", "Isolated", "windows", ("cmd.exe",),
            supported_network_modes=("disabled", "enabled"), network_reason="Public IPv4 only"),
        lambda: backend, probe, network_probe=network_probe))
    manager = SandboxManager(tmp_path / "data", registry)
    await manager.create("lab")
    await manager.configure_options("lab", {"network_enabled": True})
    if provisioned:
        await manager.start("lab")
        await manager.terminate("lab")
    assert (await registry.catalog())[0].network_available
    failing = True
    wait_for = asyncio.wait_for

    async def short_wait(awaitable, timeout):
        return await wait_for(awaitable, 0.01 if timeout == 20 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", short_wait)
    with pytest.raises(SandboxSecurityError):
        await manager.start("lab")
    info = await manager.get("lab")
    assert info.network_enabled is True
    assert info.network_available is False
    assert info.network_status in {"missing_component", "setup_failed"}
    assert ("timed out" if failure == "timeout" else "broker unavailable") in info.network_reason
    assert info.state.value == "stopped"
    assert json.loads(manager._manifest("lab").read_text())["policy"]["network_enabled"] is True
    runtime = (await registry.catalog())[0]
    assert not runtime.network_available and runtime.network_reason == info.network_reason
    failing = False
    recovered = await manager.start("lab")
    assert recovered.network_enabled and recovered.network_available
    assert recovered.network_status == "enabled" and recovered.network_reason == "Public IPv4 only"
    assert (await manager.get("lab")).network_reason == "Public IPv4 only"


@pytest.mark.asyncio
async def test_network_prerequisite_failure_preserves_offline_runtime(tmp_path):
    backend = RecordingBackend(tmp_path)
    registry = SandboxRuntimeRegistry()
    installed = False

    async def offline_probe():
        return True, None

    async def network_probe():
        return installed, None if installed else "Install slirp4netns and refresh runtime discovery"

    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("isolated", "Isolated", "linux", ("sh", "-c"),
            supported_network_modes=("disabled", "enabled"), network_reason="Public outbound only"),
        lambda: backend, offline_probe, network_probe=network_probe))
    manager = SandboxManager(tmp_path / "data", registry)
    await manager.create("lab")
    runtime = (await registry.catalog())[0]
    assert runtime.available and not runtime.network_available
    assert runtime.network_status == "missing_component"
    assert runtime.supported_network_modes == ("disabled", "enabled")
    with pytest.raises(SandboxValidationError, match="Install slirp4netns"):
        await manager.configure_options("lab", {"network_enabled": True})
    assert not (await manager.start("lab")).network_enabled
    await manager.terminate("lab")
    installed = True
    await registry.catalog(refresh=True)
    await manager.configure_options("lab", {"network_enabled": True})
    assert (await manager.start("lab")).network_status == "enabled"
    with pytest.raises(SandboxStateError, match="Stop"):
        await manager.configure_options("lab", {"network_enabled": False})
    await manager.terminate("lab")
    installed = False  # A cached discovery success cannot bypass start checks.
    with pytest.raises(SandboxSecurityError, match="Install slirp4netns"):
        await manager.start("lab")
    assert (await manager.get("lab")).runtime_id == "isolated"
    await manager.configure_options("lab", {"network_enabled": False})
    assert not (await manager.start("lab")).network_enabled


@pytest.mark.asyncio
async def test_setup_failure_is_reported_without_changing_saved_policy(tmp_path):
    class NetworkBackend(RecordingBackend):
        supports_execution_policy = True
        fail = True

        async def execute(self, sandbox_id, argv, *, execution_policy, **kwargs):
            assert execution_policy["network_enabled"] is True
            if self.fail:
                raise SandboxNetworkError("Networking setup failed: helper exited before readiness")
            return await super().execute(sandbox_id, argv, **kwargs)

    backend = NetworkBackend(tmp_path)
    registry = SandboxRuntimeRegistry()
    setup_fails = False

    async def probe():
        if setup_fails:
            raise SandboxNetworkError("Networking setup failed during start")
        return True, None

    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("isolated", "Isolated", "linux", ("sh", "-c"),
            supported_network_modes=("disabled", "enabled")),
        lambda: backend, probe, network_probe=probe))
    manager = SandboxManager(tmp_path / "data", registry)
    await manager.create("lab")
    await manager.configure_options("lab", {"network_enabled": True})
    await manager.start("lab")
    with pytest.raises(SandboxNetworkError):
        await manager.execute("lab", ["true"])
    info = await manager.get("lab")
    assert info.network_enabled and info.network_status == "setup_failed"
    assert "helper exited" in info.network_reason
    backend.fail = False
    assert (await manager.execute("lab", ["true"])).exit_code == 0
    assert (await manager.get("lab")).network_status == "enabled"
    await manager.terminate("lab")
    setup_fails = True
    with pytest.raises(SandboxNetworkError, match="during start"):
        await manager.start("lab")
    assert (await manager.get("lab")).network_status == "setup_failed"
    setup_fails = False
    assert (await manager.start("lab")).network_status == "enabled"


@pytest.mark.asyncio
async def test_discovery_distinguishes_unimplemented_from_failed_setup(tmp_path):
    registry = SandboxRuntimeRegistry()

    async def offline_probe():
        return True, None

    async def setup_probe():
        raise SandboxNetworkError("Networking setup failed: private namespace refused")

    backend = RecordingBackend(tmp_path)
    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("offline", "Offline plugin", "test", ()), lambda: backend, offline_probe))
    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("network", "Network plugin", "test", (), supported_network_modes=("disabled", "enabled")),
        lambda: backend, offline_probe, network_probe=setup_probe))
    offline, network = await registry.catalog()
    assert offline.available and network.available
    assert offline.network_status == "not_implemented"
    assert network.network_status == "setup_failed"
    assert "private namespace refused" in network.network_reason


@pytest.mark.skipif(not os.environ.get("OAW_TEST_NETWORK_RUNTIME"), reason="Select a real isolated networking runtime explicitly")
def test_real_saved_policy_routes_manual_agent_skill_and_diagnostics(tmp_path):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.tests.conftest import create_node
    from backend.tests.test_skill_packages import edit
    from backend.tests.test_skill_runtime import run

    runtime = os.environ["OAW_TEST_NETWORK_RUNTIME"]
    url = os.environ.get("OAW_TEST_HTTPS_URL", "https://example.com")
    settings = replace(Settings.for_data_root(tmp_path / "app"), sandbox_runtime=runtime, agent_runtime="core.mock")
    with TestClient(create_app(settings), client=("127.0.0.1", 51000)) as client:
        agent = create_node(client, "agent")
        sandbox = create_node(client, "sandbox", config={"runtime": runtime})
        skill = create_node(client, "oaw.skills.skill")
        windows = runtime == "windows"
        script = "scripts/network.cmd" if windows else "scripts/network.sh"
        content = ('@echo off\ncurl.exe --disable --noproxy "*" --fail --silent --show-error --max-time 12 "%~1"\nexit /b %errorlevel%\n'
            if windows else 'exec curl --disable --noproxy "*" --fail --silent --show-error --max-time 12 -- "$1"\n')
        edit(client, skill, "replace", {"files": {script: content}})
        for target, relationship in [(sandbox, "execute"), (skill, "oaw.skills.skill.use")]:
            assert client.post("/api/edges", json={"source": agent["id"], "target": target["id"], "relationship": relationship}).status_code == 201
        base = f"/api/sandboxes/{sandbox['id']}"
        argv = ["curl.exe" if windows else "curl", "--disable", "--noproxy", "*", "--fail", "--silent", "--show-error", "--max-time", "12", "--", url]
        assert client.post(base + "/start").status_code == 200
        offline = client.post(base + "/execute", json={"argv": argv})
        assert offline.status_code == 200 and offline.json()["exit_code"] != 0, offline.text
        assert client.post(base + "/stop").status_code == 200
        saved = client.patch(f"/api/nodes/{sandbox['id']}", json={"config": {"network_enabled": True}})
        assert saved.status_code == 200, saved.text
        started = client.post(base + "/start")
        assert started.status_code == 200, started.text
        assert started.json()["network_enabled"] and started.json()["runtime_id"] == runtime
        manual = client.post(base + "/execute", json={"argv": argv})
        assert manual.status_code == 200 and manual.json()["exit_code"] == 0, manual.text
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        invoked = client.portal.call(provider.invoke_tool, agent["id"], f"sandbox.execute:{sandbox['id']}", {"argv": argv})
        assert invoked["exit_code"] == 0, invoked
        skill_result = run(client, agent, sandbox, skill, script_path=script,
            interpreter=["cmd.exe", "/d", "/c", "call"] if windows else ["/bin/sh"], argv=[url])
        assert skill_result["exit_code"] == 0, skill_result
        diagnostic = client.post(base + "/diagnostics", json={"destination": url})
        assert diagnostic.status_code == 200 and diagnostic.json()["status"] == "connected", diagnostic.text
        assert client.patch(f"/api/nodes/{sandbox['id']}", json={"config": {"network_enabled": False}}).status_code == 409

        async def cancel_enabled():
            services = client.app.state.services
            command = ["cmd.exe", "/d", "/c", "for /l %i in (1,1,1000000000) do @rem waiting"] if windows else ["/bin/sleep", "30"]
            pending = asyncio.create_task(services.execute_sandbox(sandbox["id"], command))
            for _ in range(500):
                if (await services.get_sandbox(sandbox["id"])).state.value == "running":
                    break
                await asyncio.sleep(0.02)
            await services.sandbox_backend.cancel(sandbox["id"])
            assert (await pending).cancelled
            assert (await services.get_sandbox(sandbox["id"])).state.value == "ready"
        client.portal.call(cancel_enabled)
        assert client.post(base + "/stop").status_code == 200
        assert client.patch(f"/api/nodes/{sandbox['id']}", json={"config": {"network_enabled": False}}).status_code == 200
        assert client.post(base + "/start").status_code == 200
        offline_again = client.post(base + "/execute", json={"argv": argv})
        assert offline_again.status_code == 200 and offline_again.json()["exit_code"] != 0, offline_again.text
        denied_agent = client.portal.call(provider.invoke_tool, agent["id"], f"sandbox.execute:{sandbox['id']}", {"argv": argv})
        assert denied_agent["exit_code"] != 0, denied_agent
        denied_skill = run(client, agent, sandbox, skill, script_path=script,
            interpreter=["cmd.exe", "/d", "/c", "call"] if windows else ["/bin/sh"], argv=[url])
        assert denied_skill["exit_code"] != 0, denied_skill
        assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200
