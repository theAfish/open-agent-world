"""Recovery receipts must survive restart without saving tool payloads."""

import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.persistence.database import Database
from backend.runs.store import RunStore
from backend.runs.tool_receipts import RunToolReceipts, current_tool_receipt_id


class RunToolReceiptTests(unittest.TestCase):
    def test_capability_boundary_records_before_and_after_dispatch(self) -> None:
        import asyncio

        database = Database(":memory:")
        run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                        caller_kind="user")
        capability = SimpleNamespace(kind="sandbox.execute", target_id="sandbox",
                                     agent_id="agent", id="sandbox.execute:sandbox")

        @asynccontextmanager
        async def mutation(*, read_only=False):
            yield

        async def handler(_context, _capability, _arguments):
            self.assertIsNotNone(current_tool_receipt_id())
            return {"status": "running", "operation_id": "command-1"}

        services = SimpleNamespace(
            database=database, _node_mutation=mutation,
            run_manager=SimpleNamespace(current_context=SimpleNamespace(run_id=run.run_id)),
            plugins=SimpleNamespace(capability_handler=lambda _kind: handler,
                                    capability_definition=lambda _kind: SimpleNamespace(read_only=False)),
        )
        try:
            provider = WorldAgentCapabilityProvider(services)
            with patch("backend.capabilities.projection.authorize_invocation", return_value=capability):
                result = asyncio.run(provider.invoke_tool("agent", capability.id, {"argv": ["python3", "build.py"]}))
            self.assertEqual(result["operation_id"], "command-1")
            receipt = RunToolReceipts(database).list_run(run.run_id)[0]
            self.assertIsNone(current_tool_receipt_id())
            self.assertEqual(receipt["state"], "finished")
            self.assertEqual(receipt["result_hints"]["operation_id"], "command-1")
            self.assertEqual(RunToolReceipts(database).recovery_classification(run.run_id), "reconcile_required")
        finally:
            database.close()

    def test_capability_exception_never_becomes_a_safe_replay(self) -> None:
        import asyncio

        database = Database(":memory:")
        run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                        caller_kind="user")
        capability = SimpleNamespace(kind="sandbox.execute", target_id="sandbox",
                                     agent_id="agent", id="sandbox.execute:sandbox")

        @asynccontextmanager
        async def mutation(*, read_only=False):
            yield

        async def handler(_context, _capability, _arguments):
            raise ConnectionResetError("outcome uncertain")

        services = SimpleNamespace(
            database=database, _node_mutation=mutation,
            run_manager=SimpleNamespace(current_context=SimpleNamespace(run_id=run.run_id)),
            plugins=SimpleNamespace(capability_handler=lambda _kind: handler,
                                    capability_definition=lambda _kind: SimpleNamespace(read_only=False)),
        )
        try:
            provider = WorldAgentCapabilityProvider(services)
            with patch("backend.capabilities.projection.authorize_invocation", return_value=capability):
                with self.assertRaises(ConnectionResetError):
                    asyncio.run(provider.invoke_tool("agent", capability.id, {"argv": ["python3", "build.py"]}))
            receipt = RunToolReceipts(database).list_run(run.run_id)[0]
            self.assertEqual(receipt["state"], "outcome_unknown")
            self.assertEqual(RunToolReceipts(database).recovery_classification(run.run_id), "reconcile_required")
        finally:
            database.close()

    def test_read_only_receipt_is_durable_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            database = Database(path)
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            journal = RunToolReceipts(database)
            receipt_id = journal.begin(run_id=run.run_id, agent_id="agent",
                                       capability_kind="atom_structure.read", target_id="structure",
                                       arguments={"secret": "never-persist-this"}, read_only=True)
            journal.finish(receipt_id, {"revision": 4, "atoms": [{"private": "not-in-receipt"}]})
            database.close()

            restored = Database(path)
            try:
                receipts = RunToolReceipts(restored)
                record = receipts.list_run(run.run_id)[0]
                self.assertEqual(record["state"], "finished")
                self.assertEqual(record["result_hints"], {"revision": 4})
                self.assertEqual(receipts.recovery_classification(run.run_id), "read_only")
                self.assertNotIn("never-persist-this", str(record))
                self.assertNotIn("not-in-receipt", str(record))
            finally:
                restored.close()

    def test_unconfirmed_mutation_requires_reconciliation(self) -> None:
        database = Database(":memory:")
        try:
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            journal = RunToolReceipts(database)
            receipt_id = journal.begin(run_id=run.run_id, agent_id="agent",
                                       capability_kind="sandbox.execute", target_id="sandbox",
                                       arguments={"argv": ["python3", "build.py"]}, read_only=False)
            self.assertEqual(journal.recovery_classification(run.run_id), "reconcile_required")
            journal.uncertain(receipt_id, ConnectionResetError("private detail"))
            self.assertEqual(journal.list_run(run.run_id)[0]["error_type"], "ConnectionResetError")
            self.assertEqual(journal.recovery_classification(run.run_id), "reconcile_required")
        finally:
            database.close()

    def test_running_sandbox_result_is_not_a_completed_effect(self) -> None:
        database = Database(":memory:")
        try:
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            journal = RunToolReceipts(database)
            receipt_id = journal.begin(run_id=run.run_id, agent_id="agent",
                                       capability_kind="sandbox.execute", target_id="sandbox",
                                       arguments={"argv": ["python3", "build.py"]}, read_only=False)
            journal.finish(receipt_id, {"status": "running", "operation_id": "command-1"})
            self.assertEqual(journal.recovery_classification(run.run_id), "reconcile_required")
        finally:
            database.close()

    def test_returned_write_still_needs_live_reconciliation(self) -> None:
        database = Database(":memory:")
        try:
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            journal = RunToolReceipts(database)
            receipt_id = journal.begin(run_id=run.run_id, agent_id="agent",
                                       capability_kind="atomsculptor.structure.write", target_id="structure",
                                       arguments={"expected_revision": 1}, read_only=False)
            journal.finish(receipt_id, {"revision": 2})
            self.assertEqual(journal.recovery_classification(run.run_id), "reconcile_required")
        finally:
            database.close()

    def test_restart_keeps_unfinished_tool_outcome_unknown(self) -> None:
        database = Database(":memory:")
        try:
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            journal = RunToolReceipts(database)
            journal.begin(run_id=run.run_id, agent_id="agent", capability_kind="sandbox.execute",
                          target_id="sandbox", arguments={"argv": ["build"]}, read_only=False)
            journal.interrupt_run(run.run_id)
            self.assertEqual(journal.list_run(run.run_id)[0]["state"], "outcome_unknown")
            self.assertEqual(journal.recovery_classification(run.run_id), "reconcile_required")
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
