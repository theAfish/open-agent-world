"""The shipped example uses the real locked runtime and survives a restart."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


def test_deployment_example(tmp_path):
    example = Path(__file__).resolve().parents[2] / "examples/deployed-workspace"
    spec = importlib.util.spec_from_file_location("deployment_example", example / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = module.prepare(tmp_path / "demo")
    manifest = (runtime / "deployment.json").read_bytes()
    settings = replace(Settings.for_data_root(runtime), agent_runtime="example.deployment-demo",
                       plugin_directories=(example / "plugins",))
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/deployment/session", json={"password": module.PASSWORD}).status_code == 200
        app = client.get("/api/runtime-app").json()
        assert len(app["panels"]) == 4
        assert "PRIVATE-DEMO" not in json.dumps(app)
        notes = next(card for card in app["cards"] if card["type"] == "example.deployment-notes")
        definition = next(node for node in app["catalog"]["node_types"] if node["id"] == notes["type"])
        assert definition["frontend"] == {"body": "notes", "workspace": "notes"}
        assert notes["config"] == {"heading": "Workspace notes"}
        note_base = f"/api/runtime-app/workspace/nodes/{notes['id']}"
        snapshot = client.get(note_base + "/document").json()
        assert set(snapshot["value"]) == {"text"}
        updated = client.post(note_base + "/actions/save", json={"arguments": {"text": "Published plugin works"}, "expected_revision": snapshot["revision"]})
        assert updated.status_code == 200, updated.text
        assert updated.json()["value"] == {"text": "Published plugin works"}
        assert "PRIVATE-DEMO" not in updated.text
        assert client.post(note_base + "/actions/save", json={"arguments": {"text": "Conflict"}, "expected_revision": snapshot["revision"]}).status_code == 409
        assert client.get(note_base + "/document/downloads/text").text == "Published plugin works"
        for path in ("/document/downloads/private", "/execution"):
            assert client.get(note_base + path).status_code == 404
        for path in ("/actions/replace", "/resource/delete", "/execution/start", "/transformations/replace"):
            assert client.post(note_base + path, json={"arguments": {}}).status_code == 404
        assert client.patch(note_base, json={"config": {"internal_connection": "bad"}}).status_code == 404
        assert client.get("/api/world").status_code == 404
        assert client.get("/api/settings/models").status_code == 404
        chat = next(panel["card_id"] for panel in app["panels"] if panel["kind"] == "conversation")
        base = f"/api/runtime-app/workspace/conversations/{chat}"
        agents = client.get(base).json()["agents"]
        response = client.post(base + "/sessions", json={"title": "Example acceptance", "participant_ids": [agents[0]["id"]]})
        assert response.status_code == 201, response.text
        session = response.json()["id"]
        messages = f"{base}/sessions/{session}/messages"
        response = client.post(messages, json={"content": "Hello demo", "mention_agent_ids": response.json()["participant_ids"]})
        assert response.status_code == 202, response.text
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            transcript = client.get(messages).text
            if "预设回复" in transcript:
                break
            time.sleep(.05)
        assert "预设回复" in transcript
        assert "Mock response:" not in transcript
    assert module.prepare(tmp_path / "demo") == runtime
    assert (runtime / "deployment.json").read_bytes() == manifest
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        client.post("/api/deployment/session", json={"password": module.PASSWORD})
        assert "Hello demo" in client.get(messages).text
        assert client.get(note_base + "/document").json()["value"]["text"] == "Published plugin works"
        assert json.loads(manifest)["name"] == app["name"]
