"""Real ASGI HTTP/worker WebSocket checks; no ECS, systemd or cloud is started."""
import asyncio
from contextlib import AsyncExitStack, closing
from dataclasses import replace
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.hosting.accounts import AccountStore
from backend.hosting.configuration import read_gateway_settings
from backend.hosting.gateway import create_gateway
from backend.hosting.preview import create_preview_app, create_preview_services, initialize_preview, preview_units, require_nonroot
from backend.hosting.upgrades import migrate_profile
from backend.hosting.worker import WorkerConfig, mint_worker_token
from backend.tests.conversation_helpers import wait_for_conversation_delivery


ORIGIN = "https://preview.example"
PASSWORDS = {"alice": "preview-alice-password-728", "bob": "preview-bob-password-419"}


@pytest.fixture
def fixture(tmp_path, request):
    directory = tmp_path / "preview"
    origin, allow_local_http = getattr(request, "param", (ORIGIN, False))
    receipt = asyncio.run(initialize_preview(directory, origin, PASSWORDS, allow_local_http=allow_local_http))
    settings = read_gateway_settings(directory / "control/hosting.json")
    return directory, receipt, settings


def worker_config(directory, profile):
    root = directory / "instances" / profile.profile_id
    value = json.loads((root / "worker.json").read_text())
    return WorkerConfig(**{**value, "data_root": root / value["data_root"]})


def headers(config):
    return {"Origin": config.public_origin, "Authorization": "Bearer " + mint_worker_token(
        config.secret, config.profile_id, config.user_id)}


@pytest.mark.parametrize('fixture', [(ORIGIN, False), ('http://127.0.0.1:8765', True), ('http://[::1]:8765', True)], indirect=True)
def test_preview_fixture_real_http_websocket_and_mock_conversation(fixture):
    directory, receipt, settings = fixture
    config = worker_config(directory, settings.profiles[0])
    app = create_preview_app(config)
    ids = receipt["fixtures"][0]["cards"]
    with TestClient(app) as client:
        supplied = headers(config)
        assert client.get("/api/world").status_code == 401
        assert client.get("/api/application", headers=supplied).json()["mode"] == "preview"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events", headers={"Origin": config.public_origin}):
                pytest.fail("Unauthenticated Preview WebSocket accepted")
        with client.websocket_connect("/ws/events", headers=supplied) as socket:
            assert socket.receive_json()["type"] == "connection_ready"
            created = client.post("/api/nodes", headers=supplied, json={"type": "text", "content": "Preview edit"})
            assert created.status_code == 201, created.text
            assert socket.receive_json()["node_id"] == created.json()["id"]
        session = client.post(f"/api/conversations/{ids['conversation']}/sessions", headers=supplied,
            json={"title": "Preview check", "participant_ids": [ids["agent"]]})
        assert session.status_code == 201, session.text
        session_id = session.json()["id"]
        posted = client.post(f"/api/conversations/{ids['conversation']}/sessions/{session_id}/messages",
                             headers=supplied, json={"content": "Hello Preview", "mention_agent_ids": [ids["agent"]]})
        assert posted.status_code == 202, posted.text
        client.portal.call(wait_for_conversation_delivery, app.state.services, ids["conversation"], session_id)
        messages = client.get(f"/api/conversations/{ids['conversation']}/sessions/{session_id}/messages", headers=supplied)
        replies = [message for message in messages.json() if message["sender_id"] == ids["agent"]]
        assert len(replies) == 1 and replies[0]["content"].startswith("Mock response: "), messages.text
        assert replies[0]["content"].endswith("latest message: Hello Preview")
        services = app.state.services
        assert services.sandbox_backend is None
        assert services.settings.inherit_runtime_environment is False


def test_preview_denies_paid_cloud_and_cross_profile_authority(fixture, monkeypatch):
    directory, _, settings = fixture
    alice = worker_config(directory, settings.profiles[0])
    bob = worker_config(directory, settings.profiles[1])

    def unexpected_network(*args, **kwargs):
        pytest.fail("Preview attempted paid model discovery")

    monkeypatch.setattr("backend.security.model_discovery.httpx.AsyncClient", unexpected_network)
    with TestClient(create_preview_app(alice)) as client:
        supplied = headers(alice)
        for method, path in (("post", "/api/settings/models/discover"), ("put", "/api/settings/models"),
                             ("put", "/api/settings/llm"), ("post", "/api/sandboxes/fixture/start"),
                             ("post", "/api/sandboxes/fixture/execute")):
            response = getattr(client, method)(path, headers=supplied, json={})
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "preview_operation_disabled"
        paid = client.post("/api/nodes", headers=supplied,
            json={"type": "agent", "config": {"runtime_provider_id": "google.adk"}})
        assert paid.status_code == 422, paid.text
        assert "google.adk" not in client.app.state.services.run_manager._providers
        assert client.get("/api/world", headers=headers(bob)).status_code == 401
        assert client.put("/api/settings/storage", headers=supplied, json={"path": "/srv/production"}).status_code == 403
    with TestClient(create_preview_app(bob)) as client:
        private = json.loads((directory / "fixture-receipt.json").read_text())["fixtures"][0]["cards"]["note"]
        assert client.get("/api/nodes/" + private, headers=headers(bob)).status_code == 404


def test_preview_upgrade_keeps_both_profiles_saved_data_and_core_only_policy(fixture):
    directory, _, settings = fixture
    for index, profile in enumerate(settings.profiles):
        config = worker_config(directory, profile)
        binding = (config.data_root / "hosted-profile.json").read_bytes()
        content = f"Saved Preview data for fixture {index}"
        with TestClient(create_preview_app(config)) as client:
            response = client.post("/api/nodes", headers=headers(config), json={"type": "text", "content": content})
            assert response.status_code == 201, response.text
            saved = response.json()["id"]
        result = asyncio.run(migrate_profile(config, services_factory=create_preview_services))
        assert result == {"profile": config.profile_id, "ready": True, "changed_contracts": []}
        assert (config.data_root / "hosted-profile.json").read_bytes() == binding
        with TestClient(create_preview_app(config)) as client:
            services = client.app.state.services
            assert services.resources.read_text(saved).content == content
            assert set(services.run_manager._providers) == {"core.mock"}
            assert all(failure["phase"] == "hosting_policy" for failure in services.plugins.load_failures)
            paid = client.post("/api/nodes", headers=headers(config),
                               json={"type": "agent", "config": {"runtime_provider_id": "google.adk"}})
            assert paid.status_code == 422, paid.text


@pytest.mark.parametrize("provider,phase,error,message", [
    ("unexpected.preview.runtime", None, ValueError, "Pack startup or migration failed"),
    ("core.mock", "hosting_policy", RuntimeError, "Unexpected runtime startup failure"),
])
def test_preview_upgrade_still_aborts_for_unexpected_runtime_startup_failure(
        fixture, monkeypatch, provider, phase, error, message):
    from backend.services import ApplicationServices

    directory, _, settings = fixture
    config = worker_config(directory, settings.profiles[0])
    binding = (config.data_root / "hosted-profile.json").read_bytes()
    startup = ApplicationServices.startup

    async def fail(services):
        await startup(services)
        arguments = {} if phase is None else {"phase": phase}
        services.run_manager.unavailable_runtime(provider, RuntimeError("Unexpected runtime startup failure"), **arguments)

    monkeypatch.setattr(ApplicationServices, "startup", fail)
    with pytest.raises(error, match=message):
        asyncio.run(migrate_profile(config, services_factory=create_preview_services))
    assert (config.data_root / "hosted-profile.json").read_bytes() == binding


@pytest.mark.asyncio
@pytest.mark.parametrize('fixture', [(ORIGIN, False), ('http://127.0.0.1:8765', True), ('http://[::1]:8765', True)], indirect=True)
async def test_preview_and_production_sessions_and_databases_are_independent(fixture, tmp_path):
    directory, _, settings = fixture
    preview_accounts = AccountStore(settings.accounts_path)
    production_accounts = AccountStore(tmp_path / "synthetic-production" / "accounts.sqlite3")
    production_user = production_accounts.create_user("alice", "Synthetic production Alice", PASSWORDS["alice"])
    production_cookie = production_accounts.create_session(production_user["id"])
    preview_cookie = preview_accounts.create_session(settings.profiles[0].user_id)
    assert preview_cookie != production_cookie
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>Preview shell</html>")
    # Dispatch into real worker ASGI apps. The transport selects local apps;
    # it does not fabricate application responses or worker authentication.
    profiles = tuple(replace(profile, upstream=f"http://worker-{index}:8000")
                     for index, profile in enumerate(settings.profiles))
    workers = {profile.upstream: create_preview_app(worker_config(directory, original))
               for profile, original in zip(profiles, settings.profiles)}

    async def dispatch(request):
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        return await httpx.ASGITransport(app=workers[origin]).handle_async_request(request)

    gateway = create_gateway(replace(settings, profiles=profiles), frontend, transport=httpx.MockTransport(dispatch))
    async with AsyncExitStack() as stack:
        for app in workers.values():
            await stack.enter_async_context(app.router.lifespan_context(app))
        await stack.enter_async_context(gateway.app.router.lifespan_context(gateway.app))
        origin = settings.public_origin
        client = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=origin))
        client.cookies.set("oaw_session", production_cookie)
        assert (await client.get("/api/world")).status_code == 401
        client.cookies.clear()
        login = await client.post("/auth/login", headers={"Origin": origin},
            json={"username": "alice", "password": PASSWORDS["alice"]})
        assert login.status_code == 200, login.text
        cookie = login.headers['set-cookie'].lower()
        assert ('; secure' in cookie) is (not settings.allow_local_http)
        assert '; httponly' in cookie and '; samesite=lax' in cookie and '; path=/' in cookie
        assert 'domain=' not in cookie
        assert production_accounts.get_session(client.cookies["oaw_session"]) is None
        assert (await client.get("/api/world")).status_code == 200
        rejected = await client.post('/auth/login', headers={'Origin': 'https://production.example'},
            json={'username': 'alice', 'password': PASSWORDS['alice']})
        assert rejected.status_code == 403
        alice_note = await client.post("/api/nodes", headers={"Origin": origin, "X-OAW-Instance": profiles[0].profile_id},
                                      json={"type": "text", "content": "Alice private Preview data"})
        assert alice_note.status_code == 201, alice_note.text
        client.cookies.clear()
        login = await client.post("/auth/login", headers={"Origin": origin},
            json={"username": "bob", "password": PASSWORDS["bob"]})
        assert login.status_code == 200
        assert (await client.get("/api/nodes/" + alice_note.json()["id"])).status_code == 404


def test_fixture_initialization_never_resets_accounts_and_rejects_root(fixture, monkeypatch):
    directory, receipt, settings = fixture
    assert len({profile.secret for profile in settings.profiles}) == 2
    assert all(profile.upstream.startswith("unix:///run/oaw-preview/") for profile in settings.profiles)
    assert {row["username"] for row in receipt["fixtures"]} == {"alice", "bob"}
    assert all(password not in json.dumps(receipt) for password in PASSWORDS.values())
    with pytest.raises(ValueError, match="new, empty"):
        asyncio.run(initialize_preview(directory, ORIGIN, PASSWORDS))
    monkeypatch.setattr("backend.hosting.preview.os.geteuid", lambda: 0, raising=False)
    with pytest.raises(ValueError, match="unprivileged"):
        require_nonroot()


def test_initialization_creates_idle_deployment_database_without_starting_services(tmp_path, monkeypatch):
    def unexpected_command(*args, **kwargs):
        pytest.fail("Fixture initialization attempted to start a system service")

    monkeypatch.setattr("subprocess.run", unexpected_command)
    directory = tmp_path / "preview"
    asyncio.run(initialize_preview(directory, ORIGIN, PASSWORDS))
    path = directory / "control/deployments.sqlite3"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as database:
        assert database.execute("SELECT COUNT(*) FROM hosting_jobs").fetchone() == (0,)
        assert database.execute("SELECT COUNT(*) FROM hosting_entries").fetchone() == (0,)


def test_preview_units_render_host_boundaries_without_running_systemd(fixture):
    _, _, settings = fixture
    roots = ["/opt/oaw-production", "/srv/oaw-production", "/srv/oaw-production-releases", "/var/lib/oaw-operator"]
    units = preview_units(settings, directory="/srv/oaw-preview", application="/opt/oaw-preview",
                          service="oaw-preview", port=8081, verify_user="oaw-preview-verify", production_roots=roots,
                          execution_root="/opt/oaw-preview-execution")
    gateway = units["oaw-preview-gateway.service"]
    assert "IPAddressDeny=any\n" in gateway and "IPAddressAllow=" not in gateway
    assert "PrivateNetwork=yes\n" in gateway and "RestrictAddressFamilies=AF_UNIX\n" in gateway
    assert "Requires=oaw-preview-gateway.socket\n" in gateway
    assert "Sockets=oaw-preview-gateway.socket\n" in gateway
    assert "gateway --config /var/lib/oaw-preview-control/hosting.json --fd 3\n" in gateway
    assert "ListenStream=/run/oaw-preview/gateway.sock\n" in units["oaw-preview-gateway.socket"]
    assert "SocketGroup=www-data\nSocketMode=0660\n" in units["oaw-preview-gateway.socket"]
    assert "Wants=oaw-preview-gateway.socket oaw-preview-gateway.service" in units["oaw-preview.target"]
    assert "MemoryMax=128M\n" in gateway and "TasksMax=128\n" in gateway
    assert "RootDirectory=/opt/oaw-preview-execution\nMountAPIVFS=yes\nPrivateIPC=yes\nProcSubset=pid\n" in gateway
    assert "BindReadOnlyPaths=/opt/oaw-preview:/opt/oaw-preview /run/oaw-preview:/run/oaw-preview\n" in gateway
    for profile in settings.profiles:
        worker = units[f"oaw-preview-worker-{profile.profile_id}.service"]
        assert "PrivateNetwork=yes" in worker and "RestrictAddressFamilies=AF_UNIX\n" in worker
        assert "MemoryMax=384M\n" in worker and "TasksMax=128\n" in worker
        assert " -m backend.hosting.preview worker " in worker
        assert "EnvironmentFile=" not in worker
        assert "InaccessiblePaths=" not in worker
        assert "RootDirectory=/opt/oaw-preview-execution\n" in worker
        assert "BindReadOnlyPaths=/opt/oaw-preview:/opt/oaw-preview /srv/oaw-preview/instances/" in worker
        socket = units[f"oaw-preview-worker-{profile.profile_id}.socket"]
        assert "SocketGroup=oaw-preview-verify\nSocketMode=0660" in socket
    with pytest.raises(ValueError, match="separate"):
        preview_units(settings, directory="/srv/oaw-preview", application="/opt/oaw-preview",
                      service="oaw-preview", port=8081, verify_user="oaw-preview-verify", production_roots=["/opt/oaw-preview"],
                      execution_root="/opt/oaw-preview-execution")
    with pytest.raises(ValueError, match="shared"):
        create_preview_app(replace(worker_config(fixture[0], settings.profiles[0]), shared_models_origin="https://production.example"))
