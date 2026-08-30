"""Opt-in native Windows isolation smoke test.

This creates a disposable AppContainer profile, executes only built-in Windows
programs, verifies read-only mounting, unrelated-host-file isolation, outbound
network denial, and Job Object cleanup of a spawned process tree.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from backend.sandbox import ResourceAccess, WindowsSandboxBackend


async def main() -> None:
    if os.name != "nt":
        raise SystemExit("native sandbox smoke test requires Windows")

    with tempfile.TemporaryDirectory(prefix="oaw-native-smoke-") as temporary:
        root = Path(temporary)
        managed = root / "managed"
        asset = managed / "assets" / "text" / "probe.txt"
        asset.parent.mkdir(parents=True)
        asset.write_text("mounted-ok", encoding="utf-8")
        unrelated = root / "host-secret.txt"
        unrelated.write_text("must-not-be-readable", encoding="utf-8")

        sandbox_id = f"smoke-{uuid4().hex[:12]}"
        backend = WindowsSandboxBackend(managed)
        created = False
        try:
            await backend.create(sandbox_id)
            created = True
            await backend.attach_resource(
                sandbox_id,
                "probe",
                asset,
                r"resources\probe.txt",
                ResourceAccess.READ_ONLY,
            )
            await backend.start(sandbox_id)
            mounted = await backend.execute(
                sandbox_id,
                ["cmd.exe", "/d", "/s", "/c", r"type resources\probe.txt"],
                timeout_seconds=10,
            )
            if mounted.exit_code != 0 or "mounted-ok" not in mounted.stdout:
                raise RuntimeError(
                    f"explicit mount was not readable: {mounted.exit_code} {mounted.stderr}"
                )

            read_only_write = await backend.execute(
                sandbox_id,
                [
                    "cmd.exe",
                    "/d",
                    "/s",
                    "/c",
                    r"echo changed>resources\probe.txt",
                ],
                timeout_seconds=10,
            )
            asset_after_write = asset.read_text(encoding="utf-8")
            if read_only_write.exit_code == 0 or asset_after_write != "mounted-ok":
                raise RuntimeError(
                    "read-only attachment unexpectedly accepted a write: "
                    f"exit={read_only_write.exit_code} "
                    f"stdout={read_only_write.stdout!r} "
                    f"stderr={read_only_write.stderr!r} "
                    f"content={asset_after_write!r}"
                )

            host_read = await backend.execute(
                sandbox_id,
                ["cmd.exe", "/d", "/s", "/c", f'type "{unrelated}"'],
                timeout_seconds=10,
            )
            if host_read.exit_code == 0 or "must-not-be-readable" in host_read.stdout:
                raise RuntimeError("AppContainer unexpectedly read an unrelated host file")

            system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            curl = system32 / "curl.exe"
            if not curl.is_file():
                raise RuntimeError("native network test requires the Windows curl.exe")
            network = await backend.execute(
                sandbox_id,
                [
                    str(curl),
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "3",
                    "https://example.com",
                ],
                timeout_seconds=10,
            )
            if network.exit_code == 0:
                raise RuntimeError("AppContainer unexpectedly reached the Internet")
            if network.exit_code == 0xC0000142:
                raise RuntimeError("curl could not initialize, so network denial was inconclusive")

            process_tree = (await backend.get(sandbox_id)).workspace / "process-tree.cmd"
            heartbeat = process_tree.with_name("child-heartbeat.txt")
            process_tree.write_text(
                "@echo off\n"
                "if /I \"%~1\"==\"child\" goto child\n"
                "start \"\" /b \"%ComSpec%\" /d /c call \"%~f0\" child\n"
                ":parent\n"
                "goto parent\n"
                ":child\n"
                ">>\"%~dp0child-heartbeat.txt\" echo tick\n"
                "goto child\n",
                encoding="utf-8",
            )

            contained = await backend.execute(
                sandbox_id,
                [
                    "cmd.exe",
                    "/d",
                    "/s",
                    "/c",
                    "call process-tree.cmd",
                ],
                timeout_seconds=0.75,
            )
            if not contained.timed_out:
                raise RuntimeError(
                    "long-running process tree did not reach the Job timeout: "
                    f"exit={contained.exit_code} stdout={contained.stdout!r} "
                    f"stderr={contained.stderr!r}"
                )
            if not heartbeat.is_file() or heartbeat.stat().st_size == 0:
                raise RuntimeError("the process-tree probe did not start its child process")
            heartbeat_size = heartbeat.stat().st_size
            await asyncio.sleep(0.25)
            if heartbeat.stat().st_size != heartbeat_size:
                raise RuntimeError("a child process survived Job Object termination")
            follow_up = await backend.execute(
                sandbox_id,
                ["cmd.exe", "/d", "/s", "/c", "echo containment-ok"],
                timeout_seconds=10,
            )
            if follow_up.exit_code != 0 or "containment-ok" not in follow_up.stdout:
                raise RuntimeError("sandbox did not recover after Job Object termination")

            print(
                json.dumps(
                    {
                        "mount_read": "passed",
                        "read_only_write_rejection": "passed",
                        "host_file_isolation": "passed",
                        "network_denial": "passed",
                        "process_tree_timeout": "passed",
                        "security_boundary": (await backend.get(sandbox_id)).security_boundary,
                    },
                    indent=2,
                )
            )
        finally:
            if created:
                await backend.destroy(sandbox_id)


if __name__ == "__main__":
    asyncio.run(main())
