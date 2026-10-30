import asyncio
import json
import sqlite3
import time
from uuid import uuid4

from cryptography.fernet import InvalidToken
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.errors import ResourceValidationError
from backend.hosting.accounts import AccountStore
from backend.hosting.cli import enable_shared_models, initialize, provision
from backend.hosting.configuration import GatewaySettings, HostedProfile, read_gateway_settings
from backend.hosting.gateway import create_gateway
from backend.hosting.shared_models import SharedModelSource, service_token, worker_cipher
from backend.hosting.worker import WorkerConfig, create_worker_app, mint_worker_token
from backend.security.model_connections import CatalogEdit, ModelConnectionStore

ORIGIN = "https://hosted.example"
KEY = "provider-key-must-not-reach-browser"


def edit(revision=0, *, key=KEY):
    return {"revision": revision, "default_model": "oaw:model:chat", "connections": [{
        "id": "shared", "name": "Company models", "adapter": "openai", "base_url": "https://models.example/v1",
        "auth_mode": "api_key", "api_key": key, "models": [{"id": "chat", "name": "Shared assistant", "model_id": "company-chat"}],
    }]}


@pytest.fixture
def installation(tmp_path):
    accounts = AccountStore(tmp_path / "control/accounts.sqlite3")
    admin = accounts.create_user("admin", role="admin")
    users = [accounts.create_user("test1"), accounts.create_user("test2")]
    profiles = tuple(HostedProfile(str(uuid4()), user["id"], "canvas", "Canvas", "builder",
                                  f"http://worker-{index}:8000", f"{index + 1:02x}" * 32) for index, user in enumerate(users))
    settings = GatewaySettings(ORIGIN, accounts.path, profiles, shared_models_origin="http://127.0.0.1:8080")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("admin shell")
    gateway = create_gateway(settings, frontend, store=accounts)
    with TestClient(gateway, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield accounts, admin, users, profiles, client


def sign_in(accounts, client, user):
    client.cookies.set("oaw_session", accounts.create_session(user["id"]))


def test_admin_without_workspace_and_user_cannot_promote_or_modify_models(installation):
    accounts, admin, users, profiles, client = installation
    assert client.get("/hosting/admin/models").status_code == 401
    for user in users:
        sign_in(accounts, client, user)
        assert client.get("/hosting/admin/models").status_code == 403
        assert client.put("/hosting/admin/models", json=edit(), headers={"X-Role": "admin"}).status_code == 403
        assert client.get("/hosting/internal/models/" + profiles[0].profile_id).status_code == 403
    sign_in(accounts, client, admin)
    me = client.get("/auth/me").json()
    assert me["user"]["role"] == "admin" and me["instance"] is None
    assert client.get("/admin").text == "admin shell"
    assert client.get("/hosting/admin/status").json()["workspace_count"] == 2
    assert client.put("/hosting/admin/models", json=edit(), headers={"Origin": "https://evil.example"}).status_code == 403
    response = client.put("/hosting/admin/models", json=edit())
    assert response.status_code == 200 and KEY not in response.text
    assert response.json()["connections"][0]["api_key_configured"]
    assert client.put("/hosting/admin/models", json=edit()).status_code == 409
    accounts.set_role(admin["id"], "user")
    assert client.get("/hosting/admin/models").status_code == 401


def test_shared_secret_encrypted_at_rest_and_bound_to_worker_identity(installation):
    accounts, admin, users, profiles, client = installation
    sign_in(accounts, client, admin)
    assert client.put("/hosting/admin/models", json=edit()).status_code == 200
    raw = (accounts.path.parent / "services/settings.sqlite3").read_bytes()
    assert KEY.encode() not in raw
    profile = profiles[0]
    url = "/hosting/internal/models/" + profile.profile_id
    headers = {"Authorization": "Bearer " + service_token(profile.secret, profile.profile_id)}
    response = client.get(url, headers=headers)
    assert response.status_code == 200 and KEY not in response.text
    encrypted = response.json()["connections"][0]["api_key_encrypted"]
    assert worker_cipher(profile.secret).decrypt(encrypted.encode()).decode() == KEY
    with pytest.raises(InvalidToken):
        worker_cipher(profiles[1].secret).decrypt(encrypted.encode())
    for token in (
        service_token(profiles[1].secret, profile.profile_id),
        service_token(profile.secret, profile.profile_id, int(time.time()) - 61),
        mint_worker_token(profile.secret, profile.profile_id, profile.user_id),
    ):
        assert client.get(url, headers={"Authorization": "Bearer " + token}).status_code == 403
    accounts.set_enabled(users[0]["id"], False)
    assert client.get(url, headers=headers).status_code == 403


def test_workers_use_shared_models_through_existing_runtime_contract_and_cannot_write(installation, tmp_path, monkeypatch):
    accounts, admin, users, profiles, client = installation
    sign_in(accounts, client, admin)
    assert client.put("/hosting/admin/models", json=edit()).status_code == 200
    sources = []

    def dispatch(request):
        response = client.get(request.url.path, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content)

    class ConnectedSource(SharedModelSource):
        def __init__(self, config):
            super().__init__(config, transport=httpx.MockTransport(dispatch))
            sources.append(self)
    monkeypatch.setattr("backend.hosting.shared_models.SharedModelSource", ConnectedSource)
    for index, profile in enumerate(profiles):
        config = WorkerConfig(profile.profile_id, profile.user_id, "canvas", "builder", ORIGIN, profile.secret,
                              tmp_path / f"worker{index}", agent_runtime="core.mock", shared_models_origin="http://127.0.0.1:8080")
        app = create_worker_app(config)
        worker_headers = {"Origin": ORIGIN, "Authorization": "Bearer " + mint_worker_token(profile.secret, profile.profile_id, profile.user_id)}
        with TestClient(app, base_url=ORIGIN, headers=worker_headers) as worker:
            public = worker.get("/api/settings/models")
            assert public.status_code == 200 and KEY not in public.text
            assert public.json()["default_model"] == "oaw:model:chat"
            for path in ("/api/settings/models", "/api/settings/llm"):
                assert worker.put(path, json=edit()).status_code == 403
            assert worker.post("/api/settings/models/discover", json=edit()["connections"][0]).status_code == 403
            model_store = ModelConnectionStore(app.state.services.llm_settings)
            resolved = model_store.resolve_runtime("oaw:default")
            assert (resolved.model_id, resolved.api_key) == ("company-chat", KEY)
            assert resolved.context_window == 128000
            with pytest.raises(ResourceValidationError, match="administrator"):
                model_store.save(CatalogEdit.model_validate(edit(1)))
            with app.state.services.database.locked() as db:
                assert db.execute("SELECT 1 FROM application_settings WHERE key='model_connections'").fetchone() is None
            if index == 1:
                changed = edit(1, key="rotated-provider-key")
                assert client.put("/hosting/admin/models", json=changed).status_code == 200
                sources[-1].expires = 0
                assert model_store.resolve_runtime("oaw:default").api_key == "rotated-provider-key"
                changed = edit(2, key=None)
                changed["default_model"] = None
                changed["connections"][0]["enabled"] = False
                assert client.put("/hosting/admin/models", json=changed).status_code == 200
                sources[-1].expires = 0
                with pytest.raises(ResourceValidationError, match="disabled"):
                    model_store.resolve("oaw:model:chat")


def test_shared_admin_rejects_process_credentials_and_does_not_echo_invalid_secrets(installation):
    accounts, admin, users, profiles, client = installation
    sign_in(accounts, client, admin)
    value = edit()
    value["connections"][0].update(api_key=None, auth_mode="environment", environment_variable="OAW_ALIYUN_SANDBOX_API_KEY")
    assert client.put("/hosting/admin/models", json=value).status_code == 422
    value = edit()
    value["connections"][0]["base_url"] = "invalid:" + KEY
    response = client.put("/hosting/admin/models", json=value)
    assert response.status_code == 422 and KEY not in response.text


def test_shared_model_refresh_failure_does_not_reuse_stale_enabled_catalog(tmp_path):
    profile, user = str(uuid4()), str(uuid4())
    config = WorkerConfig(profile, user, "canvas", "builder", ORIGIN, "ab" * 32, tmp_path,
                          shared_models_origin="http://127.0.0.1:8080")
    source = SharedModelSource(config, transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    source.cached = {"revision": 1, "connections": [], "default_model": None}
    source.expires = 0
    with pytest.raises(ResourceValidationError, match="unavailable"):
        source.read()
    assert source.cached is None


def test_old_accounts_remain_users_and_new_workspaces_inherit_shared_models(tmp_path):
    initialize(tmp_path, ORIGIN)
    path = tmp_path / "control/accounts.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE users DROP COLUMN role")
    accounts = AccountStore(path)
    alice = accounts.create_user("alice")
    assert alice["role"] == "user"
    first = asyncio.run(provision(tmp_path, alice["id"], agent_runtime="core.mock"))
    enable_shared_models(tmp_path, "http://127.0.0.1:8080")
    bob = accounts.create_user("bob")
    second = asyncio.run(provision(tmp_path, bob["id"], agent_runtime="core.mock"))
    assert read_gateway_settings(tmp_path / "control/hosting.json").shared_models_origin == "http://127.0.0.1:8080"
    for profile in (first, second):
        value = json.loads((tmp_path / "instances" / profile.profile_id / "worker.json").read_text())
        assert value["shared_models_origin"] == "http://127.0.0.1:8080"
