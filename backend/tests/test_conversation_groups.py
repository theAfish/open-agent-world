from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.tests.test_conversations import _create


def test_group_rename_delete_are_scoped_durable_and_preserve_default(data_root: Path):
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        room = _create(client, "conversation", "Groups")
        base = f"/api/conversations/{room['id']}"
        default = client.get(base).json()["sessions"][0]
        first = client.post(base + "/sessions", json={"group_title": "Research", "title": "First"}).json()
        second = client.post(base + "/sessions", json={"group_id": first["group_id"], "title": "Second"}).json()
        group_url = base + f"/groups/{first['group_id']}"
        for session in (first, second):
            assert client.post(base + f"/sessions/{session['id']}/messages", json={"content": "Saved note"}).status_code == 202
        other = _create(client, "conversation", "Other")
        wrong = f"/api/conversations/{other['id']}/groups/{first['group_id']}"
        assert client.patch(wrong, json={"title": "Wrong"}).status_code == 404
        assert client.delete(wrong).status_code == 404
        assert client.patch(group_url, json={"title": " "}).status_code == 422
        assert client.patch(group_url, json={"title": "x" * 201}).status_code == 422
        renamed = client.patch(group_url, json={"title": " Renamed "})
        assert renamed.status_code == 200
        assert {s["group_title"] for s in renamed.json()} == {"Renamed"}
        assert {s["title"] for s in renamed.json()} == {"First", "Second"}
        # Default protection is based on membership, even when another session represents the group.
        extra = client.post(base + "/sessions", json={"group_id": default["group_id"]}).json()
        default_url = base + f"/groups/{default['group_id']}"
        assert client.patch(default_url, json={"title": "Home"}).status_code == 200
        assert client.delete(default_url).status_code == 422
        assert client.get(base + f"/sessions/{extra['id']}/timeline").status_code == 200
    with TestClient(create_app(settings)) as client:
        assert {s["group_title"] for s in client.get(base).json()["sessions"] if s["group_id"] == first["group_id"]} == {"Renamed"}
        assert client.delete(group_url).status_code == 204
        for session in (first, second):
            assert client.get(base + f"/sessions/{session['id']}/timeline").status_code == 404
        assert client.post(base + "/sessions", json={"group_id": first["group_id"]}).status_code == 404
        assert client.delete(group_url).status_code == 404
        assert {s["id"] for s in client.get(base).json()["sessions"]} == {default["id"], extra["id"]}
    with TestClient(create_app(settings)) as client:
        assert {s["id"] for s in client.get(base).json()["sessions"]} == {default["id"], extra["id"]}
