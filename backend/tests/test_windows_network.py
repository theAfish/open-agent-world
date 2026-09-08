"""Windows network policy and explicitly opted-in native acceptance.

Set OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS=1 for native checks. Public
acceptance uses example.com HTTPS and GitHub's SSH listener at port 443, without
authenticating. OAW_TEST_HTTPS_URL can select a controlled verified HTTPS
endpoint. The host-denial tests use controlled, positively checked listeners.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import socket
import threading
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.sandbox.models import SandboxNetworkError, SandboxSecurityError, SandboxState
from backend.sandbox.materialization import RuntimeBundle, RuntimeMount
from backend.sandbox.win32 import AppContainerProfile, WindowsNativeApi
from backend.sandbox.windows import WindowsSandboxBackend
from backend.sandbox import windows_network
from backend.sandbox import win32, windows
from backend.sandbox import windows_network_broker
from backend.sandbox import windows_wfp
from backend.tests.test_sandbox_runtime import FakeWindowsNativeApi
from backend.tests.windows_network_acceptance import (
    WfpAudit, acceptance_root, profile_sid, private_url, host_get, evidence,
)


@pytest.mark.asyncio
async def test_windows_forwards_only_effective_network_policy(tmp_path, monkeypatch):
    release = Mock()
    monkeypatch.setattr(windows, "release_public_egress", release)
    native = FakeWindowsNativeApi()
    backend = WindowsSandboxBackend(tmp_path, native_api=native)
    await backend.create("lab")
    await backend.start("lab")
    try:
        for enabled in (False, True, False):
            await backend.execute("lab", ["cmd.exe", "/c", "echo policy"],
                                  execution_policy={"network_enabled": enabled})
            assert native.last_network_enabled is enabled
    finally:
        await backend.destroy("lab")
    release.assert_called_once()


@pytest.mark.asyncio
async def test_windows_missing_network_prerequisite_does_not_disable_offline(tmp_path, monkeypatch):
    isolation = Mock()
    isolation.probe.side_effect = SandboxSecurityError("Windows networking requires the BFE service")
    monkeypatch.setattr(windows, "WindowsNetworkIsolation", Mock(return_value=isolation))
    available, reason = await WindowsSandboxBackend.probe_network()
    assert not available and "BFE" in reason
    native = FakeWindowsNativeApi()
    backend = WindowsSandboxBackend(tmp_path, native_api=native)
    await backend.create("offline")
    await backend.start("offline")
    try:
        assert (await backend.execute("offline", ["cmd.exe", "/c", "echo offline"])).exit_code == 0
        info = await backend.get("offline")
        assert info.supported_network_modes == ("disabled", "enabled")
        assert not info.network_available and info.network_status == "missing_component"
    finally:
        await backend.destroy("offline")


def test_windows_setup_failure_never_creates_a_workload(tmp_path, monkeypatch):
    isolation = Mock()
    isolation.require_no_loopback_exemption.side_effect = SandboxSecurityError("existing loopback exemption")
    monkeypatch.setattr(win32, "WindowsNetworkIsolation", Mock(return_value=isolation))
    native = WindowsNativeApi.__new__(WindowsNativeApi)
    native._resolve_executable = Mock(side_effect=AssertionError("workload admitted before network validation"))
    with pytest.raises(SandboxNetworkError, match="loopback exemption"):
        native.run_appcontainer(AppContainerProfile("test", 42), ["cmd.exe"],
            cwd=tmp_path, environment={}, limits=None, timeout_seconds=1,
            cancel_event=threading.Event(), on_stdout=lambda _: None,
            on_stderr=lambda _: None, on_job_open=lambda _: None,
            on_job_close=lambda: None, network_enabled=True)
    native._resolve_executable.assert_not_called()
    isolation.internet_client_sid.assert_not_called()


def test_windows_cancellation_during_network_setup_never_admits_workload(tmp_path, monkeypatch):
    cancellation = threading.Event()
    isolation = Mock()
    isolation.internet_client_sid.return_value = ctypes.create_string_buffer(68)
    monkeypatch.setattr(win32, "WindowsNetworkIsolation", Mock(return_value=isolation))
    monkeypatch.setattr(win32, "ensure_public_egress", lambda _: cancellation.set())
    native = WindowsNativeApi.__new__(WindowsNativeApi)
    native._resolve_executable = Mock(side_effect=AssertionError("cancelled workload admitted"))
    result = native.run_appcontainer(AppContainerProfile("test", 42), ["cmd.exe"],
        cwd=tmp_path, environment={}, limits=None, timeout_seconds=1,
        cancel_event=cancellation, on_stdout=lambda _: None, on_stderr=lambda _: None,
        on_job_open=lambda _: None, on_job_close=lambda: None, network_enabled=True)
    assert result.cancelled
    native._resolve_executable.assert_not_called()


def test_windows_exemption_check_frees_every_native_allocation():
    isolation = windows_network.WindowsNetworkIsolation.__new__(windows_network.WindowsNetworkIsolation)
    isolation.adv = Mock()
    isolation.adv.EqualSid.side_effect = lambda left, right: left == right
    isolation.kernel = Mock()
    isolation.firewall = Mock()
    entries = (windows_network.SidAndAttributes * 2)(
        windows_network.SidAndAttributes(11, 0), windows_network.SidAndAttributes(42, 0))

    def query(count_pointer, entries_pointer):
        ctypes.cast(count_pointer, ctypes.POINTER(ctypes.wintypes.DWORD))[0] = 2
        ctypes.cast(entries_pointer, ctypes.POINTER(ctypes.POINTER(windows_network.SidAndAttributes)))[0] = entries
        return 0

    isolation.firewall.NetworkIsolationGetAppContainerConfig.side_effect = query
    with pytest.raises(SandboxSecurityError, match="loopback exemption"):
        isolation.require_no_loopback_exemption(42)
    assert isolation.kernel.HeapFree.call_count == 3


native_windows = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS") != "1",
    reason="set OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS=1 to exercise real AppContainer networking",
)


def _curl(url: str, seconds: int = 12):
    return ["curl.exe", "--disable", "--noproxy", "*", "--silent", "--show-error",
            "--fail", "--max-time", str(seconds), url]


@native_windows
@pytest.mark.asyncio
async def test_windows_real_dns_verified_https_non_http_tcp_and_offline_return(tmp_path):
    backend = WindowsSandboxBackend(tmp_path)
    assert await backend.probe_network() == (True, None)
    await backend.create("public-egress")
    try:
        record = backend._records["public-egress"]
        lower_sid = ctypes.c_void_p()
        result = backend._native._userenv.DeriveAppContainerSidFromAppContainerName(
            record.profile.name.lower(), ctypes.byref(lower_sid))
        assert result == 0 and lower_sid
        try:
            assert windows_network.WindowsNetworkIsolation().adv.EqualSid(record.profile.sid, lower_sid)
        finally:
            backend._native._advapi32.FreeSid(lower_sid)
        await backend.start("public-egress")
        url = os.environ.get("OAW_TEST_HTTPS_URL", "https://example.com")
        assert url.startswith("https://"), "positive acceptance requires verified HTTPS"
        offline = await backend.execute("public-egress", _curl(url, 3))
        assert offline.exit_code in {6, 7, 28}, offline
        online = await backend.execute("public-egress", _curl(url), execution_policy={"network_enabled": True})
        assert online.exit_code == 0 and online.stdout, online
        # A client identification line provokes the server's binary key exchange
        # packet, proving bidirectional non-HTTP TCP traffic. curl's own bounded
        # wait then returns 28 because this probe intentionally does not log in.
        ssh = await backend.execute("public-egress", ["cmd.exe", "/d", "/s", "/c",
            'echo SSH-2.0-OAW-Network-Test | curl.exe --disable --noproxy "*" --silent '
            '--show-error --max-time 3 telnet://ssh.github.com:443'],
            execution_policy={"network_enabled": True}, timeout_seconds=8)
        assert ssh.exit_code in {0, 28} and ssh.stdout.startswith("SSH-2.0-"), ssh
        assert "curve25519" in ssh.stdout, "SSH server did not exchange its protocol key offer"
        await backend.terminate("public-egress")
        await backend.start("public-egress")
        offline_again = await backend.execute("public-egress", _curl(url, 3))
        assert offline_again.exit_code in {6, 7, 28}, offline_again
    finally:
        await backend.destroy("public-egress")


@native_windows
@pytest.mark.asyncio
async def test_windows_real_host_addresses_ipv6_and_gateway_adapter_are_isolated(tmp_path):
    backend = WindowsSandboxBackend(tmp_path)
    await backend.create("host-denial")
    listener = socket.socket(socket.AF_INET6)
    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    listener.bind(("::", 0))
    listener.listen()
    listener.settimeout(0.1)
    port = listener.getsockname()[1]
    accepted = []
    stopping = threading.Event()

    def serve():
        while not stopping.is_set():
            try:
                client, address = listener.accept()
            except socket.timeout:
                continue
            with client:
                accepted.append(address)
                client.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 6\r\n\r\nHOSTOK")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        await backend.start("host-denial")
        addresses = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
            address = sockaddr[0]
            if family == socket.AF_INET6 and sockaddr[3] and "%" not in address:
                address += "%" + str(sockaddr[3])
            addresses.add(address)
        # Include any gateway alias or additional host IP assigned by the test
        # environment. Every address must pass a host positive control first.
        addresses.update(filter(None, os.environ.get("OAW_TEST_HOST_ADDRESSES", "").split(",")))
        assert any(":" not in item and item != "127.0.0.1" for item in addresses)
        for address in sorted(addresses):
            family, socktype, proto, _, sockaddr = socket.getaddrinfo(address, port, 0, socket.SOCK_STREAM)[0]
            with socket.socket(family, socktype, proto) as host:
                if family == socket.AF_INET6:
                    host.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                host.settimeout(2)
                host.connect(sockaddr)
                assert b"HOSTOK" in host.recv(512), address
            count = len(accepted)
            authority = "[" + address.replace("%", "%25") + "]" if ":" in address else address
            response = await backend.execute("host-denial", _curl(f"http://{authority}:{port}/", 2),
                                             execution_policy={"network_enabled": True}, timeout_seconds=5)
            assert response.exit_code in {7, 28}, (address, response)
            assert "HOSTOK" not in response.stdout and len(accepted) == count, address
    finally:
        stopping.set()
        thread.join(timeout=2)
        listener.close()
        await backend.destroy("host-denial")


@native_windows
@pytest.mark.asyncio
async def test_windows_real_enabled_cancellation_keeps_job_cleanup(tmp_path):
    backend = WindowsSandboxBackend(tmp_path)
    await backend.create("cancel-egress")
    try:
        await backend.start("cancel-egress")
        task = asyncio.create_task(backend.execute("cancel-egress",
            _curl("telnet://ssh.github.com:443", 120),
            execution_policy={"network_enabled": True}, timeout_seconds=120))
        for _ in range(100):
            if backend._records["cancel-egress"].active_job is not None:
                break
            await asyncio.sleep(0.02)
        assert backend._records["cancel-egress"].active_job is not None
        await backend.cancel("cancel-egress")
        result = await asyncio.wait_for(task, timeout=10)
        assert result.cancelled
        assert backend._records["cancel-egress"].active_job is None
        assert (await backend.get("cancel-egress")).state == SandboxState.READY
        followup = await backend.execute("cancel-egress", ["cmd.exe", "/d", "/c", "echo cleaned"])
        assert followup.exit_code == 0 and "cleaned" in followup.stdout
    finally:
        await backend.destroy("cancel-egress")


@native_windows
@pytest.mark.asyncio
async def test_windows_real_enabled_skill_acl_credentials_and_process_limit(tmp_path, monkeypatch):
    backend = WindowsSandboxBackend(tmp_path / "managed")
    secret = tmp_path / "host-secret.txt"
    secret.write_text("host-secret-must-stay-private", encoding="utf-8")
    monkeypatch.setenv("OAW_PRIVATE_SECRET", "host-private-value")
    info = await backend.create("skill-egress")
    script = (
        # COPY sets ERRORLEVEL on access denial; ECHO redirection in a batch
        # file can leave the previous ERRORLEVEL unchanged despite denial.
        '@echo off\r\necho tampered>mutation.txt\r\n'
        'copy /y mutation.txt "%~f0" >nul 2>&1\r\n'
        'if not errorlevel 1 exit /b 90\r\n'
        'if defined OAW_PRIVATE_SECRET exit /b 91\r\n'
        'curl.exe --disable --noproxy "*" --fail --silent --show-error --max-time 12 '
        'https://example.com\r\n'
    ).encode("ascii")
    bundle = RuntimeBundle("skills/network-acceptance", (("network.cmd", script),))
    try:
        await backend.start("skill-egress")
        denied = await backend.execute("skill-egress", ["cmd.exe", "/d", "/c", "type", str(secret)],
                                       execution_policy={"network_enabled": True})
        assert denied.exit_code != 0 and "host-secret-must-stay-private" not in denied.stdout
        result = await backend.execute("skill-egress", ["cmd.exe", "/d", "/c", "call", "network.cmd"],
            runtime_mount=RuntimeMount(bundle, 4), execution_policy={"network_enabled": True})
        assert result.exit_code == 0 and "Example Domain" in result.stdout, result
        assert (info.workspace.parent / ".oaw" / bundle.key / "network.cmd").read_bytes() == script
        limited = await backend.execute("skill-egress", ["cmd.exe", "/d", "/c", "curl.exe --version"],
            execution_policy={"network_enabled": True, "active_process_limit": 1}, timeout_seconds=5)
        assert limited.exit_code != 0, "a child escaped the Job active-process limit"
    finally:
        await backend.destroy("skill-egress")


@native_windows
@pytest.mark.asyncio
async def test_windows_real_network_broker_pipe_denies_appcontainer(tmp_path):
    backend = WindowsSandboxBackend(tmp_path)
    assert await backend.probe_network() == (True, None)
    pipe = windows_network_broker._PipeSecurity().name
    await backend.create("broker-denial")
    try:
        await backend.start("broker-denial")
        response = await backend.execute("broker-denial", ["cmd.exe", "/d", "/s", "/c",
            'echo forbidden>"' + pipe + '"'], execution_policy={"network_enabled": True}, timeout_seconds=5)
        assert response.exit_code != 0 and not response.timed_out, response
        assert await backend.probe_network() == (True, None), "unauthorized guest damaged broker availability"
    finally:
        await backend.destroy("broker-denial")


@native_windows
@pytest.mark.skipif(not os.environ.get("OAW_TEST_PRIVATE_URL"),
                    reason="set OAW_TEST_PRIVATE_URL to a controlled reachable private-network HTTP peer")
@pytest.mark.asyncio
async def test_windows_real_private_peer_is_blocked_independent_of_interface_profile(tmp_path):
    import uuid
    url = private_url()
    token = uuid.uuid4().hex
    assert host_get(url + "?host-before=" + token)
    backend = WindowsSandboxBackend(tmp_path)
    await backend.create("private-peer")
    try:
        await backend.start("private-peer")
        response = await backend.execute("private-peer", _curl(url + "?guest=" + token, 3),
            execution_policy={"network_enabled": True}, timeout_seconds=5)
        assert response.exit_code in {7, 28} and not response.stdout, response
        assert host_get(url + "?host-after=" + token)
        peer_evidence = None
        if os.environ.get("OAW_TEST_PRIVATE_EVIDENCE_URL"):
            import json
            peer_evidence = json.loads(host_get(os.environ["OAW_TEST_PRIVATE_EVIDENCE_URL"]))
            paths = [r["path"] for r in peer_evidence]
            assert any("host-before=" + token in p for p in paths)
            assert any("host-after=" + token in p for p in paths)
            assert not any("guest=" + token in p for p in paths)
        evidence("private-peer", {"profile": backend._records["private-peer"].identity,
            "url": url, "guest_exit_code": response.exit_code, "server_requests": peer_evidence})
    finally:
        await backend.destroy("private-peer")


class _ReadOnlyWfpFilters:
    """Native acceptance inspection only; binds no policy mutation operation."""

    def __init__(self):
        self.api = ctypes.WinDLL("fwpuclnt", use_last_error=True)
        self.handle = ctypes.wintypes.HANDLE()
        self.api.FwpmEngineOpen0.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.HANDLE)]
        self.api.FwpmEngineOpen0.restype = ctypes.c_uint32
        self.api.FwpmEngineClose0.argtypes = [ctypes.wintypes.HANDLE]
        self.api.FwpmEngineClose0.restype = ctypes.c_uint32
        self.api.FwpmFilterGetByKey0.argtypes = [ctypes.wintypes.HANDLE,
            ctypes.POINTER(windows_wfp._Guid), ctypes.POINTER(ctypes.POINTER(windows_wfp._Filter))]
        self.api.FwpmFilterGetByKey0.restype = ctypes.c_uint32
        self.api.FwpmFreeMemory0.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.api.FwpmFreeMemory0.restype = None
        code = self.api.FwpmEngineOpen0(None, 10, None, None, ctypes.byref(self.handle))
        assert code == 0, f"Read-only WFP inspection unavailable: 0x{code:08X}"

    def flags(self, key):
        guid = windows_wfp._Guid.of(key)
        rule = ctypes.POINTER(windows_wfp._Filter)()
        code = self.api.FwpmFilterGetByKey0(self.handle, ctypes.byref(guid), ctypes.byref(rule))
        if code == 0x80320003:  # FWP_E_FILTER_NOT_FOUND
            return None
        assert code == 0, f"Read-only WFP filter inspection failed: 0x{code:08X}"
        try:
            assert bytes(rule.contents.key) == bytes(guid)
            return rule.contents.flags
        finally:
            self.api.FwpmFreeMemory0(ctypes.cast(ctypes.byref(rule), ctypes.POINTER(ctypes.c_void_p)))

    def close(self):
        if self.handle:
            self.api.FwpmEngineClose0(self.handle)
            self.handle = ctypes.wintypes.HANDLE()


@native_windows
@pytest.mark.asyncio
async def test_windows_real_enabled_timeout_and_launch_failure_cleanup(tmp_path):
    backend = WindowsSandboxBackend(tmp_path)
    assert await backend.probe_network() == (True, None)
    await backend.create("timeout-egress")
    record = backend._records["timeout-egress"]
    keys = windows_wfp.filter_keys(record.identity)
    try:
        await backend.start("timeout-egress")
        result = await backend.execute("timeout-egress",
            ["cmd.exe", "/d", "/c", "for /L %i in (1,1,2147483647) do @rem"],
            execution_policy={"network_enabled": True}, timeout_seconds=0.2)
        assert result.timed_out and record.active_job is None
        assert (await backend.get("timeout-egress")).state == SandboxState.READY
        with pytest.raises(FileNotFoundError, match="nonexistent.exe"):
            await backend.execute("timeout-egress", [str(tmp_path / "nonexistent.exe")],
                execution_policy={"network_enabled": True})
        assert record.active_job is None
        assert (await backend.get("timeout-egress")).state == SandboxState.ERROR
        await backend.start("timeout-egress")
        followup = await backend.execute("timeout-egress", ["cmd.exe", "/d", "/c", "echo recovered"],
            execution_policy={"network_enabled": True})
        assert followup.exit_code == 0 and "recovered" in followup.stdout
    finally:
        await backend.destroy("timeout-egress")
    assert record.active_job is None and not record.root.exists()
    inspector = _ReadOnlyWfpFilters()
    try:
        assert all(inspector.flags(key) is None for key in keys)
    finally:
        inspector.close()


@native_windows
@pytest.mark.asyncio
async def test_windows_real_owned_wfp_filters_exist_during_workload_and_are_removed_on_destroy(tmp_path):
    backend = WindowsSandboxBackend(acceptance_root())
    assert await backend.probe_network() == (True, None)
    await backend.create("filter-lifecycle")
    inspector = None
    workload = None
    destroyed = False
    try:
        record = backend._records["filter-lifecycle"]
        inspector = WfpAudit(record.identity, profile_sid(record))
        inspector.absent("before-admission")
        await backend.start("filter-lifecycle")
        workload = asyncio.create_task(backend.execute("filter-lifecycle",
            ["cmd.exe", "/d", "/c", "for /L %i in (1,1,2147483647) do @rem"],
            execution_policy={"network_enabled": True}, timeout_seconds=30))
        for _ in range(100):
            if record.active_job is not None:
                break
            if workload.done():
                await workload  # Surface actual setup failure instead of a polling assertion.
            await asyncio.sleep(0.02)
        assert record.active_job is not None and not workload.done()
        inspector.installed("during-workload")
        await backend.destroy("filter-lifecycle")
        destroyed = True
        assert (await asyncio.wait_for(workload, timeout=10)).cancelled
        assert record.active_job is None
        inspector.absent("after-destruction")
    finally:
        if not destroyed:
            await backend.destroy("filter-lifecycle")
        if workload is not None:
            await asyncio.wait_for(workload, timeout=10)
        if inspector is not None:
            assert record.active_job is None
            inspector.absent("teardown")
