import asyncio
from dataclasses import replace
import json
import time
from uuid import uuid4

from cryptography.fernet import InvalidToken
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.errors import ResourceValidationError
from backend.execution_config import configuration_summary, resolve_sandbox_configuration
from backend.hosting.accounts import AccountStore
from backend.hosting.cli import enable_shared_environment, initialize, provision
from backend.hosting.configuration import GatewaySettings, HostedProfile, read_gateway_settings
from backend.hosting.gateway import create_gateway
from backend.hosting.shared_environment import SharedEnvironmentSource, environment_cipher
from backend.hosting.shared_models import service_token
from backend.hosting.worker import WorkerConfig, create_worker_app, mint_worker_token
from backend.tests.conftest import create_node
from backend.tests.test_skill_packages import edit as edit_document
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.tests.test_tool_projection import tools, invoke

ORIGIN = "https://hosted.example"
KEY = "shared-execution-test-secret"


def edit(revision=0, key=KEY):
    return {"revision": revision, "environment_variables": {"REGION": "shared", "API_TOKEN": {"secret_ref": "token"}},
            "secrets": {} if key is None else {"token": key}}


@pytest.fixture
def installation(tmp_path):
    accounts = AccountStore(tmp_path / "control/accounts.sqlite3")
    admin = accounts.create_user("admin", role="admin")
    users = [accounts.create_user("test1"), accounts.create_user("test2")]
    profiles = tuple(HostedProfile(str(uuid4()), user["id"], "canvas", "Canvas", "builder",
                                  f"http://worker-{i}:8000", f"{i + 1:02x}" * 32) for i, user in enumerate(users))
    settings = GatewaySettings(ORIGIN, accounts.path, profiles, shared_environment_origin="http://127.0.0.1:8080")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("shell")
    with TestClient(create_gateway(settings, frontend, store=accounts), base_url=ORIGIN,
                    headers={"Origin": ORIGIN}) as client:
        yield accounts, admin, users, profiles, client


def sign_in(accounts, client, user):
    client.cookies.set("oaw_session", accounts.create_session(user["id"]))


def connected_source(client, profile, tmp_path):
    config = WorkerConfig(profile.profile_id, profile.user_id, "canvas", "builder", ORIGIN, profile.secret,
                          tmp_path, agent_runtime="core.mock", shared_environment_origin="http://127.0.0.1:8080")

    def dispatch(request):
        response = client.get(request.url.path, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content)

    return SharedEnvironmentSource(config, transport=httpx.MockTransport(dispatch))


def test_admin_scope_encryption_binding_rotation_deletion_and_revision(installation):
    accounts, admin, users, profiles, client = installation
    path = "/hosting/admin/environment"
    assert client.get(path).status_code == 401
    for user in users:
        sign_in(accounts, client, user)
        assert client.get(path).status_code == 403
        assert client.put(path, json=edit()).status_code == 403
    sign_in(accounts, client, admin)
    assert client.put(path, json=edit(), headers={"Origin": "https://other.example"}).status_code == 403
    saved = client.put(path, json=edit())
    assert saved.status_code == 200 and KEY not in saved.text
    assert saved.json()["secret_bindings"] == {"token": True}
    assert KEY.encode() not in (accounts.path.parent / "services/environment.sqlite3").read_bytes()
    assert client.get(path).json() == saved.json()
    assert client.put(path, json=edit()).status_code == 409
    assert client.put(path, json=edit(1, key=None)).status_code == 200
    internal = "/hosting/internal/environment/" + profiles[0].profile_id
    assert client.get(internal).status_code == 403
    headers = {"Authorization": "Bearer " + service_token(profiles[0].secret, profiles[0].profile_id)}
    response = client.get(internal, headers=headers)
    assert response.status_code == 200 and KEY not in response.text
    encrypted = response.json()["encrypted_secrets"]["token"]
    assert environment_cipher(profiles[0].secret).decrypt(encrypted.encode()).decode() == KEY
    with pytest.raises(InvalidToken):
        environment_cipher(profiles[1].secret).decrypt(encrypted.encode())
    for token in (service_token(profiles[1].secret, profiles[0].profile_id),
                  service_token(profiles[0].secret, profiles[0].profile_id, int(time.time()) - 61),
                  mint_worker_token(profiles[0].secret, profiles[0].profile_id, profiles[0].user_id)):
        assert client.get(internal, headers={"Authorization": "Bearer " + token}).status_code == 403
    rotated = client.put(path, json=edit(2, key="replacement"))
    assert rotated.status_code == 200
    assert client.put(path, json={"revision": 3, "environment_variables": {}}).json()["secret_bindings"] == {}
    assert client.get(internal, headers=headers).json()["encrypted_secrets"] == {}
    accounts.set_enabled(users[0]["id"], False)
    assert client.get(internal, headers=headers).status_code == 403


@pytest.mark.parametrize("variables", [
    {"PATH": KEY}, {"OAW_ALIYUN_SANDBOX_API_KEY": KEY}, {"PYTHONPATH": KEY},
    {"MODEL": {"path_ref": "folder"}}, {"MODEL": {"file_ref": "file"}},
    {"A": "one", "a": "two"}, {"REGION": "nul\0"}, {"A": "x" * 24000},
])
def test_invalid_settings_do_not_echo_secrets_or_change_saved_config(installation, variables):
    accounts, admin, _, _, client = installation
    sign_in(accounts, client, admin)
    response = client.put("/hosting/admin/environment", json={"revision": 0, "environment_variables": variables})
    assert response.status_code == 422 and KEY not in response.text
    assert client.get("/hosting/admin/environment").json()["revision"] == 0


def test_workers_inherit_shared_values_without_copying_credentials_to_profiles(installation, tmp_path, monkeypatch):
    accounts, admin, _, profiles, client = installation
    sign_in(accounts, client, admin)
    assert client.put("/hosting/admin/environment", json=edit()).status_code == 200
    for i, profile in enumerate(profiles):
        source = connected_source(client, profile, tmp_path / str(i))
        monkeypatch.setattr("backend.hosting.shared_environment.SharedEnvironmentSource", lambda config: source)
        app = create_worker_app(source.config)
        headers = {"Origin": ORIGIN, "Authorization": "Bearer " + mint_worker_token(profile.secret, profile.profile_id, profile.user_id)}
        with TestClient(app, base_url=ORIGIN, headers=headers) as worker:
            sandbox = create_node(worker, "sandbox")
            services = app.state.services
            assert resolve_sandbox_configuration(services, sandbox["id"]) == ({"REGION": "shared", "API_TOKEN": KEY}, (KEY,))
            summary = configuration_summary(services, sandbox["id"])
            assert KEY not in json.dumps(summary)
            assert all(v["source"] == "shared" for v in summary["variables"])
            assert worker.put("/api/settings/sandbox", json={}).status_code == 403
            assert worker.get("/api/settings/sandbox").status_code == 403
            with services.database.locked() as db:
                values = str([list(row) for row in db.execute("SELECT * FROM application_settings")])
                assert KEY not in values and "global_execution_credential:" not in values
            if i == 1:
                assert client.put("/hosting/admin/environment", json=edit(1, key="rotated")).status_code == 200
                assert resolve_sandbox_configuration(services, sandbox["id"])[0]["API_TOKEN"] == "rotated"
                edit_document(worker, sandbox, "replace", {"variables": {"api_token": "private"}})
                assert resolve_sandbox_configuration(services, sandbox["id"]) == ({"REGION": "shared", "api_token": "private"}, ())
                assert client.put("/hosting/admin/environment", json={"revision": 2, "environment_variables": {}}).status_code == 200
                assert resolve_sandbox_configuration(services, sandbox["id"])[0] == {"api_token": "private"}


def test_shared_secrets_reach_only_authorized_commands_and_are_redacted(installation, runtime_client, monkeypatch, tmp_path):
    accounts, admin, _, profiles, gateway = installation
    sign_in(accounts, gateway, admin)
    assert gateway.put("/hosting/admin/environment", json=edit()).status_code == 200
    client, backend, native = runtime_client
    services = client.app.state.services
    services.execution_environment_source = connected_source(gateway, profiles[0], tmp_path / "source")
    agent, sandbox, *_ = setup_skill(client)
    original = native.run_appcontainer

    def emitting(*args, **kwargs):
        return replace(original(*args, **kwargs), stdout=KEY, stderr=KEY)

    monkeypatch.setattr(native, "run_appcontainer", emitting)
    provider, definitions = tools(client, agent)
    result = invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"])
    assert native.last_environment["API_TOKEN"] == KEY
    assert result["stdout"] == result["stderr"] == "[REDACTED]"
    services.execution_environment_source.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    previous = native.last_environment
    with pytest.raises(ResourceValidationError, match="unavailable"):
        invoke(client, provider, agent, definitions["execute_command"], sandbox=sandbox["id"], argv=["cmd.exe"])
    assert native.last_environment is previous


def test_existing_and_future_profiles_inherit_enabled_environment(tmp_path):
    initialize(tmp_path, ORIGIN)
    accounts = AccountStore(tmp_path / "control/accounts.sqlite3")
    first = asyncio.run(provision(tmp_path, accounts.create_user("first")["id"], agent_runtime="core.mock"))
    enable_shared_environment(tmp_path, "http://127.0.0.1:8080")
    second = asyncio.run(provision(tmp_path, accounts.create_user("second")["id"], agent_runtime="core.mock"))
    assert read_gateway_settings(tmp_path / "control/hosting.json").shared_environment_origin == "http://127.0.0.1:8080"
    for profile in (first, second):
        value = json.loads((tmp_path / "instances" / profile.profile_id / "worker.json").read_text())
        assert value["shared_environment_origin"] == "http://127.0.0.1:8080"
        assert value["shared_models_origin"] is None
