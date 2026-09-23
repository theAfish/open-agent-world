"""Unit tests for the macOS Seatbelt sandbox backend.

Native integration requires a macOS host; these tests verify host-side state
management, profile generation, and validation logic on any OS.
"""
from pathlib import Path

import pytest
import json
import os
import sys

from backend.sandbox.darwin_seatbelt import (
    DarwinSeatbeltBackend,
    seatbelt_profile,
    minimal_darwin_environment,
    validate_argv,
    validate_relative_path,
    _sbpl_quote,
)
from backend.sandbox.commands import needs_managed_python
from backend.sandbox.models import (
    ResourceAccess, ResourceAttachment, SandboxNotFoundError,
    SandboxState, SandboxStateError, SandboxValidationError,
)


def make_backend(tmp_path: Path) -> DarwinSeatbeltBackend:
    return DarwinSeatbeltBackend(
        tmp_path / "managed",
        runtime_id="darwin-test",
        sandbox_exec="/nonexistent/sandbox-exec",
    )


@pytest.mark.parametrize("argv,mode,skill,expected", [
    (["/usr/bin/curl", "https://example.com"], "auto", False, False),
    (["python3", "script.py"], "auto", False, True),
    (["/usr/bin/python3", "-c", "print(1)"], "auto", False, True),
    (["/opt/homebrew/bin/python3", "-c", "print(1)"], "auto", False, False),
    (["python3.12", "-c", "print(1)"], "auto", False, False),
    (["/bin/zsh", "-c", "python script.py"], "auto", False, False),
    (["/bin/zsh", "-c", "python script.py"], "managed", False, True),
    (["/bin/zsh", "-c", "python script.py"], "none", False, False),
    (["/bin/sh", "scripts/check.sh"], "auto", True, True),
])
def test_managed_python_selection(argv, mode, skill, expected):
    assert needs_managed_python(argv, mode, skill=skill) is expected


class TestProfileGeneration:
    def test_profile_denies_default(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), ())
        assert "(deny default)" in profile
        assert "(allow network-outbound" not in profile

    def test_profile_allows_only_proxy_ports(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), (),
            proxy_ports=(12345, 23456))
        assert '(allow network-outbound (remote ip "localhost:12345"))' in profile
        assert '(allow network-outbound (remote ip "localhost:23456"))' in profile
        assert "(allow network-bind" not in profile
        assert "(allow network-outbound)" not in profile

    def test_profile_allows_metadata_globally(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), ())
        assert "(allow file-read-metadata)" in profile
        assert "(allow file-ioctl)" in profile
        assert "(allow sysctl-read)" in profile
        assert "(allow posix-shm)" not in profile

    def test_profile_allows_system_reads(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), ())
        assert '(subpath "/bin")' in profile
        assert '(subpath "/usr/bin")' in profile
        assert '(subpath "/System")' in profile
        assert '(literal "/")' in profile
        assert '(literal "/dev/null")' in profile

    def test_profile_allows_standard_stream_paths(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), ())
        assert '(literal "/dev/stdout")' in profile
        assert '(literal "/dev/stderr")' in profile
        assert '(literal "/dev/fd/1")' in profile

    def test_profile_allows_managed_python_base_and_venv_read_only(self):
        base = Path("/managed/runtime/python/base")
        venv = Path("/managed/runtime/python/venv")
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), (),
            python_runtime=(base, venv))
        read_section, write_section = profile.split("(allow file-write*", 1)
        assert f'(subpath "{base}")' in read_section
        assert f'(subpath "{venv}")' in read_section
        assert str(base) not in write_section
        assert str(venv) not in write_section

    def test_profile_exposes_resource_view_read_only(self):
        root = Path("/data/sandbox")
        resources = root / "resources"
        profile = seatbelt_profile(
            root, root / "workspace", root / "home", root / "tmp", ())
        read_section, write_section = profile.split("(allow file-write*", 1)
        assert f'(subpath "{resources}")' in read_section
        assert str(resources) not in write_section

    def test_probe_profile_has_runtime_library_paths_but_not_host_etc(self):
        profile = DarwinSeatbeltBackend._probe_profile()
        assert '(subpath "/usr/lib")' in profile
        assert '(subpath "/System")' in profile
        assert '(literal "/")' in profile
        assert '(subpath "/private/etc")' not in profile

    def test_profile_writable_paths(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), ())
        assert '(subpath "/data/sandbox/workspace")' in profile
        assert '(subpath "/data/sandbox/home")' in profile
        assert '(subpath "/data/sandbox/tmp")' in profile

    def test_profile_keeps_read_only_workspace_out_of_write_rules(self):
        workspace = Path("/data/sandbox/workspace")
        profile = seatbelt_profile(
            Path("/data/sandbox"), workspace,
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), (),
            workspace_access=ResourceAccess.READ_ONLY)
        read_section, write_section = profile.split("(allow file-write*", 1)
        assert f'(subpath "{workspace}")' in read_section
        assert str(workspace) not in write_section
        assert "/data/sandbox/home" in write_section
        assert "/data/sandbox/tmp" in write_section

    def test_profile_attachment_read_only(self):
        attachment = ResourceAttachment("lab", "res1", Path("/data/resource.txt"),
            "file.txt", ResourceAccess.READ_ONLY)
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), (attachment,))
        assert '(subpath "/data/resource.txt")' in profile
        # Read-only attachments must not appear in the write section.
        write_section = profile.split("(allow file-write*")[1]
        assert "/data/resource.txt" not in write_section

    def test_profile_attachment_read_write(self):
        attachment = ResourceAttachment("lab", "res1", Path("/data/resource.txt"),
            "file.txt", ResourceAccess.READ_WRITE)
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), (attachment,))
        assert '(subpath "/data/resource.txt")' in profile
        write_section = profile.split("(allow file-write*")[1]
        assert '(subpath "/data/resource.txt")' in write_section

    def test_profile_does_not_allow_users(self):
        profile = seatbelt_profile(
            Path("/data/sandbox"), Path("/data/sandbox/workspace"),
            Path("/data/sandbox/home"), Path("/data/sandbox/tmp"), ())
        assert '(subpath "/Users")' not in profile
        assert '(subpath "/private/var")' not in profile or "(subpath \"/private/var/db/timezone\")" in profile

    def test_profile_escapes_quotes(self):
        assert _sbpl_quote('/path/to "quoted"') == '"/path/to \\"quoted\\""'
        assert _sbpl_quote("/path/back\\slash") == '"/path/back\\\\slash"'


def test_watchdog_wraps_only_the_generated_seatbelt_command(tmp_path):
    backend = make_backend(tmp_path)
    wrapped = backend._watchdog_command("(version 1)\n(deny default)", ("/bin/echo", "ok"))
    assert wrapped[:3] == (sys.executable, "-I", "-c")
    sandboxed = json.loads(wrapped[-1])
    assert sandboxed[:3] == ["/nonexistent/sandbox-exec", "-p", "(version 1)\n(deny default)"]
    assert sandboxed[-2:] == ["/bin/echo", "ok"]


class TestMinimalEnvironment:
    def test_basic_paths(self, tmp_path):
        env = minimal_darwin_environment(
            tmp_path / "home", tmp_path / "tmp", resources=tmp_path / "resources")
        assert env["HOME"] == str(tmp_path / "home")
        assert env["TMPDIR"] == str(tmp_path / "tmp")
        assert "/usr/bin" in env["PATH"]
        assert "/opt/homebrew/bin" in env["PATH"]
        assert env["SANDBOX_RESOURCES"] == str(tmp_path / "resources")

    def test_allowlist(self, tmp_path):
        env = minimal_darwin_environment(
            tmp_path / "home", tmp_path / "tmp", {"TZ": "Asia/Shanghai"})
        assert env["TZ"] == "Asia/Shanghai"
        with pytest.raises(SandboxValidationError):
            minimal_darwin_environment(
                tmp_path / "home", tmp_path / "tmp", {"SECRET_KEY": "no"})


class TestValidation:
    def test_validate_argv(self):
        assert validate_argv(["echo", "ok"]) == ("echo", "ok")
        with pytest.raises(SandboxValidationError):
            validate_argv("echo hello")
        with pytest.raises(SandboxValidationError):
            validate_argv([])
        with pytest.raises(SandboxValidationError):
            validate_argv(["echo", "bad\0null"])

    def test_validate_relative_path(self):
        assert validate_relative_path("dir/file.txt") == "dir/file.txt"
        with pytest.raises(SandboxValidationError):
            validate_relative_path("../escape")
        with pytest.raises(SandboxValidationError):
            validate_relative_path("/absolute")
        with pytest.raises(SandboxValidationError):
            validate_relative_path("a\\b")


class TestBackendLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_path):
        backend = make_backend(tmp_path)
        info = await backend.create("lab")
        assert info.state == SandboxState.STOPPED
        assert info.runtime_id == "darwin-test"
        assert info.security_boundary == "macos-seatbelt"
        assert info.platform == "macos"
        assert info.shell == ("/bin/zsh", "-c")
        assert info.supported_network_modes == ("disabled", "enabled")
        assert info.network_transport == "proxy_tcp"

    @pytest.mark.asyncio
    async def test_duplicate_create_rejected(self, tmp_path):
        backend = make_backend(tmp_path)
        await backend.create("lab")
        with pytest.raises(SandboxStateError, match="already exists"):
            await backend.create("lab")

    @pytest.mark.asyncio
    async def test_missing_not_found(self, tmp_path):
        backend = make_backend(tmp_path)
        with pytest.raises(SandboxNotFoundError):
            await backend.get("missing")

    @pytest.mark.asyncio
    async def test_execute_without_start_rejected(self, tmp_path):
        backend = make_backend(tmp_path)
        await backend.create("lab")
        with pytest.raises(SandboxStateError, match="ready"):
            await backend.execute("lab", ["echo", "hi"])

    @pytest.mark.asyncio
    async def test_network_policy_validation(self, tmp_path):
        backend = make_backend(tmp_path)
        backend.validate_execution_policy({"network_enabled": True})
        with pytest.raises(SandboxValidationError, match="boolean"):
            backend.validate_execution_policy({"network_enabled": "yes"})

    @pytest.mark.asyncio
    async def test_custom_process_or_memory_limits_are_rejected(self, tmp_path, monkeypatch):
        backend = make_backend(tmp_path)
        monkeypatch.setattr("backend.sandbox.darwin_seatbelt.sys.platform", "darwin")
        monkeypatch.setattr(Path, "is_file", lambda self: True)
        policies = (
            {"memory_bytes": 1024 * 1024 * 1024},
            {"active_process_limit": 32},
        )
        for index, policy in enumerate(policies):
            sandbox_id = f"lab-{index}"
            await backend.create(sandbox_id)
            await backend.start(sandbox_id)
            with pytest.raises(SandboxValidationError, match="Container VM"):
                await backend.execute(sandbox_id, ["echo", "hi"],
                    execution_policy=policy)

    @pytest.mark.asyncio
    async def test_terminate_transitions_to_stopped(self, tmp_path):
        backend = make_backend(tmp_path)
        await backend.create("lab")
        await backend.terminate("lab")
        info = await backend.get("lab")
        assert info.state == SandboxState.STOPPED

    @pytest.mark.asyncio
    async def test_destroy_removes_storage(self, tmp_path):
        backend = make_backend(tmp_path)
        await backend.create("lab")
        root = tmp_path / "managed" / "sandbox-runtimes"
        sandbox_dir = next(root.iterdir()) / "sandboxes" / "lab"
        assert sandbox_dir.is_dir()
        await backend.destroy("lab")
        assert not sandbox_dir.exists()
        with pytest.raises(SandboxNotFoundError):
            await backend.get("lab")

    @pytest.mark.asyncio
    async def test_attach_detach_resource(self, tmp_path):
        backend = make_backend(tmp_path)
        await backend.create("lab")
        source = tmp_path / "managed" / "resource.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("data")
        attachment = await backend.attach_resource(
            "lab", "res1", source, "data/file.txt", ResourceAccess.READ_ONLY)
        assert attachment.access == ResourceAccess.READ_ONLY
        info = await backend.get("lab")
        assert len(info.attachments) == 1
        await backend.detach_resource("lab", "res1")
        info = await backend.get("lab")
        assert len(info.attachments) == 0

    @pytest.mark.asyncio
    async def test_attach_conflict_rejected(self, tmp_path):
        backend = make_backend(tmp_path)
        await backend.create("lab")
        source = tmp_path / "managed" / "resource.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("data")
        await backend.attach_resource(
            "lab", "res1", source, "dir/file.txt", ResourceAccess.READ_ONLY)
        with pytest.raises(SandboxValidationError, match="conflicts"):
            await backend.attach_resource(
                "lab", "res2", source, "dir/file.txt", ResourceAccess.READ_WRITE)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS") != "1",
    reason="set OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS=1 on macOS",
)
@pytest.mark.asyncio
async def test_native_seatbelt_read_only_workspace_and_attachment_view(tmp_path):
    managed = tmp_path / "managed"
    source = managed / "artifacts" / "payload.txt"
    source.parent.mkdir(parents=True)
    source.write_text("attachment-ok", encoding="utf-8")
    backend = DarwinSeatbeltBackend(managed)
    available, reason = await backend.probe()
    assert available, reason
    await backend.create("native")
    await backend.configure("native", workspace_path=None,
        workspace_access=ResourceAccess.READ_ONLY)
    await backend.attach_resource(
        "native", "payload", source, "data/payload.txt", ResourceAccess.READ_ONLY)
    await backend.start("native")
    result = await backend.execute("native", ["/bin/sh", "-c",
        'cat "$SANDBOX_RESOURCES/data/payload.txt"; printf denied > blocked.txt'])
    assert "attachment-ok" in result.stdout
    assert result.exit_code != 0
    assert not list((managed / "sandbox-runtimes").rglob("blocked.txt"))


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS") != "1",
    reason="set OAW_RUN_NATIVE_MACOS_SANDBOX_TESTS=1 on macOS",
)
@pytest.mark.asyncio
async def test_native_seatbelt_proxy_profile_probe():
    available, reason = await DarwinSeatbeltBackend.probe()
    assert available, reason
    network_available, network_reason = await DarwinSeatbeltBackend.probe_network()
    assert network_available, network_reason
