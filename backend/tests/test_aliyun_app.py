import pytest
from fastapi.testclient import TestClient

pytest.importorskip("e2b")
pytest.importorskip("itsdangerous")
pytest.importorskip("authlib")

from backend.cloud.aliyun import app as cloud
from backend.cloud.aliyun.sandbox import AliyunSandboxBackend
from backend.sandbox.registry import SandboxRuntime, SandboxRuntimeRegistration, SandboxRuntimeRegistry
from backend.tests.test_aliyun_sandbox import FakeSDK
from backend.tests.test_hosted_profile import TestIdentityProvider, headers


@pytest.fixture
def cloud_client(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / ".oaw-persistent-volume").write_text("alice-workspace")
    values = {
        "OPEN_AGENT_WORLD_DATA_ROOT": str(profile), "OPEN_AGENT_WORLD_AGENT_RUNTIME": "core.mock",
        "OAW_ORGANIZATION_ID": "org", "OAW_WORKSPACE_ID": "alice-workspace", "OAW_OWNER_SUBJECT": "alice",
        "OAW_PUBLIC_ORIGIN": "https://oaw.example", "OAW_OIDC_ISSUER": "https://id.example",
        "OAW_OIDC_DISCOVERY_URL": "https://id.example/discovery", "OAW_OIDC_CLIENT_ID": "client",
        "OAW_OIDC_CLIENT_SECRET": "not-a-real-secret", "OAW_SESSION_SECRET": "test-secret" * 8,
        "OAW_ALIYUN_REGION": "cn-hangzhou", "OAW_ALIYUN_SANDBOX_API_KEY": "test-key",
        "OAW_ALIYUN_SANDBOX_TEMPLATE": "test-template", "OAW_ALIYUN_WORKSPACE_VOLUME": "alice-volume",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    class Identity(TestIdentityProvider):
        def __init__(self, settings):
            pass
        def wrap(self, application):
            return application
    monkeypatch.setattr(cloud, "OidcSessionIdentity", Identity)
    workspace = tmp_path / "remote-files"
    workspace.mkdir()
    sdk = FakeSDK(workspace)
    def registry_factory(settings):
        def build(root, event_sink):
            registry = SandboxRuntimeRegistry()
            backend = AliyunSandboxBackend(root, settings, event_sink, sdk=sdk)
            async def probe():
                return True, None
            registry.register(SandboxRuntimeRegistration(
                SandboxRuntime("aliyun.agent-sandbox", "Alibaba", "linux", ("/bin/sh", "-c"), supports_workspace=False,
                               supported_network_modes=("disabled", "enabled"), network_available=True),
                lambda: backend, probe))
            return registry
        return build
    monkeypatch.setattr(cloud, "registry_factory", registry_factory)
    with TestClient(cloud.create_app(), headers=headers("alice")) as client:
        yield client, sdk


def test_cloud_catalog_keeps_assets_and_removes_host_administration(cloud_client):
    client, sdk = cloud_client
    assert client.get("/api/world", headers=headers("bob")).status_code == 403
    runtimes = client.get("/api/sandbox/runtimes").json()
    assert [r["id"] for r in runtimes["runtimes"]] == ["aliyun.agent-sandbox"]
    assert not sdk.created
    plugins = client.app.state.services.plugins
    assert {p.id for p in plugins.plugins()} == {
        "open-agent-world.core", "matcreator", "science.structure-viewer", "data.sqlite"}
    for path in ("/api/packs/install", "/api/store/packs/fake/versions/1/install", "/api/desktop/inspect-path"):
        assert client.post(path, json={}).status_code == 403
    assert client.put("/api/settings/storage", json={}).status_code == 403
    for kind in ("matcreator.kdg", "data.sqlite"):
        response = client.post("/api/nodes", json={"type": kind})
        assert response.status_code == 201, response.text


def test_existing_sandbox_http_contract_with_cloud_adapter(cloud_client):
    client, sdk = cloud_client
    response = client.post("/api/nodes", json={"type": "sandbox", "config": {"command_timeout": 60}})
    assert response.status_code == 201, response.text
    sandbox_id = response.json()["id"]
    base = f"/api/sandboxes/{sandbox_id}"
    response = client.post(base + "/start")
    assert response.status_code == 200, response.text
    contents = b"cloud file contents" * 20000  # Exceeds one transfer chunk.
    response = client.post(base + "/files", params={"path": "sample.txt"}, content=contents)
    assert response.status_code == 201, response.text
    response = client.get(base + "/files", params={"operation": "download", "path": "sample.txt"})
    assert response.status_code == 200 and response.content == contents
    response = client.post(base + "/execute", json={"command": "printf hello", "timeout_seconds": 30})
    assert response.status_code == 200, response.text
    assert response.json()["stdout"] == "hello from remote\n"
    assert sdk.killed == ["remote-1"]
    response = client.get(base + "/files", params={"operation": "download", "path": "sample.txt"})
    assert response.content == contents


def test_personal_profile_default_is_not_accepted_by_cloud_entry(monkeypatch):
    monkeypatch.delenv("OPEN_AGENT_WORLD_DATA_ROOT", raising=False)
    with pytest.raises(ValueError, match="OPEN_AGENT_WORLD_DATA_ROOT"):
        cloud.create_app()


def test_cloud_matcreator_legion_can_be_instantiated_with_supported_policy(cloud_client):
    client, sdk = cloud_client
    response = client.post("/api/legions/presets/matcreator.native-research/instances", json={})
    assert response.status_code == 201, response.text
    world = client.get("/api/world").json()
    nodes = world["nodes"]
    assert any(node["type"] == "matcreator.agent" for node in nodes)
    assert any(node["type"] == "matcreator.kdg" for node in nodes)
    sandbox = next(node for node in nodes if node["type"] == "sandbox")
    assert sandbox["config"]["network_enabled"] is True
    assert sandbox["config"]["command_timeout"] == 60
    assert not sdk.created
    # Execute goes through the real on-demand start path without a separate Start.
    response = client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={"command": "printf hello"})
    assert response.status_code == 200, response.text
    assert response.json()["stdout"] == "hello from remote\n"
    assert sdk.killed == ["remote-1"]
    assert sdk.created[0]["allow_internet_access"] is True
    from oaw_matcreator.preset import native_definition
    personal = next(node for node in native_definition().nodes if node.type == "sandbox")
    assert personal.config["network_enabled"] is True
    assert "command_timeout" not in personal.config


def test_cloud_entry_requires_persistence_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_AGENT_WORLD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("OAW_WORKSPACE_ID", "alice")
    monkeypatch.delenv("OAW_ALIYUN_ALLOW_EPHEMERAL_PROFILE", raising=False)
    with pytest.raises(ValueError, match="persistent profile"):
        cloud.create_app()
