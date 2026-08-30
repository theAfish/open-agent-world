"""Opt-in destructive-to-temporary-state Windows isolation smoke test.

Set ``OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS=1`` on a Windows development
machine.  Unit tests use a fake native adapter so CI never treats an ordinary
subprocess as a substitute for the security boundary.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.sandbox import ResourceAccess, SandboxState, WindowsSandboxBackend


@unittest.skipUnless(
    os.name == "nt"
    and os.environ.get("OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS") == "1",
    "set OPEN_AGENT_WORLD_RUN_NATIVE_SANDBOX_TESTS=1 to exercise AppContainer",
)
class NativeWindowsSandboxSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_appcontainer_can_only_write_managed_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            backend = WindowsSandboxBackend(Path(directory))
            info = await backend.create("native-smoke")
            try:
                await backend.start("native-smoke")
                result = await backend.execute(
                    "native-smoke",
                    [
                        "cmd.exe",
                        "/d",
                        "/s",
                        "/c",
                        "echo isolated>proof.txt & type proof.txt",
                    ],
                    timeout_seconds=10,
                )
                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertIn("isolated", result.stdout)
                self.assertEqual(
                    (info.workspace / "proof.txt").read_text().strip(), "isolated"
                )
                self.assertEqual(
                    (await backend.get("native-smoke")).state, SandboxState.READY
                )
            finally:
                await backend.destroy("native-smoke")

    async def test_native_attachment_acl_modes_and_revocation(self) -> None:
        with TemporaryDirectory() as directory:
            managed = Path(directory)
            assets = managed / "assets"
            assets.mkdir()
            read_only = assets / "read-only.txt"
            read_only.write_text("read-only", encoding="utf-8")
            read_write = assets / "read-write.txt"
            read_write.write_text("before", encoding="utf-8")
            backend = WindowsSandboxBackend(managed)
            info = await backend.create("native-mounts")
            try:
                await backend.attach_resource(
                    "native-mounts",
                    "read-only",
                    read_only,
                    "inputs/read-only.txt",
                    ResourceAccess.READ_ONLY,
                )
                await backend.attach_resource(
                    "native-mounts",
                    "read-write",
                    read_write,
                    "outputs/read-write.txt",
                    ResourceAccess.READ_WRITE,
                )
                await backend.start("native-mounts")
                denied = await backend.execute(
                    "native-mounts",
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "echo changed>>inputs\\read-only.txt",
                    ],
                )
                acl = subprocess.run(
                    ["icacls.exe", str(read_only)], capture_output=True, text=True
                ).stdout
                self.assertEqual(
                    read_only.read_text(encoding="utf-8"), "read-only", acl
                )
                self.assertIn("denied", (denied.stdout + denied.stderr).lower())
                allowed = await backend.execute(
                    "native-mounts",
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "echo after>>outputs\\read-write.txt",
                    ],
                )
                self.assertEqual(allowed.exit_code, 0, allowed.stderr)
                self.assertIn("after", read_write.read_text(encoding="utf-8"))

                await backend.detach_resource("native-mounts", "read-only")
                await backend.start("native-mounts")
                detached = await backend.execute(
                    "native-mounts",
                    ["cmd.exe", "/d", "/c", "type inputs\\read-only.txt"],
                )
                self.assertNotEqual(detached.exit_code, 0)
                self.assertFalse((info.workspace / "inputs" / "read-only.txt").exists())
            finally:
                await backend.destroy("native-mounts")

    async def test_host_network_and_timeout_are_denied_or_contained(self) -> None:
        with TemporaryDirectory() as directory:
            managed = Path(directory)
            host_secret = managed / "host-secret.txt"
            host_secret.write_text("must-not-be-readable", encoding="utf-8")
            backend = WindowsSandboxBackend(managed)
            await backend.create("native-security")
            try:
                await backend.start("native-security")
                host_result = await backend.execute(
                    "native-security",
                    ["cmd.exe", "/d", "/c", "type", str(host_secret)],
                    timeout_seconds=5,
                )
                self.assertNotEqual(host_result.exit_code, 0)
                self.assertNotIn("must-not-be-readable", host_result.stdout)

                curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
                if curl.is_file():
                    network_result = await backend.execute(
                        "native-security",
                        [
                            "curl.exe",
                            "--fail",
                            "--connect-timeout",
                            "2",
                            "http://1.1.1.1/",
                        ],
                        timeout_seconds=5,
                    )
                    self.assertNotEqual(network_result.exit_code, 0)

                timeout_result = await backend.execute(
                    "native-security",
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "for /L %i in (1,1,2147483647) do @rem",
                    ],
                    timeout_seconds=0.15,
                )
                self.assertTrue(timeout_result.timed_out)

                child_result = await backend.execute(
                    "native-security",
                    [
                        "cmd.exe",
                        "/d",
                        "/s",
                        "/c",
                        'start "" /b cmd.exe /d /c "for /L %j in (1,1,5000000) do @rem ^& echo escaped^>escaped.txt" & for /L %i in (1,1,2147483647) do @rem',
                    ],
                    timeout_seconds=0.15,
                )
                self.assertTrue(child_result.timed_out)
                await asyncio.sleep(1)
                workspace = (await backend.get("native-security")).workspace
                self.assertFalse(
                    (workspace / "escaped.txt").exists(),
                    "a child process escaped the kill-on-close Job Object",
                )
            finally:
                await backend.destroy("native-security")


if __name__ == "__main__":
    unittest.main()
