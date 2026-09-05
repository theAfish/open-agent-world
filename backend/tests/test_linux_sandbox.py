from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path, PurePosixPath

import pytest

import backend.sandbox.linux as linux_module

from backend.sandbox.linux import (
    LinuxSandboxBackend, _SERVICE_GUARD, bubblewrap_command,
    minimal_linux_environment, new_unit_name, service_command,
    validate_argv, validate_relative_path,
)
from backend.sandbox.models import (
    ResourceAccess, ResourceAttachment, SandboxInfo, SandboxLimits,
    SandboxSecurityError, SandboxState, SandboxStateError, SandboxValidationError,
)
from backend.sandbox.wsl import WslSandboxBackend, _BOOTSTRAP, _envelope, wsl_command


def test_linux_policy_does_not_expose_host_credentials_or_interop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/1_interop")
    environment = minimal_linux_environment()
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "WSL_INTEROP" not in environment
    assert "WSLENV" not in environment
    with pytest.raises(SandboxValidationError, match="allowlist"):
        minimal_linux_environment({"LD_PRELOAD": "/workspace/escape.so"})
    with pytest.raises(SandboxValidationError, match="allowlist"):
        minimal_linux_environment({"PATH": "/mnt/c/Windows/System32"})
    mounts = [ResourceAttachment("box", "read", PurePosixPath("/managed/assets/a"),
        "resources/a", ResourceAccess.READ_ONLY)]
    command = bubblewrap_command(PurePosixPath("/home/user/project"), ResourceAccess.READ_ONLY,
        mounts, ["/bin/sh", "-c", "printf hello"], environment)
    assert ["--ro-bind", "/home/user/project", "/workspace"] == command[command.index("/home/user/project") - 1:command.index("/home/user/project") + 2]
    assert ["--ro-bind", "/managed/assets/a", "/sandbox/resources/a"] == command[command.index("/managed/assets/a") - 1:command.index("/managed/assets/a") + 2]
    for flag in ("--unshare-all", "--unshare-user", "--new-session", "--die-with-parent", "--clearenv"):
        assert flag in command
    assert "--share-net" not in command
    assert "/run" not in command
    assert "/mnt" not in command
    assert "/home" not in command
    assert "/root" not in command
    assert "/usr/local" not in command


def test_systemd_command_keeps_metacharacters_opaque_and_enforces_tree_limits() -> None:
    argv = ["/usr/bin/bwrap", "--", "/bin/sh", "-c", 'printf "$HOME;$(id)\\x"', "", "中文"]
    limits = SandboxLimits(memory_bytes=32 * 1024 * 1024, active_process_limit=9)
    command = service_command(argv, new_unit_name(), limits, 1.25)
    assert json.loads(base64.b64decode(command[-1]))["command"] == argv
    assert "$" not in command[-1]
    assert "--property=MemoryMax=33554432" in command
    assert "--property=MemorySwapMax=0" in command
    assert "--property=TasksMax=9" in command
    assert "--property=RuntimeMaxSec=4.250" in command
    assert "--property=KillMode=control-group" in command
    assert "memory.max" in _SERVICE_GUARD and "pids.max" in _SERVICE_GUARD
    assert "'socket'" in _SERVICE_GUARD and "'connect'" in _SERVICE_GUARD
    assert "0x10000000" in _SERVICE_GUARD  # CLONE_NEWUSER denied by seccomp.
    assert "errno.ENOSYS" in _SERVICE_GUARD  # clone3 falls back safely.
    with pytest.raises(SandboxValidationError, match="identity"):
        service_command(argv, "user-session.service", limits, 1)


@pytest.mark.parametrize("path", ["../secret", "/etc/shadow", "a/../b", "a//b", "./a", "C:/secret", "resources\\a", "a\0b"])
def test_linux_attachment_paths_reject_traversal_and_platform_ambiguity(path: str) -> None:
    with pytest.raises(SandboxValidationError):
        validate_relative_path(path)


def test_argv_preserves_empty_arguments_but_rejects_shell_strings() -> None:
    assert validate_argv(["printf", "%s", ""]) == ("printf", "%s", "")
    with pytest.raises(SandboxValidationError):
        validate_argv("echo hello")
    with pytest.raises(SandboxValidationError):
        validate_argv(["", "hello"])


@pytest.mark.asyncio
async def test_linux_external_workspace_persists_and_is_never_deleted(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    project = tmp_path / "project"
    project.mkdir()
    content = project / "keep.txt"
    content.write_text("user project", encoding="utf-8")
    backend = LinuxSandboxBackend(managed)
    await backend.create("box")
    configured = await backend.configure("box", workspace_path=str(project),
        workspace_access=ResourceAccess.READ_ONLY)
    assert configured.workspace_path == str(project.resolve())
    assert configured.workspace_access == ResourceAccess.READ_ONLY
    restarted = LinuxSandboxBackend(managed)
    loaded = await restarted.get("box")
    assert loaded.workspace_path == configured.workspace_path
    assert loaded.workspace_access == ResourceAccess.READ_ONLY
    await restarted.destroy("box")
    assert content.read_text(encoding="utf-8") == "user project"
    assert not (restarted._root / "box").exists()


@pytest.mark.asyncio
async def test_linux_attachment_scope_conflicts_and_revocation(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    assets = managed / "assets"
    assets.mkdir(parents=True)
    a, b = assets / "a", assets / "b"
    a.write_text("first")
    b.write_text("second")
    outside = tmp_path / "secret"
    outside.write_text("secret")
    backend = LinuxSandboxBackend(managed)
    await backend.create("box")
    with pytest.raises(SandboxValidationError, match="managed"):
        await backend.attach_resource("box", "secret", outside, "secret", ResourceAccess.READ_ONLY)
    await backend.attach_resource("box", "a", a, "resources/a", ResourceAccess.READ_ONLY)
    with pytest.raises(SandboxValidationError, match="conflicts"):
        await backend.attach_resource("box", "b", b, "resources/a/child", ResourceAccess.READ_WRITE)
    with pytest.raises(SandboxValidationError, match="conflicts"):
        await backend.attach_resource("box", "same", a, "elsewhere", ResourceAccess.READ_WRITE)
    await backend.detach_resource("box", "a")
    assert not (await backend.get("box")).attachments
    assert a.read_text() == "first"


@pytest.mark.asyncio
async def test_linux_rejects_workspace_overlap_and_busy_mutation(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    backend = LinuxSandboxBackend(managed)
    await backend.create("box")
    for path in (managed, tmp_path, backend._root):
        with pytest.raises(SandboxValidationError, match="overlap"):
            await backend.configure("box", workspace_path=str(path), workspace_access=ResourceAccess.READ_WRITE)
    record = await backend._record("box")
    record.state = SandboxState.RUNNING
    with pytest.raises(SandboxStateError, match="stop"):
        await backend.configure("box", workspace_path=None, workspace_access=ResourceAccess.READ_ONLY)


@pytest.mark.asyncio
async def test_linux_manifest_failure_rolls_back_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = LinuxSandboxBackend(tmp_path / "managed")
    await backend.create("box")
    project = tmp_path / "project"
    project.mkdir()
    def fail(record: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(backend, "_save", fail)
    with pytest.raises(OSError, match="disk full"):
        await backend.configure("box", workspace_path=str(project), workspace_access=ResourceAccess.READ_ONLY)
    info = await backend.get("box")
    assert info.workspace_path is None
    assert info.workspace_access == ResourceAccess.READ_WRITE


def test_wsl_bridge_never_interpolates_user_commands_into_launcher() -> None:
    distro = "Ubuntu Name; $(whoami)"
    command = wsl_command(distro)
    assert command[command.index("--distribution") + 1] == distro
    assert command[-1] == _BOOTSTRAP
    assert "/bin/sh" not in command
    request = {"operation": "execute", "argv": ["/bin/sh", "-c", "echo $HOME; $(id)"],
        "workspace_path": 'D:\\some folder\\"; malicious'}
    decoded = json.loads(_envelope(request))
    assert decoded["request"] == request
    assert "echo $HOME" not in command[-1]
    assert [item[0] for item in decoded["modules"]] == ["models", "base", "linux", "linux_worker"]
    with pytest.raises(SandboxValidationError):
        wsl_command("--terminate")


def test_wsl_bootstrap_preserves_cancellation_coalesced_with_request() -> None:
    worker = "def main(request, *, stdin_pending=False):\n print('cancel-pending' if stdin_pending else 'missed-cancel')\n"
    data = json.dumps({"modules": [("linux_worker", worker)], "request": {}}).encode() + b'\n{"cancel":true}\n'
    result = subprocess.run([sys.executable, "-I", "-c", _BOOTSTRAP], input=data, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == b"cancel-pending"


def test_wsl_control_helper_source_is_frozen_before_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    def changed_file(*args: object, **kwargs: object) -> str:
        raise AssertionError("trusted worker must not be reloaded from editable source")
    monkeypatch.setattr(Path, "read_text", changed_file)
    assert json.loads(_envelope({"operation": "get"}))["modules"]


@pytest.mark.asyncio
async def test_linux_workspace_hardlink_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("host contents")
    os.link(outside, project / "linked")
    backend = LinuxSandboxBackend(tmp_path / "managed")
    await backend.create("box")
    with pytest.raises(SandboxValidationError, match="hard-linked"):
        await backend.configure("box", workspace_path=str(project), workspace_access=ResourceAccess.READ_WRITE)


@pytest.mark.asyncio
async def test_linux_failed_cleanup_preserves_scope_until_stop_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = LinuxSandboxBackend(tmp_path / "managed")
    await backend.create("box")
    record = await backend._record("box")
    record.state = SandboxState.READY
    async def cannot_launch(*args: object, **kwargs: object) -> None:
        raise OSError("launch failed")
    async def cannot_stop(unit: str) -> None:
        raise SandboxSecurityError("scope stop failed")
    monkeypatch.setattr(linux_module, "_host_control_environment", lambda: {})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", cannot_launch)
    monkeypatch.setattr(backend, "kill_unit", cannot_stop)
    with pytest.raises(SandboxSecurityError, match="stop failed"):
        await backend.execute("box", ["/bin/true"])
    assert record.state == SandboxState.ERROR
    assert record.unit is not None
    assert json.loads((record.root / "sandbox.json").read_text())["unit"] == record.unit
    with pytest.raises(SandboxSecurityError, match="stop failed"):
        await backend.terminate("box")
    assert record.state == SandboxState.ERROR
    async def stopped(unit: str) -> None:
        pass
    monkeypatch.setattr(backend, "kill_unit", stopped)
    await backend.terminate("box")
    assert record.state == SandboxState.STOPPED
    assert record.unit is None


@pytest.mark.asyncio
async def test_linux_reloads_cleanup_before_missing_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    managed = tmp_path / "managed"
    backend = LinuxSandboxBackend(managed)
    await backend.create("box")
    source = managed / "assets" / "a"
    source.parent.mkdir()
    source.write_text("managed resource")
    await backend.attach_resource("box", "a", source, "resources/a", ResourceAccess.READ_ONLY)
    record = await backend._record("box")
    record.unit = new_unit_name()
    backend._save(record)
    source.unlink()
    seen = []
    async def stop(unit: str) -> None:
        seen.append(unit)
    restarted = LinuxSandboxBackend(managed)
    monkeypatch.setattr(restarted, "kill_unit", stop)
    await restarted.destroy("box")
    assert seen == [record.unit]


def test_wsl_info_preserves_posix_runtime_paths_and_windows_workspace(tmp_path: Path) -> None:
    backend = WslSandboxBackend(tmp_path, distribution="Ubuntu")
    raw = asdict(SandboxInfo("box", SandboxState.STOPPED, PurePosixPath("/workspace"),
        runtime_id="wsl:Ubuntu", platform="linux", shell=("/bin/sh", "-c"),
        workspace_path=r"D:\Project", resources_path=PurePosixPath("/sandbox")))
    info = backend._info(raw)
    assert str(info.workspace) == "/workspace"
    assert str(info.resources_path) == "/sandbox"
    assert info.workspace_path == r"D:\Project"


@pytest.mark.asyncio
async def test_wsl_rejects_nonfinite_timeout_before_starting_worker(tmp_path: Path) -> None:
    backend = WslSandboxBackend(tmp_path, distribution="Ubuntu")
    for timeout in (float("nan"), float("inf"), 0, -1):
        with pytest.raises(SandboxValidationError, match="finite"):
            await backend.execute("box", ["true"], timeout_seconds=timeout)
    assert not backend._active


# Real security-boundary tests are opt-in on Windows because invoking WSL is
# permission-gated in development environments. They install nothing.
@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("OAW_TEST_WSL_DISTRO"), reason="set OAW_TEST_WSL_DISTRO to an existing WSL2 distro")
async def test_real_wsl_files_network_resource_limits_timeout_and_cancellation(tmp_path: Path) -> None:
    distro = os.environ["OAW_TEST_WSL_DISTRO"]
    available, reason = await WslSandboxBackend.probe(distro)
    assert available, reason
    managed = tmp_path / "managed"
    assets = managed / "assets"
    assets.mkdir(parents=True)
    attachment = assets / "notes.txt"
    attachment.write_text("read only input", encoding="utf-8")
    external = tmp_path / "project with spaces 中文"
    external.mkdir()
    secret = tmp_path / "host-secret.txt"
    secret.write_text("must remain private", encoding="utf-8")
    events = []
    backend = WslSandboxBackend(managed, distribution=distro,
        limits=SandboxLimits(memory_bytes=96 * 1024 * 1024, active_process_limit=16),
        event_sink=events.append)
    await backend.create("live")
    try:
        await backend.configure("live", workspace_path=str(external), workspace_access=ResourceAccess.READ_WRITE)
        await backend.attach_resource("live", "notes", attachment, "resources/notes.txt", ResourceAccess.READ_ONLY)
        await backend.start("live")
        result = await backend.execute("live", ["/bin/sh", "-c",
            "printf live > changed.txt; cat /sandbox/resources/notes.txt; "
            "test ! -e /mnt/c; test ! -e /run/WSL; test ! -e /root/.ssh; "
            "test ! -e /workspace/../host-secret.txt; "
            "! printf bad > /sandbox/resources/notes.txt"], timeout_seconds=8)
        assert result.exit_code == 0, result.stderr
        assert "read only input" in result.stdout
        assert (external / "changed.txt").read_text() == "live"
        assert attachment.read_text() == "read only input"
        assert any(event.type.value == "sandbox_stdout" for event in events)
        denied = await backend.execute("live", ["/usr/bin/python3", "-c",
            "import socket; socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)"])
        assert denied.exit_code != 0 and "Operation not permitted" in denied.stderr
        ns = await backend.execute("live", ["/usr/bin/unshare", "-Ur", "/bin/true"])
        assert ns.exit_code != 0
        # Ordinary threads/processes still work with clone3->clone fallback.
        threads = await backend.execute("live", ["/usr/bin/python3", "-c",
            "import threading,subprocess; t=threading.Thread(target=lambda:print('thread-ok')); t.start(); t.join(); subprocess.run(['/bin/true'],check=True)"])
        assert threads.exit_code == 0 and "thread-ok" in threads.stdout
        memory = await backend.execute("live", ["/usr/bin/python3", "-c", "x=bytearray(256*1024*1024)"], timeout_seconds=8)
        assert memory.exit_code != 0
        processes = await backend.execute("live", ["/usr/bin/python3", "-c",
            "import subprocess\nchildren=[]\ntry:\n"
            " for i in range(40):\n"
            "  try: children.append(subprocess.Popen(['/bin/sleep','5']))\n"
            "  except BlockingIOError: print('process-limit-enforced'); break\n"
            "finally:\n"
            " for child in children: child.kill()\n"
            " for child in children: child.wait()\n"], timeout_seconds=8)
        assert processes.exit_code == 0 and "process-limit-enforced" in processes.stdout, processes.stderr
        before_output = len(events)
        capped = await backend.execute("live", ["/usr/bin/python3", "-c",
            "import sys; sys.stdout.buffer.write(b'\\x01'*(2*1024*1024+1)); "
            "sys.stderr.buffer.write(b'\\x02'*(2*1024*1024+1))"], timeout_seconds=8)
        assert capped.exit_code == 0
        assert capped.stdout.count("[output truncated at 2 MiB]") == 1
        assert capped.stderr.count("[output truncated at 2 MiB]") == 1
        assert sum("[output truncated at 2 MiB]" in event.payload.get("text", "")
            for event in events[before_output:]) == 2
        await backend.configure("live", workspace_path=str(external), workspace_access=ResourceAccess.READ_ONLY)
        await backend.start("live")
        readonly = await backend.execute("live", ["/bin/sh", "-c", "printf no > denied.txt"])
        assert readonly.exit_code != 0
        assert not (external / "denied.txt").exists()
        await backend.configure("live", workspace_path=str(external), workspace_access=ResourceAccess.READ_WRITE)
        await backend.start("live")
        timeout = await backend.execute("live", ["/bin/sh", "-c",
            "(sleep 2; printf escaped > orphan.txt) & wait"], timeout_seconds=0.2)
        assert timeout.timed_out
        running = asyncio.create_task(backend.execute("live", ["/bin/sh", "-c",
            "(sleep 2; printf escaped > cancelled-orphan.txt) & wait"], timeout_seconds=10))
        await asyncio.sleep(0.4)
        await backend.terminate("live")
        assert (await running).cancelled
        await asyncio.sleep(2)
        assert not (external / "orphan.txt").exists()
        assert not (external / "cancelled-orphan.txt").exists()
        await backend.start("live")
        immediate = asyncio.create_task(backend.execute("live", ["/bin/sh", "-c",
            "sleep 2; printf escaped > immediate-orphan.txt"], timeout_seconds=10))
        await asyncio.sleep(0)
        await asyncio.wait_for(backend.terminate("live"), 5)
        assert (await immediate).cancelled
        assert not (external / "immediate-orphan.txt").exists()
        await backend.start("live")
        disconnected = asyncio.create_task(backend.execute("live", ["/bin/sh", "-c",
            "printf transport-started; (sleep 2; printf escaped > transport-orphan.txt) & wait"], timeout_seconds=10))
        for _ in range(100):
            if any(event.payload.get("text") == "transport-started" for event in events):
                break
            await asyncio.sleep(0.02)
        active = backend._active["live"]
        assert active.process is not None
        active.process.kill()
        with pytest.raises(SandboxSecurityError):
            await disconnected
        await asyncio.sleep(2)
        assert not (external / "transport-orphan.txt").exists()
        reloaded = WslSandboxBackend(managed, distribution=distro)
        info = await reloaded.get("live")
        assert Path(info.workspace_path) == external
    finally:
        await backend.destroy("live")
    assert (external / "changed.txt").read_text() == "live"
    assert secret.read_text() == "must remain private"
