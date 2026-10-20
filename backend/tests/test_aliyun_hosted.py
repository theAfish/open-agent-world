from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.cloud.aliyun import hosted
from backend.config import Settings
from backend.hosting.worker import WorkerConfig, create_worker_app, mint_worker_token, hosted_scope
from backend.cloud.aliyun.sandbox import AliyunSandboxBackend, AliyunSandboxSettings
from backend.sandbox.models import SandboxStateError


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
