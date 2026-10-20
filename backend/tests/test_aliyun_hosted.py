from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.cloud.aliyun import hosted
from backend.config import Settings
from backend.hosting.worker import WorkerConfig, create_worker_app, mint_worker_token, hosted_scope
from backend.cloud.aliyun.sandbox import AliyunSandboxBackend, AliyunSandboxSettings
from backend.sandbox.models import SandboxStateError


def test_full_research_publication_round_trips_into_private_cloud_workspaces(tmp_path, monkeypatch):
    import asyncio
    from contextlib import ExitStack
    import json

    from backend.hosting.templates import initialize_runtime
    from oaw_matcreator.preset import native_definition

    configure(monkeypatch, "unused")
    monkeypatch.delenv("OAW_ALIYUN_PROFILE_ID")
    monkeypatch.setenv("OAW_ALIYUN_WORKSPACE_STORE", "ecs")
    monkeypatch.setenv("OAW_ALIYUN_SANDBOX_READY", "1")

    async def forbidden(*args, **kwargs):
        raise AssertionError("Publishing and browsing must not allocate cloud compute")

    monkeypatch.setattr(AliyunSandboxBackend, "_start", forbidden)

    def config(name, mode):
        return WorkerConfig(str(uuid4()), str(uuid4()), "research", mode,
                            "https://hosted.example", "ab" * 32, tmp_path / name,
                            agent_runtime="core.mock")

    def client(worker):
        return TestClient(create_worker_app(worker, services_factory=hosted.create_hosted_services),
                          headers={"Authorization": "Bearer " + mint_worker_token(
                              worker.secret, worker.profile_id, worker.user_id),
                                   "Origin": worker.public_origin})

    registry = hosted.approved_plugins()
    preset = next(p for p in registry.legion_presets() if p.id == "matcreator.native-research")
    assert preset == native_definition()
    original = next(n for n in native_definition().nodes if n.key == "group")
    assert next(n for n in preset.nodes if n.key == "group").config["workspace_layout"] == original.config["workspace_layout"]
    with client(config("admin", "builder")) as admin:
        result = admin.post("/api/legions/presets/matcreator.native-research/instances", json={})
        assert result.status_code == 201, result.text
        legion = next(n for n in result.json()["nodes"] if n["type"] == "legion")
        request = {"legion_id": legion["id"], "name": "Full research"}
        assert admin.post("/api/deployments/preview", json=request).json()["ready"]
        release = admin.post("/api/deployments", json=request)
        assert release.status_code == 201, release.text
        assert len(release.json()["panels"]) == 10
        template = admin.get("/api/hosting-template/" + release.json()["id"])
        assert template.status_code == 200, template.text
        path = tmp_path / "template.json"
        path.write_text(json.dumps(template.json()), encoding="utf-8")

    root = "/api/runtime-app/workspace"
    with ExitStack() as stack:
        workspaces = []
        for name in ("alice", "bob"):
            worker = config(name, "runtime")
            asyncio.run(initialize_runtime(worker, path, services_factory=hosted.create_hosted_services))
            runtime = stack.enter_context(client(worker))
            bootstrap = runtime.get("/api/runtime-app")
            assert bootstrap.status_code == 200, bootstrap.text
            cards = {n["type"]: n["id"] for n in bootstrap.json()["cards"]}
            assert len(bootstrap.json()["panels"]) == 10
            assert len(bootstrap.json()["edges"]) == 2
            for suffix in (
                f"/nodes/{cards['core.execution-graph']}/execution-sources",
                f"/nodes/{cards['core.jobs']}/jobs",
                f"/artifact-collections/{cards['core.artifact-collection']}/versions",
                f"/sandboxes/{cards['sandbox']}/files",
            ):
                response = runtime.get(root + suffix)
                assert response.status_code == 200, response.text
            assert runtime.get("/api/world").status_code == 404
            assert runtime.get("/api/settings/models").status_code == 404
            workspaces.append((runtime, cards))
        (alice, a), (bob, b) = workspaces
        assert not set(a.values()).intersection(b.values())
        url = f"{root}/nodes/{a['matcreator.kdg']}/actions/"
        before = alice.post(url + "search", json={"arguments": {}}).json()
        edit = alice.post(url + "edit", json={"arguments": {
            "title": "Alice private finding", "type": "memory", "summary": "Not a template asset"},
            "expected_revision": before["revision"]})
        assert edit.status_code == 200, edit.text
        other = bob.post(f"{root}/nodes/{b['matcreator.kdg']}/actions/search", json={"arguments": {}})
        assert other.status_code == 200 and "Alice private finding" not in other.text
        assert bob.post(url + "search", json={"arguments": {}}).status_code == 404


def configure(monkeypatch, profile):
    for name, value in {
        "OAW_ALIYUN_PROFILE_ID": profile,
        "OAW_ALIYUN_REGION": "cn-hangzhou",
        "OAW_ALIYUN_SANDBOX_API_KEY": "test-only-key",
        "OAW_ALIYUN_SANDBOX_TEMPLATE": "base",
        "OAW_ALIYUN_WORKSPACE_VOLUME": f"oaw-{profile}",
        "OAW_ALIYUN_WORKSPACE_STORE": "volume",
        "OAW_ALIYUN_SANDBOX_READY": "0",
    }.items():
        monkeypatch.setenv(name, value)


def test_hosted_composition_exposes_only_unavailable_cloud_runtime_until_verified(tmp_path, monkeypatch):
    profile, user = str(uuid4()), str(uuid4())
    configure(monkeypatch, profile)
    worker = WorkerConfig(profile, user, "canvas", "builder", "https://hosted.example", "ab" * 32,
                          tmp_path, agent_runtime="core.mock")
    headers = {"Authorization": "Bearer " + mint_worker_token(worker.secret, profile, user),
               "Origin": worker.public_origin}
    app = create_worker_app(worker, services_factory=hosted.create_hosted_services)
    with TestClient(app, headers=headers) as client:
        result = client.get("/api/sandbox/runtimes")
        assert result.status_code == 200
        runtimes = result.json()["runtimes"]
        assert [item["id"] for item in runtimes] == ["aliyun.agent-sandbox"]
        assert not runtimes[0]["available"]
        assert "persistence verification" in runtimes[0]["reason"]
        assert client.get("/api/world").status_code == 200
        assert client.post("/api/packs/install", json={}).status_code == 403
        assert client.post("/api/nodes", json={"type": "text"}).status_code == 201


def test_new_ecs_workspace_files_work_before_any_compute_start(tmp_path, monkeypatch):
    profile, user = str(uuid4()), str(uuid4())
    configure(monkeypatch, profile)
    monkeypatch.setenv("OAW_ALIYUN_WORKSPACE_STORE", "ecs")
    monkeypatch.setenv("OAW_ALIYUN_SANDBOX_READY", "1")
    worker = WorkerConfig(profile, user, "files", "builder", "https://hosted.example", "ab" * 32,
                          tmp_path, agent_runtime="core.mock")
    headers = {"Authorization": "Bearer " + mint_worker_token(worker.secret, profile, user), "Origin": worker.public_origin}
    async def forbidden(*args, **kwargs):
        raise AssertionError("File browsing must not allocate cloud compute")
    monkeypatch.setattr(AliyunSandboxBackend, "_start", forbidden)
    with TestClient(create_worker_app(worker, services_factory=hosted.create_hosted_services), headers=headers) as client:
        card = client.post("/api/nodes", json={"type": "sandbox", "name": "Files"}).json()
        response = client.get(f"/api/sandboxes/{card['id']}/files")
        assert response.status_code == 200, response.text
        assert client.get(f"/api/sandboxes/{card['id']}").json()["state"] == "stopped"


def test_worker_rejects_another_users_sandbox_configuration(tmp_path, monkeypatch):
    profile, user = str(uuid4()), str(uuid4())
    configure(monkeypatch, str(uuid4()))
    settings = replace(Settings.for_data_root(tmp_path), tenant_scope=hosted_scope(user, "canvas", profile))
    with pytest.raises(ValueError, match="belong to this hosted profile"):
        hosted.create_hosted_services(settings)
    configure(monkeypatch, profile)
    monkeypatch.setenv("OAW_ALIYUN_WORKSPACE_VOLUME", "shared-volume")
    with pytest.raises(ValueError, match="own oaw-"):
        hosted.create_hosted_services(settings)


def test_shared_cloud_environment_derives_new_users_private_identity(tmp_path, monkeypatch):
    configure(monkeypatch, str(uuid4()))
    monkeypatch.delenv("OAW_ALIYUN_PROFILE_ID")
    monkeypatch.delenv("OAW_ALIYUN_WORKSPACE_VOLUME")
    monkeypatch.setenv("OAW_ALIYUN_WORKSPACE_STORE", "ecs")
    monkeypatch.setenv("OAW_ALIYUN_SANDBOX_TEMPLATE", "code-interpreter-v1")
    monkeypatch.setenv("OAW_ALIYUN_SANDBOX_PYTHON_REQUIREMENTS", '["numpy", "ase"]')
    captured = []
    monkeypatch.setattr(hosted, "registry_factory", lambda remote: captured.append(remote))
    monkeypatch.setattr(hosted, "create_services", lambda *args, **kwargs:
        SimpleNamespace(world=SimpleNamespace(list_cards=lambda: [])))
    profiles = [str(uuid4()), str(uuid4())]
    for profile in profiles:
        settings = replace(Settings.for_data_root(tmp_path / profile),
                           tenant_scope=hosted_scope(str(uuid4()), "canvas", profile))
        hosted.create_hosted_services(settings)
    assert [remote.profile_id for remote in captured] == profiles
    assert all(remote.template == "code-interpreter-v1" and remote.python_requirements == ("numpy", "ase")
               and remote.workspace_store == "ecs" and not remote.volume for remote in captured)


@pytest.mark.asyncio
async def test_unverified_storage_cannot_create_or_reconnect_a_remote_vm(tmp_path):
    class NoCloudCalls:
        def __getattr__(self, name):
            raise AssertionError(f"Unexpected cloud call: {name}")
    remote = AliyunSandboxSettings("cn-hangzhou", "test-key", "base", "test-volume", "test-profile",
                                   unavailable_reason="Storage verification pending")
    backend = AliyunSandboxBackend(tmp_path, remote, sdk=NoCloudCalls())
    await backend.create("sandbox")
    with pytest.raises(SandboxStateError, match="Storage verification pending"):
        await backend.start("sandbox")
    state = backend._read("sandbox")
    assert state["remote_id"] is None and state["request_key"] is None
