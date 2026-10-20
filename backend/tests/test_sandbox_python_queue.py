"""Managed Python commands queue behind the host's environment preparation."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.sandbox.manager import SandboxManager, _Binding
from backend.sandbox.models import CommandResult, SandboxInfo, SandboxPreparationError, SandboxState
from backend.sandbox.operations import SandboxOperations


class SandboxPythonQueueTests(unittest.TestCase):
    def test_python_command_waits_for_bootstrap_without_cancelling_it(self) -> None:
        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()
            executed = asyncio.Event()
            started = []

            class Backend:
                supports_optional_python_runtime = True
                supports_execution_policy = True
                supports_invocation_environment = True

                async def prepare_python(self, _requirements, bootstrap_key=None):
                    entered.set()
                    await release.wait()
                    return {"kind": "python"}

                async def python_status(self):
                    return {"last_install_state": "running"}

                async def execute(self, sandbox_id, argv, **_options):
                    executed.set()
                    return CommandResult(sandbox_id, tuple(argv), 0, "ok", "", 0.01)

            with tempfile.TemporaryDirectory() as directory:
                manager = SandboxManager(Path(directory), registry=None)
                manager._bindings["sandbox"] = _Binding(
                    "sandbox", resolved_runtime="seatbelt", provisioned=True,
                )
                manager._backends["seatbelt"] = Backend()
                install = asyncio.create_task(manager.prepare_python(
                    "seatbelt", ["numpy"], bootstrap_key="enabled-packs",
                ))
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(manager.python_preparation_active("seatbelt"))
                self.assertTrue((await manager.python_status("sandbox"))["preparing"])

                async def mark_started():
                    started.append("started")
                command = asyncio.create_task(manager.execute("sandbox", ["python3", "-c", "print(1)"],
                                                              on_command_start=mark_started))
                await asyncio.sleep(0)
                self.assertFalse(executed.is_set())
                self.assertEqual(started, [])
                command.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await command
                self.assertFalse(install.cancelled())

                queued = asyncio.create_task(manager.execute("sandbox", ["python3", "-c", "print(1)"],
                                                             on_command_start=mark_started))
                await asyncio.sleep(0)
                self.assertFalse(executed.is_set())
                release.set()
                await asyncio.wait_for(install, 1)
                result = await asyncio.wait_for(queued, 1)
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(started, ["started"])
                self.assertFalse(manager.python_preparation_active("seatbelt"))
                self.assertFalse((await manager.python_status("sandbox"))["preparing"])

        asyncio.run(scenario())

    def test_wait_reports_queued_python_command(self) -> None:
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()

            class Backend:
                async def prepare_python(self, _requirements, bootstrap_key=None):
                    entered.set()
                    await release.wait()
                    return {"kind": "python"}

                async def python_status(self):
                    return {"last_install_state": "running", "output_tail": "Downloading wheel"}

            with tempfile.TemporaryDirectory() as directory:
                manager = SandboxManager(Path(directory), registry=None)
                manager._bindings["sandbox"] = _Binding(
                    "sandbox", resolved_runtime="seatbelt", provisioned=True,
                )
                manager._backends["seatbelt"] = Backend()
                install = asyncio.create_task(manager.prepare_python("seatbelt", ["numpy"]))
                await asyncio.wait_for(entered.wait(), 1)
                receipt = {"id": "queued-command", "sandbox_id": "sandbox",
                           "operation_kind": "command", "state": "running",
                           "argv": ["python3", "-c", "print(1)"],
                           "python_environment": "auto", "started_at": "now"}
                services = SimpleNamespace(sandbox_backend=manager, _sandbox_tasks={},
                    _require_card_type=lambda *_: None)
                operations = SandboxOperations(services)
                with patch("backend.sandbox.history.read", return_value=[receipt]):
                    status = await operations.wait(None, "sandbox", "queued-command", 0)
                self.assertEqual(status["status"], "running")
                self.assertEqual(status["phase"], "preparing_python")
                self.assertFalse(status["command_started"])
                self.assertTrue(status["waiting_for_shared_python"])
                self.assertEqual(status["shared_python"]["output_tail"], "Downloading wheel")
                self.assertIn("Do not resubmit", status["next_step"])
                release.set()
                await asyncio.wait_for(install, 1)

        asyncio.run(scenario())

    def test_seatbelt_start_warms_python_and_failure_is_visible_until_explicit_retry(self) -> None:
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            info = SandboxInfo("sandbox", SandboxState.READY, Path("/sandbox"))

            class Backend:
                supports_optional_python_runtime = True
                supports_execution_policy = True
                supports_invocation_environment = True
                fail = True
                calls = 0

                async def get(self, _sandbox_id):
                    return info

                async def prepare_python(self, _requirements, bootstrap_key=None):
                    self.calls += 1
                    entered.set()
                    await release.wait()
                    if self.fail:
                        raise SandboxPreparationError("download made no progress")
                    return {"kind": "python"}

                async def python_status(self):
                    return {"last_install_state": "failed" if self.fail else "ready"}

                async def execute(self, sandbox_id, argv, **_options):
                    return CommandResult(sandbox_id, tuple(argv), 0, "ok", "", 0.01)

            with tempfile.TemporaryDirectory() as directory:
                manager = SandboxManager(Path(directory), registry=None)
                backend = Backend()
                manager._bindings["sandbox"] = _Binding(
                    "sandbox", resolved_runtime="darwin", provisioned=True)
                manager._backends["darwin"] = backend
                with patch.object(manager, "get", return_value=info):
                    await manager.start("sandbox")
                    await asyncio.wait_for(entered.wait(), 1)
                    self.assertTrue((await manager.python_status("sandbox"))["preparing"])
                    release.set()
                    with self.assertRaises(SandboxPreparationError):
                        await manager._python_warmups["darwin"]
                    await asyncio.sleep(0)  # deliver the warmup completion callback
                    status = await manager.python_status("sandbox")
                    self.assertIn("download made no progress", status["warmup_error"])
                    with self.assertRaisesRegex(SandboxPreparationError, "start_sandbox again"):
                        await manager.execute("sandbox", ["python3", "-V"])
                    self.assertEqual(backend.calls, 1)
                    backend.fail = False
                    await manager.start("sandbox")
                    await asyncio.wait_for(manager._python_warmups["darwin"], 1)
                    result = await manager.execute("sandbox", ["python3", "-V"])
                    self.assertEqual(result.exit_code, 0)
                    self.assertFalse((await manager.python_status("sandbox"))["preparing"])
                await manager.shutdown()

        asyncio.run(scenario())

    def test_concurrent_install_requests_are_serialized(self) -> None:
        async def scenario():
            first_entered, release = asyncio.Event(), asyncio.Event()
            calls = []

            class Backend:
                async def prepare_python(self, requirements, bootstrap_key=None):
                    calls.append(tuple(requirements))
                    if len(calls) == 1:
                        first_entered.set()
                        await release.wait()
                    return {"kind": "python"}

            with tempfile.TemporaryDirectory() as directory:
                manager = SandboxManager(Path(directory), registry=None)
                manager._backends["seatbelt"] = Backend()
                first = asyncio.create_task(manager.prepare_python("seatbelt", ["numpy"]))
                await asyncio.wait_for(first_entered.wait(), 1)
                second = asyncio.create_task(manager.prepare_python("seatbelt", ["ase"]))
                await asyncio.sleep(0)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(manager._python_preparations["seatbelt"]), 2)
                release.set()
                await asyncio.wait_for(asyncio.gather(first, second), 1)
                self.assertEqual(len(calls), 2)
                self.assertFalse(manager.python_preparation_active("seatbelt"))

        asyncio.run(scenario())

    def test_first_command_preparation_is_tracked(self) -> None:
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            executed = []

            class Backend:
                supports_optional_python_runtime = True
                supports_execution_policy = True
                supports_invocation_environment = True

                async def prepare_python(self, requirements, bootstrap_key=None):
                    entered.set()
                    await release.wait()
                    return {"kind": "python"}

                async def execute(self, sandbox_id, argv, **_options):
                    executed.append(tuple(argv))
                    return CommandResult(sandbox_id, tuple(argv), 0, "ok", "", 0.01)

            with tempfile.TemporaryDirectory() as directory:
                manager = SandboxManager(Path(directory), registry=None)
                manager._bindings["sandbox"] = _Binding(
                    "sandbox", resolved_runtime="seatbelt", provisioned=True,
                )
                manager._backends["seatbelt"] = Backend()
                first = asyncio.create_task(manager.execute("sandbox", ["python3", "-V"]))
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(manager.python_preparation_active("seatbelt"))
                second = asyncio.create_task(manager.execute("sandbox", ["python3", "-c", "print(1)"]))
                await asyncio.sleep(0)
                self.assertFalse(executed)
                release.set()
                await asyncio.wait_for(asyncio.gather(first, second), 1)
                self.assertEqual(len(executed), 2)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
