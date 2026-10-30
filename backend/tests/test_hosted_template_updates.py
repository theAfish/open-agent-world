import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import shutil
import sqlite3
from uuid import uuid4, uuid5

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.hosting.deployments import DeployRequest, DeploymentStore
from backend.hosting.template_updates import PROVENANCE, update_runtime
from backend.hosting.templates import export_template, initialize_runtime
from backend.hosting.worker import WorkerConfig, create_worker_app, create_worker_services, mint_worker_token
from backend.tests.test_deployments import prepare_multiple_workspaces, prepare_source
from backend.world.models import CardCreate, CardPatch


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("template-update-seed")
    records = prepare_source(directory / "source")
    path = directory / "template.json"
    template = asyncio.run(export_template(directory / "source", records["release"]["id"], path))
    config = WorkerConfig(str(uuid4()), str(uuid4()), "stable-application", "runtime",
                          "https://oaw.example", "ac" * 32, directory / "runtime", agent_runtime="core.mock")
    asyncio.run(initialize_runtime(config, path))
    return config, template, records


@pytest.fixture
def runtime(seed, tmp_path):
    config, template, records = seed
    root = tmp_path / "runtime"
    shutil.copytree(config.data_root, root, ignore=shutil.ignore_patterns("resource-leases"))
    return replace(config, data_root=root), deepcopy(template), records


def definition(tmp_path, template):
    path = tmp_path / "new-template.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    return path


def provenance(config):
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        return json.loads(db.execute("SELECT value_json FROM application_settings WHERE key=?", (PROVENANCE,)).fetchone()[0])


def headers(config):
    return {"Authorization": "Bearer " + mint_worker_token(config.secret, config.profile_id, config.user_id),
            "Origin": config.public_origin}


def test_compatible_update_retains_profile_sessions_resources_knowledge_and_user_edits(runtime, tmp_path):
    config, template, records = runtime
    root = config.data_root
    old_manifest = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    binding = (root / "hosted-profile.json").read_bytes()
    assignment = root.parent / "agenticfs.json"
    assignment.write_text('{"volume_id":"existing-user-volume"}', encoding="utf-8")
    uploaded = root / "uploads" / "analysis.txt"
    uploaded.parent.mkdir()
    uploaded.write_text("User results", encoding="utf-8")
    ids = provenance(config)["node_ids"]
    chat, agent, text = (ids[records[key]["id"]] for key in ("chat", "agent", "text"))
    with TestClient(create_worker_app(config), headers=headers(config)) as client:
        saved = client.post(f"/api/runtime-app/workspace/conversations/{chat}/sessions",
                            json={"title": "Retain this session"})
        assert saved.status_code in {200, 201}, saved.text
        session = saved.json()

    from backend.config import Settings
    from oaw_knowledge_base import client as knowledge_client, projects
    from oaw_knowledge_base.context import KnowledgeContext, MemorySettings
    services = create_worker_services(replace(Settings.for_data_root(root), agent_runtime="core.mock", sandbox_runtime=None))
    try:
        asyncio.run(services.update_card(agent, CardPatch(config={"legion_role": "User-selected role"})))
        services.resources.replace_text(text, "User-edited guide")
        knowledge = asyncio.run(services.create_card(CardCreate(type="knowledge.base", name="User knowledge")))
        storage = services.resources.node_storage_path(knowledge.id)
        context = KnowledgeContext(knowledge.id, storage, state=MemorySettings())
        preview = projects.project_import_preview(context, {"files": [{"filename": "measurements.md",
            "content_base64": base64.b64encode(b"# User knowledge\nMeasured 42.").decode()}], "grouping": "single"})
        imported = projects.project_import_commit(context, {"token": preview["token"], "grouping": "single", "auto_process": False})
        source_id = imported["source_ids"][0]
        original_source = knowledge_client.open_client(knowledge.id, storage).sources.require(source_id)
        source_hash = original_source.sha256
    finally:
        knowledge_client.close_client(knowledge.id)
        services.close()

    for node in template["blueprint"]["nodes"]:
        source_id_for_node = template["source_nodes"][node["key"]]
        if source_id_for_node == records["agent"]["id"]:
            node["config"]["system_instruction"] = "New authored instruction"
        elif source_id_for_node == records["legion"]["id"]:
            node["config"]["workspace_layout"]["root"]["ratio"] = .4
            node["name"] = "Updated studio"
        # Capture keys may change across releases. Map every reference through
        # source lineage and prove that the stable runtime IDs still match.
    from backend.hosting.template_updates import _references
    changed_keys = {key: "capture-" + str(index) for index, key in enumerate(reversed(template["source_nodes"]))}
    template["blueprint"] = _references(template["blueprint"], changed_keys)
    template["source_nodes"] = {changed_keys[key]: value for key, value in template["source_nodes"].items()}
    template["root_key"] = changed_keys[template["root_key"]]
    template["root_keys"] = [changed_keys[key] for key in template["root_keys"]]
    template["source_release_id"] = str(uuid4())
    report = asyncio.run(update_runtime(config, definition(tmp_path, template)))
    assert report["profile"] == config.profile_id and report["ready"]
    current = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    assert current["id"] == old_manifest["id"] and current["legion_id"] == old_manifest["legion_id"]
    assert current["source_release_id"] == template["source_release_id"]
    assert current["layout"]["root"]["ratio"] == .4
    assert (root / "hosted-profile.json").read_bytes() == binding
    assert assignment.read_text(encoding="utf-8") == '{"volume_id":"existing-user-volume"}'
    assert uploaded.read_text(encoding="utf-8") == "User results"
    services = create_worker_services(replace(Settings.for_data_root(root), agent_runtime="core.mock", sandbox_runtime=None))
    try:
        assert services.world.get_card(agent).config["system_instruction"] == "New authored instruction"
        assert services.world.get_card(agent).config["legion_role"] == "User-selected role"
        assert services.resources.read_text(text).content == "User-edited guide"
        assert knowledge_client.open_client(knowledge.id, storage).sources.require(source_id).sha256 == source_hash
    finally:
        knowledge_client.close_client(knowledge.id)
        services.close()
    with TestClient(create_worker_app(config), headers=headers(config)) as client:
        response = client.get(f"/api/runtime-app/workspace/conversations/{chat}")
        assert response.status_code == 200, response.text
        assert any(item["id"] == session["id"] and item["title"] == session["title"] for item in response.json()["sessions"])


@pytest.mark.parametrize("change,match", [
    (lambda value: value["source_identity"].update(profile_id=str(uuid4())), "source owner or profile"),
    (lambda value: value["source_identity"].update(organization_id="foreign", workspace_id="other", world_id="world"), "source owner or profile"),
    (lambda value: value["blueprint"]["nodes"][-1].update(type="conversation"), "explicit migration"),
    (lambda value: value["blueprint"]["nodes"][1].update(state_scope="session"), "explicit migration"),
    (lambda value: next(node for node in value["blueprint"]["nodes"] if node["type"] == "text")["payload"].update(content="Replacement guide"), "explicit migration"),
])
def test_foreign_lineage_and_structural_changes_are_rejected_without_mutation(runtime, tmp_path, change, match):
    config, template, _ = runtime
    manifest = (config.data_root / "deployment.json").read_bytes()
    before = provenance(config)
    change(template)
    def forbidden(_):
        pytest.fail("Incompatible lineage must fail before loading runtime services")
    with pytest.raises(ValueError, match=match):
        asyncio.run(update_runtime(config, definition(tmp_path, template), services_factory=forbidden))
    assert provenance(config) == before
    assert (config.data_root / "deployment.json").read_bytes() == manifest


def test_missing_provenance_requires_operator_adoption(runtime, tmp_path):
    config, template, _ = runtime
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        db.execute("DELETE FROM application_settings WHERE key=?", (PROVENANCE,))
    with pytest.raises(ValueError, match="operator adoption"):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))


def test_foreign_runtime_owner_is_rejected_before_services(runtime, tmp_path):
    config, template, _ = runtime
    def forbidden(_):
        pytest.fail("Foreign runtime must fail before loading services")
    with pytest.raises(ValueError, match="owner or scope"):
        asyncio.run(update_runtime(replace(config, user_id=str(uuid4())), definition(tmp_path, template), services_factory=forbidden))


def test_active_runs_are_never_recovered_or_overwritten_by_update(runtime, tmp_path):
    config, template, _ = runtime
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        db.execute("INSERT INTO runs(run_id,agent_id,root_run_id,caller_kind,runtime_provider_id,status,created_at,updated_at) "
                   "VALUES ('pending','agent','pending','conversation','core.mock','waiting','now','now')")
    before = provenance(config)
    with pytest.raises(ValueError, match="active runs"):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        assert db.execute("SELECT status FROM runs WHERE run_id='pending'").fetchone()[0] == "waiting"
    assert provenance(config) == before


def test_conflicting_user_config_keeps_entire_update_unapplied(runtime, tmp_path):
    config, template, records = runtime
    agent = provenance(config)["node_ids"][records["agent"]["id"]]
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        value = json.loads(db.execute("SELECT config_json FROM cards WHERE id=?", (agent,)).fetchone()[0])
        value["system_instruction"] = "User customized instruction"
        db.execute("UPDATE cards SET config_json=? WHERE id=?", (json.dumps(value), agent))
    for node in template["blueprint"]["nodes"]:
        node["name"] = "Update should be atomic"
        if node["type"] == "agent":
            node["config"]["system_instruction"] = "Conflicting new instruction"
    before = provenance(config)
    manifest = (config.data_root / "deployment.json").read_bytes()
    with pytest.raises(ValueError, match="user edit"):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))
    assert provenance(config) == before
    assert (config.data_root / "deployment.json").read_bytes() == manifest
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        assert not db.execute("SELECT 1 FROM cards WHERE name='Update should be atomic'").fetchone()


def test_invalid_new_layout_rolls_back_configuration_and_provenance(runtime, tmp_path):
    from backend.errors import DomainError
    config, template, _ = runtime
    before = provenance(config)
    for node in template["blueprint"]["nodes"]:
        if node["type"] == "legion":
            node["name"] = "Should roll back"
            node["config"]["workspace_layout"]["root"] = {"kind": "pane", "view": {"card_id": "missing"}}
    with pytest.raises(DomainError):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))
    assert provenance(config) == before
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        assert not db.execute("SELECT 1 FROM cards WHERE name='Should roll back'").fetchone()


def test_unknown_background_work_remains_tracked_and_blocks_update(runtime, tmp_path):
    from backend.errors import ConflictError
    config, template, records = runtime
    before = provenance(config)
    job = {"agent_id": before["node_ids"][records["agent"]["id"]], "sandbox_id": "sandbox",
           "status": "unknown", "wake_requested": False}
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        db.execute("INSERT INTO background_jobs VALUES (?,?,?,?,?)", ("job", job["agent_id"], "session", "request", json.dumps(job)))
    with pytest.raises(ConflictError, match="Background work"):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))
    assert provenance(config) == before
    with sqlite3.connect(config.data_root / "database/world.sqlite3") as db:
        assert json.loads(db.execute("SELECT record_json FROM background_jobs WHERE id='job'").fetchone()[0]) == job


def test_multiworkspace_scope_and_order_require_explicit_migration(tmp_path):
    records = prepare_multiple_workspaces(tmp_path / "source")
    path = tmp_path / "template.json"
    template = asyncio.run(export_template(tmp_path / "source", records["release"]["id"], path))
    config = WorkerConfig(str(uuid4()), str(uuid4()), "portal", "runtime", "https://oaw.example",
                          "cd" * 32, tmp_path / "runtime", agent_runtime="core.mock")
    asyncio.run(initialize_runtime(config, path))
    before = provenance(config)
    template["root_keys"].reverse()
    template["root_key"] = template["root_keys"][0]
    with pytest.raises(ValueError, match="workspace scope"):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))
    assert provenance(config) == before


def test_interrupted_manifest_handoff_is_recovered_on_worker_start(runtime, tmp_path, monkeypatch):
    config, template, _ = runtime
    template["name"] = "New application name"
    template["source_release_id"] = str(uuid4())
    import backend.hosting.template_updates as updates
    write = updates.write_private_json
    def interrupted(*args):
        raise OSError("Simulated interrupted manifest publication")
    monkeypatch.setattr(updates, "write_private_json", interrupted)
    with pytest.raises(OSError, match="interrupted manifest"):
        asyncio.run(update_runtime(config, definition(tmp_path, template)))
    assert provenance(config)["pending_manifest"]["source_release_id"] == template["source_release_id"]
    monkeypatch.setattr(updates, "write_private_json", write)
    with TestClient(create_worker_app(config), headers=headers(config)) as client:
        assert client.get("/api/runtime-app").status_code == 200
    assert "pending_manifest" not in provenance(config)
    assert json.loads((config.data_root / "deployment.json").read_text(encoding="utf-8"))["name"] == "New application name"


def test_update_queue_reuses_only_explicit_profile_and_is_idempotent(runtime, tmp_path):
    config, template, _ = runtime
    store = DeploymentStore(tmp_path / "jobs.sqlite3")
    request = DeployRequest(request_id=uuid4(), release_id=template["source_release_id"], user_ids=[config.user_id],
                            target_profiles={config.user_id: config.profile_id})
    jobs = store.enqueue(request, "admin", template)
    assert store.enqueue(request, "admin", template) == jobs
    assert store.jobs()[0]["profile_id"] == config.profile_id
    assert store.jobs()[0]["operation"] == "update"
    with pytest.raises(ValueError, match="existing profile deployment"):
        store.enqueue(request.model_copy(update={"request_id": uuid4()}), "admin", template)
    with pytest.raises(ValueError, match="every target account"):
        DeployRequest(request_id=uuid4(), release_id=template["source_release_id"], user_ids=[config.user_id],
                      target_profiles={str(uuid4()): config.profile_id})


def test_admin_update_requires_owned_runtime_target_and_keeps_entry_assignment(seed, tmp_path):
    from backend.hosting.gateway import create_gateway
    from backend.tests.test_hosted_gateway import fixture_data, ORIGIN
    _, template, _ = seed
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    store.set_role(users[0]["id"], "admin")
    target = replace(profiles[1], mode="runtime")
    settings = replace(settings, profiles=(profiles[0], target))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=(
        template if "/hosting-template/" in request.url.path else [])))
    with TestClient(create_gateway(settings, frontend, store=store, transport=transport), base_url=ORIGIN) as client:
        payload = {"request_id": str(uuid4()), "release_id": template["source_release_id"],
                   "user_ids": [users[1]["id"]], "target_profiles": {users[1]["id"]: target.profile_id}}
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN}).status_code == 403
        client.cookies.set("oaw_session", store.create_session(users[0]["id"]))
        foreign = {**payload, "target_profiles": {users[1]["id"]: profiles[0].profile_id}}
        assert client.post("/hosting/admin/deployments", json=foreign, headers={"Origin": ORIGIN}).status_code == 409
        accepted = client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN})
        assert accepted.status_code == 202, accepted.text
        listing = client.get("/hosting/admin/deployments").json()
        assert listing["jobs"][0]["operation"] == "update"
        assert listing["jobs"][0]["profile_id"] == target.profile_id
        assert next(user for user in listing["users"] if user["id"] == users[1]["id"])["entry_id"] == target.profile_id


def test_legacy_queued_initialization_remains_idempotent_after_schema_upgrade(seed, tmp_path):
    _, template, _ = seed
    request = DeployRequest(request_id=uuid4(), release_id=template["source_release_id"], user_ids=[uuid4()])
    legacy_request = request.model_dump(mode="json")
    legacy_request.pop("target_profiles")
    digest = hashlib.sha256(json.dumps(legacy_request, sort_keys=True).encode()).hexdigest()
    job_id = str(uuid5(request.request_id, str(request.user_ids[0])))
    path = tmp_path / "legacy-jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE hosting_jobs (id TEXT PRIMARY KEY,request_id TEXT,request_digest TEXT,"
                   "requested_by TEXT,user_id TEXT,profile_id TEXT,mode TEXT,name TEXT,template_json TEXT,"
                   "status TEXT,message TEXT,created_at REAL)")
        db.execute("INSERT INTO hosting_jobs VALUES (?,?,?,?,?,?,?,?,?,'queued','',0)",
                   (job_id, str(request.request_id), digest, "admin", str(request.user_ids[0]), job_id,
                    "runtime", template["name"], json.dumps(template)))
    store = DeploymentStore(path)
    assert store.previous(request, "admin") == [job_id]
    assert store.enqueue(request, "admin", template) == [job_id]
    assert [(job["id"], job["operation"]) for job in store.jobs()] == [(job_id, "initialize")]
