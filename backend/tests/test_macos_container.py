"""Unit tests for the macOS Container VM sandbox backend.

Native integration requires macOS 26 on Apple Silicon with the container CLI
installed and its system service running; these tests mock the container
process to verify host-side state management and request framing on any OS.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from backend.sandbox.macos_container import (
    MacosContainerSandboxBackend,
    _ContainerPythonRuntime,
    minimal_container_environment,
    validate_argv,
    validate_relative_path,
)
from backend.sandbox.macos_container_network import (
    NETWORK_BOOTSTRAP_SOURCE, NETWORK_IMAGE, network_bootstrap_arguments,
    probe_network_image,
)
from backend.sandbox.models import SandboxLimits
from backend.sandbox.models import (
    CommandResult, ResourceAccess, SandboxNetworkError, SandboxNotFoundError, SandboxSecurityError,
    SandboxState, SandboxStateError, SandboxValidationError,
)


class _FakeProcess:
    def __init__(self, response: bytes | None = None, exit_code: int = 0,
        stderr: bytes = b""):
        self.stdin = _FakeWriter()
        self.stdout = _FakeReader(response)
        self.stderr = _FakeReader(stderr)
        self.returncode = None
        self._exit_code = exit_code

    async def wait(self):
        self.returncode = self._exit_code
        return self._exit_code

    async def communicate(self):
        self.returncode = self._exit_code
        return await self.stdout.read(), await self.stderr.read()

    def kill(self):
        self.returncode = -9


class _FakeWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class _FakeReader:
    def __init__(self, data: bytes):
        self._data = data

    async def readline(self):
        return self._data

    async def read(self, limit=-1):
        return self._data[:limit] if limit > 0 else self._data


def _ok_response() -> bytes:
    return (json.dumps({"exit_code": 0, "stdout": "hello", "stderr": "",
        "duration_seconds": 0.1, "timed_out": False}) + "\n").encode()


def make_backend(tmp_path: Path, *, process=None) -> MacosContainerSandboxBackend:
    return MacosContainerSandboxBackend(
        tmp_path / "managed", container_path="/nonexistent/container",
        runtime_id="macos-container-test",
    )


@pytest.mark.asyncio
async def test_create_and_get_roundtrip(tmp_path):
    backend = make_backend(tmp_path)
    info = await backend.create("lab")
    assert info.state == SandboxState.STOPPED
    assert info.runtime_id == "macos-container-test"
    assert info.security_boundary == "macos-containerization-vm"
    assert info.supported_network_modes == ("disabled", "enabled")
    info2 = await backend.get("lab")
    assert info2.sandbox_id == "lab"
    assert (tmp_path / "managed" / "sandbox-runtimes").is_dir()


@pytest.mark.asyncio
async def test_create_duplicate_rejects(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    with pytest.raises(SandboxStateError, match="already exists"):
        await backend.create("lab")


@pytest.mark.asyncio
async def test_get_missing_raises_not_found(tmp_path):
    backend = make_backend(tmp_path)
    with pytest.raises(SandboxNotFoundError):
        await backend.get("missing")


@pytest.mark.asyncio
async def test_execute_without_start_rejected(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    with pytest.raises(SandboxStateError, match="ready"):
        await backend.execute("lab", ["echo", "hi"])


@pytest.mark.asyncio
async def test_terminate_transitions_to_stopped(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    monkeypatch.setattr("backend.sandbox.macos_container.sys.platform", "darwin")
    await backend.start("lab")
    await backend.terminate("lab")
    info = await backend.get("lab")
    assert info.state == SandboxState.STOPPED


@pytest.mark.asyncio
async def test_destroy_removes_storage(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    root = tmp_path / "managed" / "sandbox-runtimes"
    sandbox_dir = next(root.iterdir()) / "sandboxes" / "lab"
    assert sandbox_dir.is_dir()
    await backend.destroy("lab")
    assert not sandbox_dir.exists()
    with pytest.raises(SandboxNotFoundError):
        await backend.get("lab")


def test_validate_argv_rejects_strings():
    with pytest.raises(SandboxValidationError):
        validate_argv("echo hello")
    with pytest.raises(SandboxValidationError):
        validate_argv([])
    with pytest.raises(SandboxValidationError):
        validate_argv(["echo", "bad\0null"])
    assert validate_argv(["echo", "ok"]) == ("echo", "ok")


def test_validate_relative_path_rejects_traversal():
    with pytest.raises(SandboxValidationError):
        validate_relative_path("../escape")
    with pytest.raises(SandboxValidationError):
        validate_relative_path("/absolute")
    with pytest.raises(SandboxValidationError):
        validate_relative_path("a//b")
    assert validate_relative_path("dir/file.txt") == "dir/file.txt"


def test_minimal_container_environment_allowlist():
    env = minimal_container_environment({"TZ": "UTC"})
    assert env["TZ"] == "UTC"
    assert env["HOME"] == "/sandbox/home"
    assert env["PATH"].startswith("/sandbox/home/.local/bin")
    with pytest.raises(SandboxValidationError):
        minimal_container_environment({"SECRET_KEY": "no"})


def test_enabled_network_rules_block_host_and_non_public_addresses(monkeypatch):
    monkeypatch.setattr(
        "backend.sandbox.macos_container_network.host_ipv4_addresses",
        lambda: ["203.0.114.7", "192.168.1.9"],
    )
    settings = json.loads(network_bootstrap_arguments(501, 20))
    assert settings["uid"] == 501 and settings["gid"] == 20
    rules = settings["rules"]
    assert "203.0.114.7/32" in rules
    assert "192.168.0.0/16" in rules
    assert "policy drop" in rules
    assert "meta nfproto ipv4 meta l4proto { tcp, udp } accept" in rules


@pytest.mark.asyncio
async def test_network_probe_reports_filter_failure_as_setup_failure(monkeypatch):
    monkeypatch.setattr("backend.sandbox.macos_container_network.sys.platform", "darwin")
    monkeypatch.setattr("backend.sandbox.macos_container_network.image_available",
        lambda _path: True)
    monkeypatch.setattr("backend.sandbox.macos_container_network.network_bootstrap_arguments",
        lambda _uid, _gid: '{}')

    async def create(*_argv, **_options):
        return _FakeProcess(b"", exit_code=125,
            stderr=b"OAW_SANDBOX_NETWORK_SETUP: nft unavailable")

    monkeypatch.setattr("backend.sandbox.macos_container_network.asyncio.create_subprocess_exec", create)
    with pytest.raises(SandboxNetworkError, match="nft unavailable"):
        await probe_network_image("/container")


@pytest.mark.asyncio
async def test_container_command_forces_offline_network_and_read_only_workspace(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    await backend.configure("lab", workspace_path=None,
        workspace_access=ResourceAccess.READ_ONLY)
    record = await backend._record("lab")
    command = backend._container_command(record, None, SandboxLimits())
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--ulimit") + 1] == "nproc=65:65"
    assert command[command.index("--uid") + 1].isdigit()
    assert command[command.index("--gid") + 1].isdigit()
    assert "--read-only" in command
    assert command[command.index("--tmpfs") + 1] == "/tmp"
    volumes = [command[index + 1] for index, item in enumerate(command[:-1])
        if item == "--volume"]
    assert any(volume.endswith(":/workspace:ro") for volume in volumes)
    assert any(volume.endswith(":/opt/oaw-python:ro") for volume in volumes)


@pytest.mark.asyncio
async def test_container_command_keeps_writable_workspace_writable(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    record = await backend._record("lab")
    command = backend._container_command(record, None, SandboxLimits())
    volumes = [command[index + 1] for index, item in enumerate(command[:-1])
        if item == "--volume"]
    assert any(volume.endswith(":/workspace") and not volume.endswith(":ro")
        for volume in volumes)


@pytest.mark.asyncio
async def test_enabled_container_installs_firewall_before_dropping_privileges(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    record = await backend._record("lab")
    command = backend._container_command(
        record, None, SandboxLimits(), network_settings='{"uid":501,"gid":20,"rules":"test"}')
    assert command[command.index("--network") + 1] == "default"
    assert command[command.index("--uid") + 1] == "0"
    assert command[command.index("--gid") + 1] == "0"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert {command[index + 1] for index, item in enumerate(command[:-1])
        if item == "--cap-add"} == {"NET_ADMIN", "SETPCAP", "SETUID", "SETGID"}
    assert command[command.index("--dns") + 1] == "1.1.1.1"
    assert NETWORK_IMAGE in command
    assert NETWORK_BOOTSTRAP_SOURCE in command
    assert command[-1].startswith("\nimport json, os, subprocess")


@pytest.mark.asyncio
async def test_execution_containers_have_scoped_recoverable_names(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    record = await backend._record("lab")
    container_id = backend._execution_container_id("lab", "command-1")
    command = backend._container_command(
        record, None, SandboxLimits(), container_id=container_id)
    assert command[command.index("--name") + 1] == container_id
    assert container_id.startswith(backend._container_prefix("lab"))
    assert backend._execution_container_id("other", "command-1") != container_id


@pytest.mark.asyncio
async def test_startup_cleanup_only_deletes_scoped_orphans(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    prefix = backend._container_prefix("lab")
    orphan = prefix + "0123456789abcdef"
    calls = []

    async def create(*argv, **options):
        calls.append(argv)
        if argv[1] == "list":
            return _FakeProcess(f"{orphan}\nunrelated-container\n".encode())
        return _FakeProcess(b"")

    monkeypatch.setattr(
        "backend.sandbox.macos_container.asyncio.create_subprocess_exec", create)
    await backend._cleanup_orphaned_containers("lab")
    deletes = [argv for argv in calls if argv[1] == "delete"]
    assert deletes == [("/nonexistent/container", "delete", "--force", orphan)]


def test_container_python_runtime_is_linux_native_and_receipted(tmp_path, monkeypatch):
    runtime = _ContainerPythonRuntime(tmp_path / "managed", "/container")
    calls = []

    def run(command, **options):
        calls.append((command, options))
        if len(command) > 1 and command[1] == "run":
            runtime.python.parent.mkdir(parents=True, exist_ok=True)
            runtime.python.touch()
            (runtime.venv / "pyvenv.cfg").touch()
        return type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr("backend.sandbox.macos_container.subprocess.run", run)
    result = runtime.prepare_sync(["ase>=3.22"], "enabled-packs")
    assert result["python"] == "/opt/oaw-python/venv/bin/python"
    run_calls = [call for call in calls if call[0][1] == "run"]
    command = run_calls[0][0]
    assert command[command.index("--network") + 1] == "default"
    assert "--only-binary" in command[-2]
    assert json.loads(command[-1]) == ["ase>=3.22"]
    assert runtime.prepare_sync(["ase>=3.22"], "enabled-packs") == result
    assert len(run_calls) == 1


def test_container_python_rewrites_python_and_environment(tmp_path):
    runtime = _ContainerPythonRuntime(tmp_path / "managed", "/container")
    environment = minimal_container_environment()
    runtime.environment(environment)
    assert environment["PATH"].startswith("/opt/oaw-python/venv/bin:")
    assert environment["VIRTUAL_ENV"] == "/opt/oaw-python/venv"
    assert runtime.command(["python", "-c", "pass"])[0] == "/opt/oaw-python/venv/bin/python"


@pytest.mark.asyncio
async def test_execute_enabled_network_rejects_missing_policy_image(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    monkeypatch.setattr("backend.sandbox.macos_container.sys.platform", "darwin")
    monkeypatch.setattr("backend.sandbox.macos_container.image_available", lambda _path: False)
    await backend.start("lab")
    with pytest.raises(SandboxNetworkError, match="public-egress image is missing"):
        await backend.execute("lab", ["echo", "hi"],
            execution_policy={"network_enabled": True})


@pytest.mark.asyncio
async def test_enabled_container_reports_firewall_setup_failure(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    monkeypatch.setattr("backend.sandbox.macos_container.sys.platform", "darwin")
    monkeypatch.setattr(backend, "_cleanup_orphaned_containers", lambda _id: asyncio.sleep(0))
    monkeypatch.setattr(backend.python_runtime, "prepare", lambda: asyncio.sleep(0))
    monkeypatch.setattr(backend.python_runtime, "environment", lambda _env: None)
    monkeypatch.setattr(backend.python_runtime, "command", lambda command: command)
    monkeypatch.setattr("backend.sandbox.macos_container.image_available", lambda _path: True)
    monkeypatch.setattr("backend.sandbox.macos_container.network_bootstrap_arguments",
        lambda _uid, _gid: '{}')

    async def create(*_argv, **_options):
        return _FakeProcess(b"", exit_code=125,
            stderr=b"OAW_SANDBOX_NETWORK_SETUP: nft rejected policy\n")

    monkeypatch.setattr("backend.sandbox.macos_container.asyncio.create_subprocess_exec", create)
    await backend.start("lab")
    with pytest.raises(SandboxNetworkError, match="nft rejected policy"):
        await backend.execute("lab", ["echo", "hi"],
            execution_policy={"network_enabled": True})
    assert (await backend.get("lab")).active_command is None


@pytest.mark.asyncio
async def test_attach_and_detach_resource(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    source = tmp_path / "managed" / "resource.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("data")
    attachment = await backend.attach_resource("lab", "res1", source, "data/file.txt", ResourceAccess.READ_ONLY)
    assert attachment.access == ResourceAccess.READ_ONLY
    info = await backend.get("lab")
    assert len(info.attachments) == 1
    await backend.detach_resource("lab", "res1")
    info = await backend.get("lab")
    assert len(info.attachments) == 0


@pytest.mark.asyncio
async def test_attach_conflicting_path_rejected(tmp_path):
    backend = make_backend(tmp_path)
    await backend.create("lab")
    source = tmp_path / "managed" / "resource.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("data")
    await backend.attach_resource("lab", "res1", source, "dir/file.txt", ResourceAccess.READ_ONLY)
    with pytest.raises(SandboxValidationError, match="conflicts"):
        await backend.attach_resource("lab", "res2", source, "dir/file.txt", ResourceAccess.READ_WRITE)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS") != "1",
    reason="set OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS=1 on macOS",
)
@pytest.mark.asyncio
async def test_native_container_is_offline_and_uses_managed_python(tmp_path):
    backend = MacosContainerSandboxBackend(tmp_path / "managed")
    available, reason = await backend.probe()
    assert available, reason
    await backend.create("native")
    await backend.start("native")
    result = await backend.execute("native", ["python", "-c",
        "import os,sys; print(sys.executable); print(','.join(os.listdir('/sys/class/net')))"])
    assert result.exit_code == 0, result.stderr
    assert "/opt/oaw-python/venv/bin/python" in result.stdout
    assert result.stdout.strip().splitlines()[-1] == "lo"


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS") != "1",
    reason="set OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS=1 on macOS",
)
@pytest.mark.asyncio
async def test_native_container_public_egress_requires_filtered_image(tmp_path):
    backend = MacosContainerSandboxBackend(tmp_path / "managed")
    available, reason = await backend.probe_network()
    assert available, reason
    await backend.create("network")
    await backend.start("network")
    url = os.environ.get("OAW_TEST_HTTPS_URL", "https://example.com")
    result = await backend.execute("network", ["curl", "--disable", "--noproxy", "*",
        "--fail", "--silent", "--show-error", "--max-time", "15", "--", url],
        execution_policy={"network_enabled": True})
    assert result.exit_code == 0, result.stderr
