from dataclasses import asdict
import asyncio
import importlib
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.hosting.accounts import AccountStore
from backend.hosting.cli import initialize, provision
from backend.hosting.configuration import HostedProfile, write_private_json
from backend.hosting.deployments import DeployRequest, DeploymentStore
from backend.hosting import provisioner


def profile():
    return HostedProfile(str(uuid4()), str(uuid4()), "application", "Workspace", "runtime",
                         "http://private-worker", "ca" * 32)


def configure(tmp_path, monkeypatch, handler):
    path = tmp_path / "resource-provisioner.json"
    write_private_json(path, {"handler": "test_resource_hook:prepare", "settings": {"operator_value": "fixed"}})
    trusted = []
    monkeypatch.setattr(provisioner, "_trusted", trusted.append)
    original = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(prepare=handler)
                        if name == "test_resource_hook" else original(name))
    return path, trusted


def test_resource_hook_uses_only_fixed_operator_settings_and_keeps_waiting_identity(tmp_path, monkeypatch):
    current = profile()
    seen = []
    def prepare(item, settings, directory):
        seen.append((item.profile_id, settings, directory))
        return None if len(seen) == 1 else {"provider-workspace.json": {"profile_id": item.profile_id, "ready": True}}
    path, trusted = configure(tmp_path, monkeypatch, prepare)
    assert provisioner.prepare_resources(tmp_path, current) is None
    assert provisioner.prepare_resources(tmp_path, current) == {
        "provider-workspace.json": {"profile_id": current.profile_id, "ready": True}}
    assert seen == [(current.profile_id, {"operator_value": "fixed"}, tmp_path)] * 2
    assert trusted == [path, path]
    assert not (tmp_path / "instances").exists()


def test_resource_hook_absent_preserves_existing_install_behavior(tmp_path):
    assert provisioner.prepare_resources(tmp_path, profile()) == {}


def test_resource_hook_rejects_symlink_before_import(tmp_path, monkeypatch):
    path = tmp_path / "resource-provisioner.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda value: value == path or original(value))
    with pytest.raises(ValueError, match="symlinks"):
        provisioner.prepare_resources(tmp_path, profile())


def test_resource_hook_rejects_untrusted_ownership(tmp_path, monkeypatch):
    path, _ = configure(tmp_path, monkeypatch, lambda *args: pytest.fail("Untrusted hook must never run"))
    def untrusted(value):
        assert value == path
        raise ValueError("Configuration must be root-owned")
    monkeypatch.setattr(provisioner, "_trusted", untrusted)
    with pytest.raises(ValueError, match="root-owned"):
        provisioner.prepare_resources(tmp_path, profile())


@pytest.mark.parametrize("files", [
    {"../outside.json": {}}, {"worker.json": {}}, {"TEMPLATE.json": {}}, {"data/config.json": {}},
    {"valid.json": "not an object"}, {"large.json": {"value": "a" * (1024 * 1024)}},
])
def test_resource_hook_rejects_reserved_paths_and_invalid_assignments(tmp_path, monkeypatch, files):
    configure(tmp_path, monkeypatch, lambda *args: files)
    with pytest.raises(ValueError):
        provisioner.prepare_resources(tmp_path, profile())
    assert not (tmp_path / "instances").exists()


@pytest.fixture
def queued(tmp_path, monkeypatch):
    directory = tmp_path / "hosting"
    path = initialize(directory, "https://oaw.example")
    accounts = AccountStore(path.parent / "accounts.sqlite3")
    admin = accounts.create_user("operator", role="admin")
    user = accounts.create_user("alice")
    node = lambda key, kind, parent: {
        "key": key, "parent_key": parent, "type": kind, "plugin_id": "core", "name": key,
        "position": {"x": 0, "y": 0}, "size": {"width": 300, "height": 200},
        "expanded": False, "status": "available", "config": {},
    }
    release = str(uuid4())
    template = {"version": 1, "name": "Application", "source_release_id": release, "root_key": "root",
                "created_at": "2026-10-30T00:00:00+00:00", "blueprint": {"format_version": 1,
                    "bounds": {"width": 500, "height": 400},
                    "nodes": [node("root", "legion", None), node("note", "text", "root")], "edges": []}}
    store = DeploymentStore(path.parent / "deployments.sqlite3")
    request = DeployRequest(request_id=uuid4(), release_id=release, user_ids=[user["id"]])
    job_id, = store.enqueue(request, admin["id"], template)
    monkeypatch.setattr(provisioner, "check_installation", lambda *args: (directory, tmp_path / "application", "oaw-hosted"))
    return directory, store, job_id


def test_waiting_resource_job_retries_without_local_install_and_publishes_only_after_ready(queued, monkeypatch):
    directory, store, job_id = queued
    resources = iter([None, {"provider-workspace.json": {"profile_id": job_id, "ready": True}}])
    prepared, installed, registered = [], [], []
    def prepare(root, item):
        prepared.append(item.profile_id)
        assert not (root / "instances").exists()
        return next(resources)
    monkeypatch.setattr(provisioner, "prepare_resources", prepare)
    monkeypatch.setattr(provisioner, "install_profile", lambda *args, **kwargs: installed.append(kwargs["resource_files"]))
    monkeypatch.setattr(provisioner, "register_profile", lambda root, item, name: registered.append(item.profile_id))
    provisioner.tick(directory, directory / "application")
    assert store.jobs()[0]["status"] == "waiting_resources"
    assert installed == registered == []
    provisioner.tick(directory, directory / "application")
    assert prepared == [job_id, job_id] and registered == [job_id]
    assert installed == [{"provider-workspace.json": {"profile_id": job_id, "ready": True}}]
    assert store.jobs()[0]["status"] == "ready"


def test_uncertain_local_install_is_failed_and_never_replayed(queued, monkeypatch):
    directory, store, job_id = queued
    calls = []
    monkeypatch.setattr(provisioner, "prepare_resources", lambda *args: {})
    def failed(*args, **kwargs):
        calls.append(job_id)
        raise RuntimeError("Uncertain local installation")
    monkeypatch.setattr(provisioner, "install_profile", failed)
    monkeypatch.setattr(provisioner, "register_profile", lambda *args: pytest.fail("Failed profile must not be registered"))
    provisioner.tick(directory, directory / "application")
    provisioner.tick(directory, directory / "application")
    assert calls == [job_id] and store.jobs()[0]["status"] == "failed"


def test_interrupted_resource_preparation_retries_same_job_before_any_install(queued, monkeypatch):
    directory, store, job_id = queued
    attempts = []
    def prepare(root, item):
        attempts.append(item.profile_id)
        if len(attempts) == 1:
            raise KeyboardInterrupt("Simulated process termination during provider request")
        return None
    monkeypatch.setattr(provisioner, "prepare_resources", prepare)
    monkeypatch.setattr(provisioner, "install_profile", lambda *args, **kwargs: pytest.fail("Resources are not ready"))
    with pytest.raises(KeyboardInterrupt):
        provisioner.tick(directory, directory / "application")
    assert store.jobs()[0]["status"] == "preparing_resources"
    provisioner.tick(directory, directory / "application")
    assert attempts == [job_id, job_id] and store.jobs()[0]["status"] == "waiting_resources"
    assert not (directory / "instances").exists()


def test_interrupted_local_preparation_requires_operator_inspection(queued, monkeypatch):
    directory, store, job_id = queued
    with store.transaction(immediate=True) as db:
        db.execute("UPDATE hosting_jobs SET status='preparing' WHERE id=?", (job_id,))
    monkeypatch.setattr(provisioner, "prepare_resources", lambda *args: pytest.fail("An uncertain local install must not retry"))
    provisioner.tick(directory, directory / "application")
    assert store.jobs()[0]["status"] == "interrupted"


def test_offline_profile_add_keeps_resource_identity_while_waiting(tmp_path, monkeypatch):
    directory = tmp_path / "hosting"
    path = initialize(directory, "https://oaw.example")
    user = AccountStore(path.parent / "accounts.sqlite3").create_user("alice")
    write_private_json(directory / "resource-provisioner.json", {"handler": "test_resource_hook:prepare", "settings": {}})
    seen = []
    def waiting(root, item):
        seen.append(item.profile_id)
        return None
    monkeypatch.setattr(provisioner, "prepare_resources", waiting)
    for _ in range(2):
        with pytest.raises(ValueError, match="pending"):
            asyncio.run(provision(directory, user["id"], agent_runtime="core.mock"))
    assert len(seen) == 2 and seen[0] == seen[1]
    assert not (directory / "instances").exists()


def test_queued_update_uses_existing_profile_and_never_provisions_new_resources(queued, monkeypatch):
    directory, store, job_id = queued
    with store.transaction(immediate=True) as db:
        job = dict(db.execute("SELECT * FROM hosting_jobs WHERE id=?", (job_id,)).fetchone())
        existing = profile()
        existing = HostedProfile(existing.profile_id, job["user_id"], existing.application_id,
                                 existing.name, existing.mode, existing.upstream, existing.secret)
        db.execute("UPDATE hosting_jobs SET operation='update', profile_id=? WHERE id=?", (existing.profile_id, job_id))
    path = directory / "control/hosting.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["profiles"].append(asdict(existing))
    write_private_json(path, settings)
    updates = []
    monkeypatch.setattr(provisioner, "update_profile", lambda root, app, item, template, name, job: updates.append((item, job)))
    monkeypatch.setattr(provisioner, "prepare_resources", lambda *args: pytest.fail("Updates must retain existing resources"))
    monkeypatch.setattr(provisioner, "install_profile", lambda *args, **kwargs: pytest.fail("Updates must not initialize a new profile"))
    monkeypatch.setattr(provisioner, "register_profile", lambda *args: pytest.fail("Updates must retain existing route identity"))
    provisioner.tick(directory, directory / "application")
    assert updates == [(existing, job_id)]
    assert store.jobs()[0]["status"] == "ready" and store.jobs()[0]["profile_id"] == existing.profile_id


def test_update_oneshot_reuses_worker_identity_and_private_filesystem_boundary():
    item = profile()
    unit = provisioner.profile_task_unit(PurePosixPath("/srv/oaw-hosting"), PurePosixPath("/opt/open-agent-world"),
                                         item.profile_id, "oaw-hosted", "update", template=True)
    assert "ProtectSystem=strict" in unit and "CapabilityBoundingSet=\n" in unit
    assert f"User={provisioner.service_user('oaw-hosted', item.profile_id)}" in unit
    assert "BindPaths=/srv/oaw-hosting/instances/" in unit
    assert "update --config /var/lib/oaw-hosted-profile/worker.json --template /var/lib/oaw-hosted-profile/update-template.json" in unit
    assert "User=root" not in unit
