"""Recovery checks observe external state without resubmitting operations."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend.persistence.database import Database
from backend.runs.recovery import assess_run
from backend.runs.store import RunStore
from backend.runs.tool_receipts import RunToolReceipts


class RunRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.run = RunStore(self.database).create(agent_id="agent", runtime_provider_id="test",
                                                   caller_kind="user")
        self.journal = RunToolReceipts(self.database)
        self.capabilities = SimpleNamespace(
            capability_for_id=lambda *_args: object(),
            require_sandbox_execute=lambda *_args: None,
        )
        self.services = SimpleNamespace(database=self.database, capabilities=self.capabilities)

    def tearDown(self) -> None:
        self.database.close()

    def test_structure_revision_must_still_match(self) -> None:
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id="agent",
                                     capability_kind="atomsculptor.structure.write",
                                     target_id="structure", arguments={"expected_revision": 2}, read_only=False)
        self.journal.finish(receipt, {"revision": 3})
        with patch("backend.node_documents.read_document", return_value={"revision": 3}):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "confirmed")
        with patch("backend.node_documents.read_document", return_value={"revision": 4}):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "changed")

    def test_sandbox_operation_is_looked_up_by_id_and_run(self) -> None:
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id="agent",
                                     capability_kind="sandbox.execute", target_id="sandbox",
                                     arguments={"argv": ["python3", "build.py"]}, read_only=False)
        self.journal.finish(receipt, {"status": "running", "operation_id": "command-1"})
        command = {"id": "command-1", "run_id": self.run.run_id,
                   "state": "finished", "exit_code": 0, "argv": ["python3", "-c", "print(1)"],
                   "stdout": "1\n", "stderr": ""}
        with patch("backend.sandbox.history.read", return_value=[command]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "confirmed")
        self.assertEqual(result[0]["operation"]["argv"], command["argv"])
        self.assertFalse(result[0]["operation"]["argv_truncated"])
        self.assertEqual(result[0]["operation"]["output_preview"]["stdout"], "1\n")
        self.assertEqual(result[0]["operation"]["output_reference"]["arguments"]["operation_id"], "command-1")
        self.assertEqual(result[0]["operation"]["output_reference"]["reader"], "wait_sandbox_operation")
        with patch("backend.sandbox.history.read", return_value=[{**command, "run_id": "another-run"}]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "unknown")
        with patch("backend.sandbox.history.read", return_value=[{
            **command, "tool_result": {"exit_code": 1},
        }]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "unknown")

    def test_operation_outputs_are_bounded_and_keep_a_native_reader(self):
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id="agent",
            capability_kind="sandbox.execute", target_id="sandbox", arguments={}, read_only=False)
        self.journal.finish(receipt, {"operation_id": "command-1"})
        command = {"id": "command-1", "run_id": self.run.run_id, "state": "finished",
            "tool_result": {"exit_code": 0, "stdout": "x" * 1500, "stderr": "diagnostic"}}
        with patch("backend.sandbox.history.read", return_value=[command]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        operation = result[0]["operation"]
        self.assertEqual(result[0]["evidence"], "confirmed")
        self.assertEqual(operation["exit_code"], 0)
        self.assertEqual(len(operation["output_preview"]["stdout"]), 1000)
        self.assertTrue(operation["output_truncated"])
        self.assertEqual(operation["output_reference"]["arguments"]["operation_id"], "command-1")
        self.capabilities.capability_for_id = Mock(side_effect=RuntimeError("revoked"))
        with patch("backend.sandbox.history.read") as history:
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertNotIn("operation", result[0])
        history.assert_not_called()

    def test_lost_tool_response_can_reconcile_by_tool_receipt_id(self) -> None:
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id="agent",
                                     capability_kind="sandbox.execute", target_id="sandbox",
                                     arguments={"argv": ["python3", "build.py"]}, read_only=False)
        self.journal.uncertain(receipt, ConnectionResetError("response lost"))
        command = {"id": "command-1", "tool_receipt_id": receipt,
                   "run_id": self.run.run_id, "state": "finished", "exit_code": 0}
        with patch("backend.sandbox.history.read", return_value=[command]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "confirmed")

    def test_saved_file_requires_reconciliation_without_current_state_evidence(self) -> None:
        content = b"print('model')\n"
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id="agent",
                                     capability_kind="sandbox.write_text_file", target_id="sandbox",
                                     arguments={"path": "build.py", "content": content.decode()}, read_only=False)
        self.journal.finish(receipt, {"path": "build.py", "written": len(content), "sha256": "legacy-digest"})
        backend = SimpleNamespace(file_operation=AsyncMock())
        authorize = Mock()
        self.services.capabilities.capability_for_id = authorize
        self.services._require_sandbox_backend = lambda: backend
        result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "unknown")
        self.assertNotIn("sha256", result[0]["result_hints"])
        backend.file_operation.assert_not_awaited()
        authorize.assert_called_once_with("agent", "sandbox.write_text_file:sandbox")


    def test_start_is_confirmed_by_live_state_and_unknown_when_not_recovered(self):
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id='agent',
            capability_kind='sandbox.start', target_id='sandbox', arguments={}, read_only=False)
        self.journal.finish(receipt, {'state': 'ready'})
        self.services.get_sandbox = AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(value='ready')))
        result = asyncio.run(assess_run(self.services, 'agent', self.run.run_id))
        self.assertEqual(result[0]['evidence'], 'confirmed')
        self.services.get_sandbox.return_value.state.value = 'error'
        result = asyncio.run(assess_run(self.services, 'agent', self.run.run_id))
        self.assertEqual(result[0]['evidence'], 'unknown')


if __name__ == "__main__":
    unittest.main()
