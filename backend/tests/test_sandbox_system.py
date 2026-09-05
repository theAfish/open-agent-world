"""Opt-in HTTP-to-native-boundary acceptance test against a disposable folder."""
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.mark.skipif(not os.environ.get("OAW_TEST_SANDBOX_RUNTIME"), reason="requires an explicitly selected native runtime")
def test_native_card_live_folder_and_readonly_round_trip(tmp_path):
    runtime = os.environ["OAW_TEST_SANDBOX_RUNTIME"]
    folder = tmp_path / "real project"
    folder.mkdir()
    settings = replace(Settings.for_data_root(tmp_path / "application"), sandbox_runtime=runtime, agent_runtime="core.mock")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/nodes", json={"type": "sandbox", "config": {
            "runtime": runtime, "workspace_path": str(folder), "workspace_access": "read_write",
        }})
        assert response.status_code == 201, response.text
        card = response.json()
        base = f"/api/sandboxes/{card['id']}"
        started = client.post(base + "/start")
        assert started.status_code == 200, started.text
        info = started.json()
        assert info["runtime_id"] == runtime
        assert info["workspace_path"] == str(folder)
        assert info["runtime_locked"]
        written = client.post(base + "/execute", json={"command": "echo live-work > result.txt"})
        assert written.status_code == 200 and written.json()["exit_code"] == 0, written.text
        assert (folder / "result.txt").read_text().strip() == "live-work"
        assert client.post(base + "/stop").status_code == 200
        readonly = client.patch(f"/api/nodes/{card['id']}", json={"config": {"workspace_access": "read_only"}})
        assert readonly.status_code == 200, readonly.text
        assert client.post(base + "/start").status_code == 200
        refused = client.post(base + "/execute", json={"command": "echo blocked > result.txt"})
        assert refused.status_code == 200 and refused.json()["exit_code"] != 0, refused.text
        assert (folder / "result.txt").read_text().strip() == "live-work"
        assert client.post(base + "/stop").status_code == 200
        assert client.delete(f"/api/nodes/{card['id']}").status_code == 200
        assert (folder / "result.txt").read_text().strip() == "live-work"
