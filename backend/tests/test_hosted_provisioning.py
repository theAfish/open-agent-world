import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.hosting.accounts import AccountStore
from backend.hosting.cli import initialize, provision
from backend.hosting.configuration import default_upstream, read_gateway_settings
from backend.hosting.systemd import render_units, service_user
from backend.hosting.templates import export_template
from backend.hosting.worker import create_worker_app, mint_worker_token
from backend.tests.test_deployments import prepare_source


def test_provision_independent_builder_profiles_and_private_systemd_services(tmp_path):
    directory = tmp_path / "hosting"
    config = initialize(directory, "https://oaw.example")
    store = AccountStore(config.parent / "accounts.sqlite3")
    alice, bob = store.create_user("alice"), store.create_user("bob")
    a = asyncio.run(provision(directory, alice["id"], agent_runtime="core.mock"))
    b = asyncio.run(provision(directory, bob["id"], agent_runtime="core.mock"))
    assert a.profile_id != b.profile_id and a.secret != b.secret
    assert len(read_gateway_settings(config).profiles) == 2
    with pytest.raises(ValueError, match="already"):
        asyncio.run(provision(directory, alice["id"]))
    with pytest.raises(ValueError, match="already"):
        initialize(directory, "https://oaw.example")
    units = render_units(read_gateway_settings(config), directory="/srv/oaw-hosting", application="/opt/open-agent-world")
    for p, other in ((a, b), (b, a)):
        worker = units[f"oaw-hosted-worker-{p.profile_id}.service"]
        assert p.upstream == default_upstream(p.profile_id)
        assert "--fd 3" in worker and "--host" not in worker
        assert "ProtectSystem=strict" in worker and "CapabilityBoundingSet=\n" in worker
        assert other.profile_id not in worker and "control/hosting" not in worker
        assert f"User={service_user('oaw-hosted', p.profile_id)}" in worker
        assert "SocketMode=0600" in units[f"oaw-hosted-worker-{p.profile_id}.socket"]
    assert service_user('oaw-hosted', a.profile_id) != service_user('oaw-hosted', b.profile_id)
    assert "--port 8080" in units["oaw-hosted-gateway.service"]
    assert a.secret not in json.dumps(units)


def test_published_template_creates_private_fresh_runtime_without_source_history_or_secrets(tmp_path):
    source = tmp_path / "source"
    records = prepare_source(source)
    settings = replace(Settings.for_data_root(source), agent_runtime="core.mock", sandbox_runtime=None)
    with TestClient(create_app(settings)) as client:
        response = client.post(f"/api/conversations/{records['chat']['id']}/sessions", json={"title": "PRIVATE HISTORY"})
        assert response.status_code in {200, 201}, response.text
    with sqlite3.connect(source / "database/world.sqlite3") as db:
        source_session_ids = {row[0] for row in db.execute("SELECT id FROM conversation_sessions")}
    secret = source / "secrets" / "private.txt"
    secret.parent.mkdir(exist_ok=True)
    secret.write_text("PRIVATE CREDENTIAL DO NOT COPY", encoding="utf-8")
    target = tmp_path / "template.json"
    asyncio.run(export_template(source, records["release"]["id"], target))
    text = target.read_text(encoding="utf-8")
    assert "PRIVATE HISTORY" not in text and "PRIVATE CREDENTIAL" not in text and "NOT PUBLIC" not in text
    directory = tmp_path / "hosting"
    config = initialize(directory, "https://oaw.example")
    store = AccountStore(config.parent / "accounts.sqlite3")
    alice, bob = store.create_user("alice"), store.create_user("bob")
    a = asyncio.run(provision(directory, alice["id"], mode="runtime", template=target, agent_runtime="core.mock"))
    b = asyncio.run(provision(directory, bob["id"], mode="runtime", template=target, agent_runtime="core.mock"))
    roots = []
    manifests = []
    for profile in (a, b):
        root = directory / "instances" / profile.profile_id / "data"
        roots.append(root)
        assert not (root / "secrets/private.txt").exists()
        with sqlite3.connect(root / "database/world.sqlite3") as db:
            sessions = db.execute("SELECT id, title FROM conversation_sessions").fetchall()
            assert not source_session_ids.intersection(row[0] for row in sessions)
            assert "PRIVATE HISTORY" not in [row[1] for row in sessions]
            assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM cards WHERE name='Hidden internal notes'").fetchone()[0] == 0
        manifest = json.loads((root / "deployment.json").read_text())
        manifests.append(manifest)
        assert manifest["auth_mode"] == "hosted" and "password" not in manifest
        worker_config = json.loads((root.parent / "worker.json").read_text())
        worker_config["data_root"] = root
        headers = {"Authorization": "Bearer " + mint_worker_token(profile.secret, profile.profile_id, profile.user_id),
                   "Origin": "https://oaw.example"}
        with TestClient(create_worker_app(worker_config)) as client:
            assert client.get("/api/runtime-app", headers=headers).status_code == 200
            assert client.get("/api/world", headers=headers).status_code == 404
            assert client.post("/api/deployment/session", headers=headers, json={"password": "anything"}).status_code == 404
            chat_id = next(node for node, ops in manifest["permissions"].items() if "conversation" in ops)
            response = client.post(f"/api/runtime-app/workspace/conversations/{chat_id}/sessions", headers=headers,
                                   json={"title": profile.user_id})
            assert response.status_code in {200, 201}, response.text
    assert manifests[0]["legion_id"] != manifests[1]["legion_id"]
    with sqlite3.connect(roots[1] / "database/world.sqlite3") as db:
        titles = {row[0] for row in db.execute("SELECT title FROM conversation_sessions")}
        assert bob["id"] in titles and alice["id"] not in titles and "PRIVATE HISTORY" not in titles


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(password="embedded credential"),
    lambda value: value.update(allow_terminal="false"),
    lambda value: value["blueprint"].update(format_version=99),
    lambda value: value["blueprint"]["nodes"][1].update(parent_key=None),
    lambda value: value["blueprint"]["nodes"][1].update(plugin_defaults={"api_key": "embedded credential"}),
    lambda value: value["blueprint"]["nodes"][1].update(initial_document={"path": "/private/source.sqlite3"}),
    lambda value: value["blueprint"]["nodes"][0].update(initial_shared_state={"private": "state"}),
])
def test_invalid_templates_rejected_before_profile_creation(tmp_path, mutation):
    from uuid import uuid4
    from backend.hosting.templates import initialize_runtime, validate_template
    from backend.hosting.worker import WorkerConfig
    node = lambda key, kind, parent: {
        "key": key, "parent_key": parent, "type": kind, "plugin_id": "core", "name": key,
        "position": {"x": 0, "y": 0}, "size": {"width": 300, "height": 200},
        "expanded": False, "status": "available", "config": {},
    }
    value = {"version": 1, "name": "Studio", "source_release_id": str(uuid4()), "root_key": "root",
             "created_at": "2026-10-19T00:00:00+00:00", "allow_terminal": False,
             "blueprint": {"format_version": 1, "bounds": {"width": 500, "height": 400},
                           "nodes": [node("root", "legion", None), node("note", "text", "root")], "edges": []}}
    validate_template(value)
    mutation(value)
    path = tmp_path / "bad-template.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    root = tmp_path / "profile"
    config = WorkerConfig(str(uuid4()), str(uuid4()), "studio", "runtime", "https://oaw.example", "fa" * 32, root)
    with pytest.raises(ValueError):
        asyncio.run(initialize_runtime(config, path))
    assert not root.exists()


def test_configuration_requires_tls_and_runtime_template(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        initialize(tmp_path / "bad", "http://example.com")
    config = initialize(tmp_path / "local", "http://127.0.0.1:8080", True)
    user = AccountStore(config.parent / "accounts.sqlite3").create_user("alice")
    with pytest.raises(ValueError, match="template"):
        asyncio.run(provision(tmp_path / "local", user["id"], mode="runtime"))
    assert read_gateway_settings(config).profiles == ()
