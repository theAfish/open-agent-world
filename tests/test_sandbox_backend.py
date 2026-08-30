from __future__ import annotations

import asyncio
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.sandbox import (
    ResourceAccess,
    SandboxEventType,
    SandboxSecurityError,
    SandboxState,
    SandboxStateError,
    SandboxValidationError,
    WindowsSandboxBackend,
)
from backend.sandbox.environment import minimal_windows_environment
from backend.sandbox.win32 import AppContainerProfile, NativeCommandResult


class FakeWindowsNativeApi:
    def __init__(self, *, fail_profile: bool = False, block_run: bool = False) -> None:
        self.fail_profile = fail_profile
        self.block_run = block_run
        self.grants: list[tuple[Path, int, bool]] = []
        self.revokes: list[tuple[Path, int]] = []
        self.protected: list[Path] = []
        self.unprotected: list[Path] = []
        self.deleted: list[str] = []
        self.runs: list[dict[str, object]] = []
        self.job_opened = threading.Event()

    def ensure_appcontainer(self, identity: str) -> AppContainerProfile:
        if self.fail_profile:
            raise SandboxSecurityError("profile creation denied")
        return AppContainerProfile(identity, 4242)

    def free_appcontainer_sid(self, profile: AppContainerProfile) -> None:
        pass

    def delete_appcontainer(self, identity: str) -> None:
        self.deleted.append(identity)

    def grant_path(self, path: Path, sid: int, *, read_only: bool) -> None:
        self.grants.append((Path(path), sid, read_only))

    def revoke_path(self, path: Path, sid: int) -> None:
        self.revokes.append((Path(path), sid))

    def protect_path_acl(self, path: Path) -> None:
        self.protected.append(Path(path))

    def unprotect_path_acl(self, path: Path) -> None:
        self.unprotected.append(Path(path))

    def terminate_job(self, job_handle: int) -> None:
        pass

    def run_appcontainer(self, profile, argv, **kwargs) -> NativeCommandResult:
        self.runs.append({"profile": profile, "argv": tuple(argv), **kwargs})
        kwargs["on_job_open"](73)
        self.job_opened.set()
        kwargs["on_stdout"]("sandbox output\n")
        if self.block_run:
            deadline = time.monotonic() + 3
            while not kwargs["cancel_event"].is_set() and time.monotonic() < deadline:
                time.sleep(0.005)
        cancelled = kwargs["cancel_event"].is_set()
        kwargs["on_job_close"]()
        return NativeCommandResult(
            exit_code=0 if not cancelled else 0xC000013A,
            stdout="sandbox output\n",
            stderr="",
            duration_seconds=0.01,
            timed_out=False,
            cancelled=cancelled,
        )


class WindowsSandboxBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.native = FakeWindowsNativeApi()
        self.events = []
        self.backend = WindowsSandboxBackend(
            self.root, native_api=self.native, event_sink=self.events.append
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_secure_creation_failure_is_fail_closed(self) -> None:
        backend = WindowsSandboxBackend(
            self.root / "failed", native_api=FakeWindowsNativeApi(fail_profile=True)
        )
        with self.assertRaises(SandboxSecurityError):
            await backend.create("broken")
        self.assertFalse((self.root / "failed" / "sandboxes" / "broken").exists())
        with self.assertRaises(Exception):
            await backend.get("broken")

    async def test_lifecycle_execution_and_scrubbed_environment(self) -> None:
        created = await self.backend.create("lab")
        self.assertEqual(created.state, SandboxState.STOPPED)
        self.assertFalse(created.network_enabled)
        with self.assertRaises(SandboxStateError):
            await self.backend.execute("lab", ["cmd.exe", "/c", "echo no"])

        ready = await self.backend.start("lab")
        self.assertEqual(ready.state, SandboxState.READY)
        old = os.environ.get("GOOGLE_API_KEY")
        os.environ["GOOGLE_API_KEY"] = "must-not-leak"
        try:
            result = await self.backend.execute(
                "lab", ["cmd.exe", "/d", "/c", "echo yes"], env={"LANG": "C"}
            )
        finally:
            if old is None:
                os.environ.pop("GOOGLE_API_KEY", None)
            else:
                os.environ["GOOGLE_API_KEY"] = old
        self.assertEqual(result.stdout, "sandbox output\n")
        environment = self.native.runs[-1]["environment"]
        self.assertNotIn("GOOGLE_API_KEY", environment)
        self.assertEqual(environment["LANG"], "C")
        self.assertEqual((await self.backend.get("lab")).state, SandboxState.READY)
        self.assertIn(SandboxEventType.STDOUT, [event.type for event in self.events])

    async def test_never_accepts_shell_strings_or_secret_environment_names(self) -> None:
        await self.backend.create("lab")
        await self.backend.start("lab")
        with self.assertRaises(SandboxValidationError):
            await self.backend.execute("lab", "cmd.exe /c whoami")
        with self.assertRaises(SandboxValidationError):
            await self.backend.execute(
                "lab", ["cmd.exe"], env={"AWS_SECRET_ACCESS_KEY": "secret"}
            )

    async def test_read_only_and_read_write_hard_link_attachments(self) -> None:
        source_ro = self.assets / "read.txt"
        source_ro.write_text("original", encoding="utf-8")
        source_rw = self.assets / "write.txt"
        source_rw.write_text("before", encoding="utf-8")
        info = await self.backend.create("lab")

        await self.backend.attach_resource(
            "lab", "read", source_ro, "inputs/read.txt", ResourceAccess.READ_ONLY
        )
        await self.backend.attach_resource(
            "lab", "write", source_rw, "outputs/write.txt", ResourceAccess.READ_WRITE
        )
        self.assertIn((source_ro.resolve(), 4242, True), self.native.grants)
        self.assertIn((source_rw.resolve(), 4242, False), self.native.grants)
        mounted_rw = info.workspace / "outputs" / "write.txt"
        self.assertTrue(os.path.samefile(source_rw, mounted_rw))
        mounted_rw.write_text("after", encoding="utf-8")
        self.assertEqual(source_rw.read_text(encoding="utf-8"), "after")

        await self.backend.detach_resource("lab", "read")
        self.assertFalse((info.workspace / "inputs" / "read.txt").exists())
        self.assertIn((source_ro.resolve(), 4242), self.native.revokes)

    async def test_path_traversal_and_unmanaged_sources_are_rejected(self) -> None:
        outside_dir = TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside = Path(outside_dir.name) / "host.txt"
        outside.write_text("host", encoding="utf-8")
        source = self.assets / "safe.txt"
        source.write_text("safe", encoding="utf-8")
        await self.backend.create("lab")
        with self.assertRaises(SandboxValidationError):
            await self.backend.attach_resource(
                "lab", "x", outside, "host.txt", ResourceAccess.READ_ONLY
            )
        with self.assertRaises(SandboxValidationError):
            await self.backend.attach_resource(
                "lab", "x", source, "../escape.txt", ResourceAccess.READ_ONLY
            )
        with self.assertRaises(SandboxValidationError):
            await self.backend.attach_resource(
                "lab", "x", source, r"C:\escape.txt", ResourceAccess.READ_ONLY
            )

    async def test_stop_cancels_complete_native_job_and_leaves_stopped(self) -> None:
        native = FakeWindowsNativeApi(block_run=True)
        backend = WindowsSandboxBackend(self.root / "blocking", native_api=native)
        await backend.create("lab")
        await backend.start("lab")
        command_task = asyncio.create_task(backend.execute("lab", ["cmd.exe"]))
        opened = await asyncio.to_thread(native.job_opened.wait, 1)
        self.assertTrue(opened)
        await backend.terminate("lab")
        result = await command_task
        self.assertTrue(result.cancelled)
        self.assertEqual((await backend.get("lab")).state, SandboxState.STOPPED)

    async def test_manifest_reload_does_not_restore_running_state(self) -> None:
        await self.backend.create("persistent")
        await self.backend.start("persistent")
        reloaded = WindowsSandboxBackend(self.root, native_api=self.native)
        info = await reloaded.get("persistent")
        self.assertEqual(info.state, SandboxState.STOPPED)

    async def test_destroy_revokes_identity_and_storage(self) -> None:
        source = self.assets / "file.txt"
        source.write_text("data", encoding="utf-8")
        info = await self.backend.create("lab")
        await self.backend.attach_resource(
            "lab", "file", source, "file.txt", ResourceAccess.READ_ONLY
        )
        await self.backend.destroy("lab")
        self.assertFalse(info.workspace.parent.exists())
        self.assertTrue(self.native.deleted)


class MinimalEnvironmentTests(unittest.TestCase):
    def test_environment_is_synthesized_not_inherited(self) -> None:
        env = minimal_windows_environment(
            Path(r"C:\managed\workspace"), windows_directory=Path(r"C:\Windows")
        )
        self.assertEqual(
            set(env),
            {
                "COMSPEC",
                "LOCALAPPDATA",
                "PATH",
                "PATHEXT",
                "SystemRoot",
                "TEMP",
                "TMP",
                "WINDIR",
            },
        )


if __name__ == "__main__":
    unittest.main()
