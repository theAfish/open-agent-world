"""Old failed Sandbox receipts remain readable without changing stored data."""
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from backend.sandbox.history import read_key


class SandboxHistoryCompatibilityTests(unittest.TestCase):
    def test_missing_argv_is_normalized_for_history_consumers(self):
        stored = json.dumps([{"state": "error", "error": "failed before dispatch"}])

        class Database:
            @contextmanager
            def locked(self):
                yield SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchone=lambda: (stored,)))

        services = SimpleNamespace(database=Database(), _sandbox_commands={})
        records = read_key(services, "sandbox_history:test", "sandbox")
        self.assertEqual(records[0]["argv"], [])
        self.assertEqual(records[0]["id"], "legacy:sandbox_history:test:0")
        self.assertEqual(records[0]["error"], "failed before dispatch")
        self.assertNotIn("argv", json.loads(stored)[0])


if __name__ == "__main__":
    unittest.main()
