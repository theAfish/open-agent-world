from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.main import create_app
from backend.sandbox import (
    ResourceAccess,
    SandboxLimits,
    SandboxSecurityError,
    SandboxValidationError,
    WindowsSandboxBackend,
)
from backend.sandbox.environment import minimal_windows_environment
from backend.sandbox.win32 import (
    AppContainerProfile,
    NativeCommandResult,
    WindowsNativeApi,
)
from backend.services import create_services


class FakeWindowsNativeApi:
    def __init__(self, *, fail_profile: bool = False) -> None:
        self.fail_profile = fail_profile
        self.grants: list[tuple[Path, bool]] = []
        self.revocations: list[Path] = []
        self.protected: list[Path] = []
        self.unprotected: list[Path] = []
        self.deleted_profiles: list[str] = []
        self.terminated_jobs: list[int] = []
        self.last_argv: tuple[str, ...] = ()
        self.last_environment: dict[str, str] = {}

    def ensure_appcontainer(self, name: str) -> AppContainerProfile:
        if self.fail_profile:
            raise SandboxSecurityError("AppContainer profile creation failed")
        return AppContainerProfile(name=name, sid=0xA71A5)

    def grant_path(self, path: Path, sid: int, *, read_only: bool) -> None:
        assert sid == 0xA71A5
        self.grants.append((Path(path), read_only))

    def revoke_path(self, path: Path, sid: int) -> None:
        assert sid == 0xA71A5
        self.revocations.append(Path(path))

    def protect_path_acl(self, path: Path) -> None:
        self.protected.append(Path(path))

    def unprotect_path_acl(self, path: Path) -> None:
        self.unprotected.append(Path(path))

    def free_appcontainer_sid(self, profile: AppContainerProfile) -> None:
        assert profile.sid == 0xA71A5

    def delete_appcontainer(self, name: str) -> None:
        self.deleted_profiles.append(name)

    def terminate_job(self, job_handle: int) -> None:
        self.terminated_jobs.append(job_handle)

    def run_appcontainer(
        self,
        profile: AppContainerProfile,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        limits: SandboxLimits,
        timeout_seconds: float,
        cancel_event: threading.Event,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
        on_job_open: Callable[[int], None],
        on_job_close: Callable[[], None],
    ) -> NativeCommandResult:
        del profile, limits, timeout_seconds, cancel_event
        self.last_argv = tuple(argv)
        self.last_environment = dict(environment)
        on_job_open(41)
        try:
            if any("write-mounted" in item for item in argv):
                (cwd / "resources" / "notes.txt").write_text(
                    "changed in sandbox", encoding="utf-8"
                )
            on_stdout("sandbox output\n")
            on_stderr("")
            return NativeCommandResult(
                exit_code=0,
                stdout="sandbox output\n",
                stderr="",
                duration_seconds=0.01,
                timed_out=False,
                cancelled=False,
            )
        finally:
            on_job_close()


def _backend(root: Path, native: FakeWindowsNativeApi) -> WindowsSandboxBackend:
    return WindowsSandboxBackend(root, native_api=native)  # type: ignore[arg-type]


def test_minimal_environment_never_inherits_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "host-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "host-secret")
    environment = minimal_windows_environment(tmp_path / "workspace")
    assert "GOOGLE_API_KEY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "SSH_AUTH_SOCK" not in environment
    assert set(environment) == {
        "COMSPEC",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    with pytest.raises(SandboxValidationError, match="allowlist"):
        minimal_windows_environment(
            tmp_path / "workspace", {"GOOGLE_API_KEY": "must-not-cross"}
        )


@pytest.mark.asyncio
async def test_windows_backend_lifecycle_mounts_and_fail_closed_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed"
    source = root / "assets" / "text" / "notes.txt"
    source.parent.mkdir(parents=True)
    source.write_text("before", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("host secret", encoding="utf-8")
    native = FakeWindowsNativeApi()
    events: list[Any] = []

    async def sink(event: Any) -> None:
        events.append(event)

    backend = WindowsSandboxBackend(
        root, event_sink=sink, native_api=native  # type: ignore[arg-type]
    )
    created = await backend.create("lab-1")
    assert created.network_enabled is False
    assert created.security_boundary == "windows-appcontainer+ntfs-acl+job-object"
    await backend.attach_resource(
        "lab-1", "notes", source, r"resources\notes.txt", ResourceAccess.READ_ONLY
    )
    assert (created.workspace / "resources" / "notes.txt").is_file()
    assert native.grants[-1] == (source.resolve(), True)
    assert native.protected[-1] == source.resolve()

    with pytest.raises(SandboxValidationError, match="managed storage"):
        await backend.attach_resource(
            "lab-1", "outside", outside, "outside.txt", ResourceAccess.READ_ONLY
        )

    await backend.start("lab-1")
    with pytest.raises(SandboxValidationError, match="sequence"):
        await backend.execute("lab-1", "cmd.exe /c echo unsafe")
    with pytest.raises(SandboxValidationError, match="allowlist"):
        await backend.execute(
            "lab-1", ["cmd.exe", "/c", "echo"], env={"GOOGLE_API_KEY": "secret"}
        )
    await backend.start("lab-1")
    result = await backend.execute(
        "lab-1", ["cmd.exe", "/d", "/c", "echo", "ok"], env={"PYTHONUTF8": "1"}
    )
    assert result.exit_code == 0
    assert result.stdout == "sandbox output\n"
    assert "GOOGLE_API_KEY" not in native.last_environment
    assert native.last_environment["PYTHONUTF8"] == "1"
    assert any(event.type.value == "sandbox_stdout" for event in events)

    await backend.detach_resource("lab-1", "notes")
    assert not (created.workspace / "resources" / "notes.txt").exists()
    await backend.destroy("lab-1")
    assert not created.workspace.parent.exists()
    assert native.deleted_profiles


@pytest.mark.asyncio
async def test_secure_creation_failure_leaves_no_executable_sandbox(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed"
    backend = _backend(root, FakeWindowsNativeApi(fail_profile=True))
    with pytest.raises(SandboxSecurityError, match="profile creation failed"):
        await backend.create("broken")
    assert not (root / "sandboxes" / "broken").exists()
    with pytest.raises(Exception, match="not found"):
        await backend.get("broken")


def test_native_launcher_has_no_ordinary_subprocess_fallback() -> None:
    import inspect

    source = inspect.getsource(WindowsNativeApi.run_appcontainer)
    assert "subprocess.Popen" not in source
    assert "CreateProcessW" in source
    assert "AssignProcessToJobObject" in source
    assert "ResumeThread" in source


def test_sandbox_api_mount_write_sync_and_revocation(data_root: Path) -> None:
    native = FakeWindowsNativeApi()
    backend = _backend(data_root, native)
    settings = Settings.for_data_root(data_root)
    services = create_services(settings, sandbox_backend=backend)
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client:
            text = client.post(
                "/api/nodes",
                json={
                    "type": "text",
                    "config": {"filename": "notes.txt"},
                    "content": "before",
                },
            ).json()
            sandbox = client.post(
                "/api/nodes", json={"type": "sandbox", "name": "North lab"}
            ).json()
            edge = client.post(
                "/api/edges",
                json={
                    "source": text["id"],
                    "target": sandbox["id"],
                    "relationship": "mount_read_write",
                },
            )
            assert edge.status_code == 201
            assert client.post(f"/api/sandboxes/{sandbox['id']}/start").status_code == 200

            executed = client.post(
                f"/api/sandboxes/{sandbox['id']}/execute",
                json={"argv": ["fake.exe", "write-mounted"]},
            )
            assert executed.status_code == 200, executed.text
            document = client.get(f"/api/resources/{text['id']}/text").json()
            assert document["content"] == "changed in sandbox"
            assert document["revision"] == 2

            command = client.post(
                f"/api/sandboxes/{sandbox['id']}/execute",
                json={"command": "dir"},
            )
            assert command.status_code == 200
            assert native.last_argv[-2:] == ("/c", "dir")

            assert client.delete(f"/api/edges/{edge.json()['id']}").status_code == 200
            workspace = Path(client.get(f"/api/sandboxes/{sandbox['id']}").json()["workspace"])
            assert not (workspace / "resources" / "notes.txt").exists()
            with pytest.raises(PermissionDeniedError):
                services.capabilities.require_sandbox_resource(
                    sandbox["id"], text["id"]
                )
            assert client.post(f"/api/sandboxes/{sandbox['id']}/stop").status_code == 200
    finally:
        services.close()
