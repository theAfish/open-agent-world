"""Latest specialist request is durable, bounded, and never commits partial output."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.persistence.database import Database
from backend.runs.model_checkpoints import RunModelCheckpoints
from backend.runs.store import RunStore


def request(text: str):
    content = SimpleNamespace(model_dump=lambda **_kwargs: {
        "role": "user", "parts": [{"text": text}],
    })
    return SimpleNamespace(contents=[content])


class RunModelCheckpointTests(unittest.TestCase):
    def test_specialist_request_survives_restart_and_marks_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            database = Database(path)
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            checkpoints = RunModelCheckpoints(database, run.run_id)
            checkpoints.start("structure_builder", 3, 0, request("Build the nanotube"))
            checkpoints.settle("structure_builder", 3, "interrupted")
            database.close()

            restored = Database(path)
            try:
                snapshot = RunModelCheckpoints(restored, run.run_id).latest("structure_builder")
                self.assertEqual(snapshot["state"], "interrupted")
                self.assertTrue(snapshot["replayable"])
                self.assertEqual(snapshot["contents"][0]["parts"][0]["text"], "Build the nanotube")
            finally:
                restored.close()

    def test_secret_or_oversized_request_does_not_enter_snapshot(self) -> None:
        database = Database(":memory:")
        try:
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            checkpoints = RunModelCheckpoints(database, run.run_id, api_key="private-api-key")
            checkpoints.start("planner", 1, 0, request("private-api-key"))
            snapshot = checkpoints.latest("planner")
            self.assertFalse(snapshot["replayable"])
            self.assertIsNone(snapshot["contents_json"])
            checkpoints.start("planner", 2, 0, request("x" * (256 * 1024)))
            self.assertFalse(checkpoints.latest("planner")["replayable"])
        finally:
            database.close()

    def test_restart_interrupts_unfinished_model_snapshot(self) -> None:
        database = Database(":memory:")
        try:
            run = RunStore(database).create(agent_id="agent", runtime_provider_id="test",
                                            caller_kind="user")
            checkpoints = RunModelCheckpoints(database, run.run_id)
            checkpoints.start("planner", 1, 0, request("Plan"))
            checkpoints.interrupt_run()
            self.assertEqual(checkpoints.latest("planner")["state"], "interrupted")
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
