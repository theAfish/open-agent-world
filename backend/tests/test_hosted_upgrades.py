import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.errors import ConflictError
from backend.hosting.accounts import AccountStore
from backend.hosting.cli import initialize, provision
from backend.hosting.templates import export_template
from backend.hosting.upgrades import migrate_profile, refresh_manifest
from backend.hosting.worker import WorkerConfig, create_worker_app, create_worker_services, mint_worker_token
from backend.services import ApplicationServices
from backend.tests.test_deployments import prepare_source


@pytest.fixture
def runtime(tmp_path):
    source = tmp_path / "source"
    records = prepare_source(source)
    template = tmp_path / "template.json"
    asyncio.run(export_template(source, records["release"]["id"], template))
    directory = tmp_path / "hosting"
    path = initialize(directory, "https://oaw.example")
    user = AccountStore(path.parent / "accounts.sqlite3").create_user("alice")
    profile = asyncio.run(provision(directory, user["id"], mode="runtime", template=template,
                                   agent_runtime="core.mock"))
    instance = directory / "instances" / profile.profile_id
    value = json.loads((instance / "worker.json").read_text(encoding="utf-8"))
    value["data_root"] = instance / value["data_root"]
    return WorkerConfig(**value)


def test_upgrade_keeps_live_user_history_files_and_profile_identity(runtime):
    config = runtime
    root = config.data_root
    original = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    binding = (root / "hosted-profile.json").read_bytes()
    uploaded = root / "uploads" / "user-data.txt"
    uploaded.parent.mkdir()
    uploaded.write_text("Written by the user after initial deployment", encoding="utf-8")
    headers = {"Authorization": "Bearer " + mint_worker_token(config.secret, config.profile_id, config.user_id),
               "Origin": config.public_origin}
    chat = next(node for node, grants in original["permissions"].items() if "conversation" in grants)
    with TestClient(create_worker_app(config), headers=headers) as client:
        response = client.post(f"/api/runtime-app/workspace/conversations/{chat}/sessions",
                               json={"title": "Saved user conversation"})
        assert response.status_code in {200, 201}, response.text
        session = response.json()
        with pytest.raises(ConflictError, match="in use"):
            asyncio.run(migrate_profile(config))
    report = asyncio.run(migrate_profile(config))
    assert report == {"profile": config.profile_id, "ready": True, "changed_contracts": []}
    current = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    for key in ("id", "source_release_id", "layout", "runtime_settings", "legion_id"):
        assert current[key] == original[key]
    assert (root / "hosted-profile.json").read_bytes() == binding
    assert uploaded.read_text(encoding="utf-8") == "Written by the user after initial deployment"
    with TestClient(create_worker_app(config), headers=headers) as client:
        response = client.get(f"/api/runtime-app/workspace/conversations/{chat}")
        assert response.status_code == 200, response.text
        assert any(item["id"] == session["id"] and item["title"] == session["title"] for item in response.json()["sessions"])


def test_upgrade_rejects_foreign_owner_before_loading_plugins(runtime):
    def unavailable(settings):
        pytest.fail("Foreign profile must be rejected before loading any services")
    with pytest.raises(ValueError, match="owner or scope"):
        asyncio.run(migrate_profile(replace(runtime, user_id=str(uuid4())), services_factory=unavailable))


def test_upgrade_refuses_unfinished_work_without_recovery_or_manifest_changes(runtime):
    root = runtime.data_root
    manifest = (root / "deployment.json").read_bytes()
    with sqlite3.connect(root / "database/world.sqlite3") as db:
        db.execute("INSERT INTO runs(run_id,agent_id,root_run_id,caller_kind,runtime_provider_id,status,created_at,updated_at) "
                   "VALUES ('pending','agent','pending','conversation','core.mock','waiting','now','now')")
    with pytest.raises(ValueError, match="active runs"):
        asyncio.run(migrate_profile(runtime))
    with sqlite3.connect(root / "database/world.sqlite3") as db:
        assert db.execute("SELECT status FROM runs WHERE run_id='pending'").fetchone()[0] == "waiting"
    assert (root / "deployment.json").read_bytes() == manifest


def test_failed_plugin_startup_keeps_manifest_and_closes_services(runtime, monkeypatch):
    manifest = (runtime.data_root / "deployment.json").read_bytes()
    closed = []
    close = ApplicationServices.close
    async def fail(self):
        raise RuntimeError("Simulated installed plugin migration failure")
    def finish(self):
        closed.append(True)
        close(self)
    monkeypatch.setattr(ApplicationServices, "startup", fail)
    monkeypatch.setattr(ApplicationServices, "close", finish)
    with pytest.raises(RuntimeError, match="migration failure"):
        asyncio.run(migrate_profile(runtime))
    assert closed == [True]
    assert (runtime.data_root / "deployment.json").read_bytes() == manifest


def test_manifest_refresh_keeps_saved_identity_and_rejects_new_public_scope():
    old = {"id": "saved-release", "source_release_id": "initial-release", "layout": {"root": "saved"},
           "panels": [{"card_id": "kb", "sections": ["read"]}],
           "permissions": {"kb": ["read"], "conversation": ["chat"]},
           "plugin_access": {"kb": {"actions": ["read"]}}, "plugin_versions": {"kb": "1"},
           "runtime_settings": {"agent_runtime": "configured"}}
    new = deepcopy(old)
    new.update(id="fresh-release", source_release_id="new-release", plugin_versions={"kb": "2"}, runtime_settings={})
    new["plugin_access"]["kb"]["actions"].append("graph")
    new["panels"][0]["sections"].append("graph")
    result, changed = refresh_manifest(old, new)
    assert result["id"] == old["id"] and result["source_release_id"] == old["source_release_id"]
    assert result["runtime_settings"] == old["runtime_settings"]
    assert result["plugin_versions"] == {"kb": "2"} and changed == ["kb"]
    for key, value in (("layout", {}), ("permissions", {"kb": ["read"], "conversation": ["terminal"]}),
                       ("plugin_access", {"kb": {}, "other": {}})):
        with pytest.raises(ValueError):
            refresh_manifest(old, {**new, key: value})


def test_manifest_refresh_preserves_multiple_workspace_order_and_layout():
    workspaces = [{"legion_id": node, "layout": {"root": node}, "panels": [],
                   "permissions": {}, "plugin_access": {}} for node in ("one", "two")]
    old = {**workspaces[0], "workspaces": workspaces}
    new = deepcopy(old)
    new["workspaces"].reverse()
    with pytest.raises(ValueError, match="workspace scope or order"):
        refresh_manifest(old, new)
    new = deepcopy(old)
    new["workspaces"][1]["layout"] = {}
    with pytest.raises(ValueError, match="layout changed"):
        refresh_manifest(old, new)
