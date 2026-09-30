"""Recovery checks observe external state without resubmitting operations."""

import asyncio
import base64
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
                   "state": "finished", "exit_code": 0}
        with patch("backend.sandbox.history.read", return_value=[command]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "confirmed")
        with patch("backend.sandbox.history.read", return_value=[{**command, "run_id": "another-run"}]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "unknown")
        with patch("backend.sandbox.history.read", return_value=[{
            **command, "tool_result": {"exit_code": 1},
        }]):
            result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "unknown")

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

    def test_saved_script_is_checked_by_content_hash(self) -> None:
        content = b"print('model')\n"
        receipt = self.journal.begin(run_id=self.run.run_id, agent_id="agent",
                                     capability_kind="sandbox.write_text_file", target_id="sandbox",
                                     arguments={"path": "build.py", "content": content.decode()}, read_only=False)
        self.journal.finish(receipt, {"path": "build.py", "sha256": hashlib.sha256(content).hexdigest()})

        class Backend:
            async def file_operation(self, _sandbox_id, _operation, **_options):
                return {"state": "ready", "data": base64.b64encode(content).decode()}

        self.services._require_sandbox_backend = lambda: Backend()
        result = asyncio.run(assess_run(self.services, "agent", self.run.run_id))
        self.assertEqual(result[0]["evidence"], "confirmed")


if __name__ == "__main__":
    unittest.main()
