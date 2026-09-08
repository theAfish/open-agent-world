"""Real existing-process broker-loss acceptance; requires approved test control.

The audit utility remains independent and read-only throughout broker loss.
"""
import asyncio
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest

from backend.sandbox.manager import SandboxManager
from backend.sandbox.models import SandboxSecurityError, SandboxNetworkError
from backend.sandbox.registry import SandboxRuntime, SandboxRuntimeRegistration, SandboxRuntimeRegistry
from backend.sandbox.windows import WindowsSandboxBackend
from backend.sandbox.win32 import WindowsNativeApi
from backend.sandbox.windows_network_broker import _PipeSecurity, probe_network_broker
from backend.tests.windows_network_acceptance import (
    WfpAudit, acceptance_root, assert_unprivileged, broker_control, evidence,
    host_get, private_url, profile_sid,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.name != "nt" or os.environ.get("OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS") != "1",
    reason="Explicitly opt in to real Windows acceptance with approved broker control")]


async def wait_json(path, workload):
    for _ in range(300):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                pass
        if workload.done():
            raise AssertionError(f"Native workload exited before checkpoint: {await workload}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"Native workload did not reach {path.name}")


@pytest.mark.parametrize("mode", ["graceful", "abnormal"])
async def test_existing_appcontainer_keeps_denials_after_real_broker_loss(mode):
    assert_unprivileged()
    assert os.environ.get("OAW_TEST_BROKER_SESSION"), "Start the approved broker test controller first"
    url = private_url()
    peer = urlsplit(url)
    assert host_get(url + "?loss-host-before=" + mode)
    backend = WindowsSandboxBackend(acceptance_root())
    registry = SandboxRuntimeRegistry()

    async def available():
        await asyncio.to_thread(WindowsNativeApi)
        return True, None

    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("windows", "Windows", "windows", ("cmd.exe",),
            supported_network_modes=("disabled", "enabled"), network_reason="Public IPv4 only"),
        lambda: backend, available, network_probe=WindowsSandboxBackend.probe_network))
    manager = SandboxManager(acceptance_root(), registry, preferred="windows")
    identity, admission = "loss-" + mode, "admission-" + mode
    host_requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host_requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"HOST_CONTROL")
        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert host_get(f"http://127.0.0.1:{server.server_port}/before") == b"HOST_CONTROL"
    workload = None
    audits = []
    created = []
    try:
        for name in (identity, admission):
            await manager.create(name)
            created.append(name)
            await manager.configure_options(name, {"runtime": "windows", "network_enabled": True, "command_timeout": 120})
            await manager.start(name)
            record = backend._records[name]
            audit = WfpAudit(record.identity, profile_sid(record))
            audit.absent("loss-before-admission-" + name)
            audits.append(audit)
        record = backend._records[identity]
        # A native Winsock process performs both phases without spawning a
        # child or weakening AppContainer process mitigations.
        source = Path(os.environ["OAW_TEST_NETWORK_PROBE_EXE"]).resolve(strict=True)
        executable = record.workspace / "probe.exe"
        executable.write_bytes(source.read_bytes())
        workload = asyncio.create_task(manager.execute(identity,
            [str(executable), peer.hostname, str(peer.port or 80), str(server.server_port)],
            timeout_seconds=120))
        before = await wait_json(record.workspace / "before.json", workload)
        security = _PipeSecurity()
        process = security.kernel.OpenProcess(0x1000, False, before["pid"])
        security.check(process, "Inspect test workload token")
        try:
            owner, elevated, container = security.process_identity(process)
            assert owner == security.owner and not elevated and container
        finally:
            security.kernel.CloseHandle(process)
        before_rules = audits[0].installed("before-broker-" + mode)
        stopped = await asyncio.to_thread(broker_control, mode)
        assert stopped["exit_code"] == (0 if mode == "graceful" else 1), stopped
        assert probe_network_broker()[0] is False
        persisted = audits[0].installed("broker-absent-" + mode)
        assert [(r["key"], r["filter_id"]) for r in persisted] == [(r["key"], r["filter_id"]) for r in before_rules]
        (record.workspace / "after-loss").write_text("go")
        after = await wait_json(record.workspace / "after.json", workload)
        assert after["pid"] == before["pid"] and not workload.done()
        for phase in (before, after):
            for target in ("private", "host"):
                assert not phase[target]["connected"] and phase[target]["error"] in {10013, 10060}, phase
        assert host_requests == ["/before"], host_requests
        assert host_get(url + "?loss-host-after=" + mode)
        assert host_get(f"http://127.0.0.1:{server.server_port}/after") == b"HOST_CONTROL"
        assert len(audits[0].installed("after-fresh-denied-connections-" + mode)) == len(persisted)
        with pytest.raises(SandboxSecurityError, match="broker"):
            await manager.start(admission)
        info = await manager.get(admission)
        assert info.network_enabled and not info.network_available
        assert info.network_status != "enabled" and "broker" in info.network_reason
        with pytest.raises(SandboxNetworkError, match="broker"):
            await manager.execute(admission, ["cmd.exe", "/d", "/c", "echo forbidden>unexpected.txt"])
        assert not (backend._records[admission].workspace / "unexpected.txt").exists()
        assert backend._records[admission].active_job is None
        failed_execution = await manager.get(admission)
        assert failed_execution.network_enabled and not failed_execution.network_available
        assert failed_execution.state.value == "error" and "broker" in failed_execution.network_reason
        audits[1].absent("failed-new-admission-" + mode)
        saved = json.loads(manager._manifest(admission).read_text())
        assert saved["policy"]["network_enabled"] is True
        evidence("loss", {"mode": mode, "before": before, "after": after,
            "profile": record.identity, "workload_elevated": elevated, "workload_appcontainer": container,
            "stopped_broker": stopped, "failed_status": info.network_status, "failure_reason": info.network_reason})
        await asyncio.to_thread(broker_control, "start")
        recovered = await manager.start(admission)
        assert recovered.network_enabled and recovered.network_available and recovered.network_status == "enabled"
        assert recovered.network_reason == "Public IPv4 only"
        result = await manager.execute(admission, ["curl.exe", "--disable", "--noproxy", "*", "--fail",
            "--silent", "--show-error", "--max-time", "12", "https://example.com"])
        assert result.exit_code == 0 and "Example Domain" in result.stdout, result
        audits[1].installed("recovered-enabled-" + mode)
        (record.workspace / "finish").write_text("done")
        assert (await asyncio.wait_for(workload, timeout=10)).exit_code == 0
        assert record.active_job is None
        audits[0].installed("workload-ended-before-destroy-" + mode)
    finally:
        await asyncio.to_thread(broker_control, "start")
        try:
            for name in reversed(created):
                await manager.destroy(name)
            if workload is not None:
                await asyncio.wait_for(workload, timeout=10)
            for audit in audits:
                audit.absent("loss-final-cleanup-" + mode)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
