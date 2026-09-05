"""Live Windows workspace lifecycle and opt-in native boundary regressions."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.sandbox import (
    ResourceAccess, SandboxSecurityError, SandboxState, SandboxStateError,
    SandboxValidationError, WindowsSandboxBackend,
)
from backend.sandbox.win32 import READ_ACCESS, WRITE_DENIED_ACCESS, WindowsNativeApi
from tests.test_sandbox_backend import FakeWindowsNativeApi


class WorkspaceNativeApi(FakeWindowsNativeApi):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.workspace_grants = []
        self.workspace_revokes = []
        self.workspace_closed = []

    def validate_workspace_volume(self, path):
        pass

    def open_workspace(self, path):
        return 999

    def close_workspace(self, handle):
        self.workspace_closed.append(handle)

    def grant_workspace(self, path, sid, *, read_only, root_handle=None):
        self.workspace_grants.append((path, sid, read_only))

    def revoke_workspace(self, path, sid, *, root_handle=None):
        self.workspace_revokes.append((path, sid))


class WindowsWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "original.txt").write_text("keep", encoding="utf-8")
        self.native = WorkspaceNativeApi()
        self.backend = WindowsSandboxBackend(self.root / "managed", native_api=self.native)
        self.info = await self.backend.create("workspace")

    async def bind(self, access=ResourceAccess.READ_WRITE):
        return await self.backend.configure(
            "workspace", workspace_path=str(self.project), workspace_access=access,
        )

    async def test_binding_uses_real_cwd_but_keeps_internal_files_separate(self):
        info = await self.bind()
        self.assertEqual(info.workspace, self.project)
        self.assertEqual(info.resources_path, self.info.workspace)
        self.assertEqual(self.native.workspace_grants, [])
        await self.backend.start("workspace")
        await self.backend.execute("workspace", ["cmd.exe", "/c", "echo hello"])
        invocation = self.native.runs[-1]
        self.assertEqual(invocation["cwd"], self.project)
        self.assertEqual(invocation["environment"]["TEMP"], str(self.info.workspace / ".tmp"))
        self.assertEqual(invocation["environment"]["SANDBOX_RESOURCES"], str(self.info.workspace))
        self.assertEqual(list(self.project.iterdir()), [self.project / "original.txt"])
        await self.backend.destroy("workspace")
        self.assertEqual((self.project / "original.txt").read_text(), "keep")
        self.assertFalse(self.info.workspace.exists())
        self.assertTrue(self.native.workspace_revokes)

    async def test_attachments_never_create_files_in_user_folder(self):
        await self.bind()
        source = self.root / "managed" / "resource.txt"
        source.write_text("resource")
        await self.backend.attach_resource(
            "workspace", "file", source, "inputs/file.txt", ResourceAccess.READ_ONLY,
        )
        self.assertTrue((self.info.workspace / "inputs" / "file.txt").is_file())
        self.assertFalse((self.project / "inputs").exists())
        await self.backend.destroy("workspace")
        self.assertEqual(source.read_text(), "resource")

    async def test_permission_change_requires_stop_and_revokes_old_access(self):
        await self.bind()
        await self.backend.start("workspace")
        with self.assertRaises(SandboxStateError):
            await self.bind(ResourceAccess.READ_ONLY)
        await self.backend.terminate("workspace")
        self.assertIn((self.project, 4242), self.native.workspace_revokes)
        await self.bind(ResourceAccess.READ_ONLY)
        await self.backend.start("workspace")
        self.assertEqual(self.native.workspace_grants[-1], (self.project, 4242, True))

    async def test_restart_restores_binding_and_revokes_stale_acl(self):
        await self.bind(ResourceAccess.READ_ONLY)
        await self.backend.start("workspace")
        fresh = WindowsSandboxBackend(self.root / "managed", native_api=self.native)
        loaded = await fresh.get("workspace")
        self.assertEqual(loaded.state, SandboxState.STOPPED)
        self.assertEqual(loaded.workspace, self.project)
        self.assertEqual(loaded.workspace_access, ResourceAccess.READ_ONLY)
        self.assertIn((self.project, 4242), self.native.workspace_revokes)

    async def test_unbinding_preserves_all_user_files(self):
        await self.bind()
        unbound = await self.backend.configure(
            "workspace", workspace_path=None, workspace_access=ResourceAccess.READ_WRITE,
        )
        self.assertIsNone(unbound.workspace_path)
        self.assertEqual(unbound.workspace, self.info.workspace)
        self.assertEqual((self.project / "original.txt").read_text(), "keep")

    async def test_manifest_failure_rolls_back_binding(self):
        await self.bind()
        with patch.object(self.backend, "_write_manifest", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.backend.configure(
                    "workspace", workspace_path=None, workspace_access=ResourceAccess.READ_ONLY,
                )
        current = await self.backend.get("workspace")
        self.assertEqual(current.workspace, self.project)
        self.assertEqual(current.workspace_access, ResourceAccess.READ_WRITE)

    async def test_invalid_or_overbroad_paths_never_grant_access(self):
        for path in ("relative", str(self.root / "missing"), str(self.root), str(self.root / "managed"), str(Path.home()), str(self.project.anchor)):
            with self.subTest(path=path), self.assertRaises(SandboxValidationError):
                await self.backend.configure(
                    "workspace", workspace_path=path, workspace_access=ResourceAccess.READ_WRITE,
                )
        self.assertEqual(self.native.workspace_grants, [])

    async def test_existing_hardlinks_are_rejected_before_grant(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        os.link(outside, self.project / "linked.txt")
        with self.assertRaisesRegex(SandboxValidationError, "hard-linked"):
            await self.bind()
        self.assertEqual(self.native.workspace_grants, [])

    async def test_reparse_ancestor_or_child_is_rejected(self):
        real_is_reparse = self.backend._is_reparse
        with patch.object(self.backend, "_is_reparse", side_effect=lambda p: p == self.project or real_is_reparse(p)):
            with self.assertRaisesRegex(SandboxValidationError, "reparse"):
                await self.bind()
        with patch.object(self.backend, "_is_reparse", side_effect=lambda p: p.name == "original.txt" or real_is_reparse(p)):
            with self.assertRaisesRegex(SandboxValidationError, "reparse"):
                await self.bind()

    async def test_new_link_is_rejected_before_next_command(self):
        await self.bind()
        await self.backend.start("workspace")
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        os.link(outside, self.project / "linked.txt")
        with self.assertRaisesRegex(SandboxValidationError, "hard-linked"):
            await self.backend.execute("workspace", ["cmd.exe"])
        self.assertEqual(self.native.runs, [])

    async def test_cancellation_waits_for_process_tree_then_revokes(self):
        self.native.block_run = True
        await self.bind()
        await self.backend.start("workspace")
        task = asyncio.create_task(self.backend.execute("workspace", ["cmd.exe"]))
        self.assertTrue(await asyncio.to_thread(self.native.job_opened.wait, 1))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.backend._records["workspace"].active_job)
        self.assertEqual((await self.backend.get("workspace")).state, SandboxState.STOPPED)
        self.assertIn((self.project, 4242), self.native.workspace_revokes)

    async def test_authorization_obligation_is_persisted_until_revocation_finishes(self):
        await self.bind()
        await self.backend.start("workspace")
        manifest = self.info.workspace.parent / "sandbox.json"
        self.assertTrue(json.loads(manifest.read_text())["workspace_authorized"])
        with patch.object(self.native, "revoke_workspace", side_effect=SandboxSecurityError("ACL busy")):
            with self.assertRaisesRegex(SandboxSecurityError, "ACL busy"):
                await self.backend.terminate("workspace")
        record = self.backend._records["workspace"]
        self.assertEqual(record.state, SandboxState.ERROR)
        self.assertTrue(record.workspace_authorized)
        self.assertIsNotNone(record.workspace_handle)
        self.assertTrue(json.loads(manifest.read_text())["workspace_authorized"])
        await self.backend.terminate("workspace")
        self.assertFalse(json.loads(manifest.read_text())["workspace_authorized"])
        self.assertIsNone(record.workspace_handle)

    async def test_cancelled_start_finishes_acl_grant_and_revokes_before_returning(self):
        await self.bind()
        entered = threading.Event()
        finish = threading.Event()
        original = self.native.grant_workspace

        def slow_grant(*args, **kwargs):
            entered.set()
            finish.wait(2)
            original(*args, **kwargs)

        with patch.object(self.native, "grant_workspace", side_effect=slow_grant):
            task = asyncio.create_task(self.backend.start("workspace"))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        record = self.backend._records["workspace"]
        self.assertEqual(record.state, SandboxState.STOPPED)
        self.assertFalse(record.workspace_authorized)
        self.assertIsNone(record.workspace_handle)

    async def test_missing_or_replaced_authorized_root_is_never_reported_revoked(self):
        await self.bind()
        await self.backend.start("workspace")
        moved = self.root / "moved"
        # The fake handle permits a move that the real no-delete-sharing handle
        # blocks; it models a move after process death but before restart.
        self.project.rename(moved)
        with self.assertRaisesRegex(SandboxSecurityError, "missing"):
            await self.backend.terminate("workspace")
        self.project.mkdir()
        fresh = WindowsSandboxBackend(self.root / "managed", native_api=self.native)
        with self.assertRaisesRegex(SandboxSecurityError, "replaced"):
            await fresh.get("workspace")
        self.assertEqual(self.native.workspace_revokes, [])
        self.project.rmdir()
        moved.rename(self.project)
        await self.backend.terminate("workspace")
        self.assertIn((self.project, 4242), self.native.workspace_revokes)

    def test_read_only_deny_does_not_also_deny_read_or_synchronize(self):
        self.assertEqual(READ_ACCESS & WRITE_DENIED_ACCESS, 0)

    def test_high_output_is_bounded_for_result_and_events_but_pipe_is_drained(self):
        for payload in (b"x", b"\xff"):
            with self.subTest(payload=payload):
                chunks = [payload * 65536] * 50
                calls = []

                class Pipe:
                    def ReadFile(self, handle, buffer, size, read_pointer, unused):
                        del handle, size, unused
                        calls.append(True)
                        chunk = chunks.pop(0) if chunks else b""
                        ctypes.memmove(buffer, chunk, len(chunk))
                        ctypes.cast(read_pointer, ctypes.POINTER(ctypes.wintypes.DWORD))[0] = len(chunk)
                        return True

                native = object.__new__(WindowsNativeApi)
                native._kernel32 = Pipe()
                output, events, errors = [], [], []
                native._read_pipe(1, output, events.append, errors)
                text = "".join(output)
                self.assertEqual(errors, [])
                self.assertEqual(text, "".join(events))
                self.assertEqual(text.count(native._OUTPUT_TRUNCATED), 1)
                self.assertLessEqual(len(text.encode("utf-8")), native._MAX_OUTPUT_BYTES + len(native._OUTPUT_TRUNCATED))
                self.assertEqual(len(calls), 51)


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS") == "1",
    "set OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS=1 to exercise AppContainer",
)
class NativeWindowsWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_does_not_follow_a_junction_created_in_the_workspace(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            secret = outside / "secret.txt"
            secret.write_text("secret")
            before = subprocess.run(["icacls.exe", str(secret)], capture_output=True).stdout
            backend = WindowsSandboxBackend(root / "managed")
            await backend.create("native-junction")
            try:
                await backend.configure(
                    "native-junction", workspace_path=str(project), workspace_access=ResourceAccess.READ_WRITE,
                )
                await backend.start("native-junction")
                junction = project / "escape"
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/j", str(junction), str(outside)],
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                with self.assertRaisesRegex(SandboxValidationError, "reparse"):
                    await backend.execute("native-junction", ["cmd.exe", "/d", "/c", "type escape\\secret.txt"])
                await backend.terminate("native-junction")
                after = subprocess.run(["icacls.exe", str(secret)], capture_output=True).stdout
                self.assertEqual(before, after)
                self.assertEqual(secret.read_text(), "secret")
            finally:
                await backend.destroy("native-junction")
            self.assertTrue(junction.exists())
            self.assertTrue(secret.exists())

    async def test_live_write_readonly_host_denial_and_complete_sid_revocation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            child = project / "nested"
            child.mkdir(parents=True)
            original = child / "original.txt"
            original.write_text("original")
            sibling = root / "host-secret.txt"
            sibling.write_text("host-secret")
            backend = WindowsSandboxBackend(root / "managed")
            created = await backend.create("native-workspace")
            try:
                await backend.configure(
                    "native-workspace", workspace_path=str(project), workspace_access=ResourceAccess.READ_WRITE,
                )
                profile = backend._records["native-workspace"].profile
                convert = backend._native._advapi32.ConvertSidToStringSidW
                convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
                convert.restype = ctypes.c_int
                pointer = ctypes.c_void_p()
                self.assertTrue(convert(profile.sid, ctypes.byref(pointer)))
                try:
                    sid = ctypes.wstring_at(pointer.value)
                finally:
                    backend._native._kernel32.LocalFree(pointer)
                await backend.start("native-workspace")
                with self.assertRaises(PermissionError):
                    project.rename(root / "moved")
                write = await backend.execute(
                    "native-workspace", ["cmd.exe", "/d", "/c", "echo edited>nested\\original.txt & echo created>new.txt"],
                )
                self.assertEqual(write.exit_code, 0, write.stderr)
                self.assertEqual(original.read_text().strip(), "edited")
                self.assertEqual((project / "new.txt").read_text().strip(), "created")
                denied = await backend.execute(
                    "native-workspace", ["cmd.exe", "/d", "/c", f'type "{sibling}"'],
                )
                self.assertNotEqual(denied.exit_code, 0)
                self.assertNotIn("host-secret", denied.stdout)
                await backend.terminate("native-workspace")
                for path in (project, child, original, project / "new.txt"):
                    acl = subprocess.run(["icacls.exe", str(path)], capture_output=True).stdout.decode(errors="replace")
                    self.assertNotIn(sid, acl, f"stale AppContainer grant: {path}: {acl}")
                    self.assertNotIn("OpenAgentWorld.", acl, f"stale named AppContainer grant: {path}: {acl}")
                await backend.configure(
                    "native-workspace", workspace_path=str(project), workspace_access=ResourceAccess.READ_ONLY,
                )
                await backend.start("native-workspace")
                read = await backend.execute(
                    "native-workspace", ["cmd.exe", "/d", "/c", "type nested\\original.txt"],
                )
                self.assertEqual(read.exit_code, 0, read.stderr)
                self.assertIn("edited", read.stdout)
                failed_write = await backend.execute(
                    "native-workspace", ["cmd.exe", "/d", "/c", "echo forbidden>nested\\original.txt"],
                )
                self.assertNotEqual(failed_write.exit_code, 0)
                self.assertEqual(original.read_text().strip(), "edited")
                self.assertFalse((project / ".tmp").exists())
            finally:
                await backend.destroy("native-workspace")
            self.assertEqual(original.read_text().strip(), "edited")
            self.assertTrue((project / "new.txt").exists())
            self.assertFalse(created.workspace.exists())
