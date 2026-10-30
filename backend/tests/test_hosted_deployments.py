import asyncio
from dataclasses import asdict, replace
import json
from uuid import uuid4
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from backend.hosting.configuration import HostedProfile, write_private_json
from backend.hosting.deployments import DeploymentStore
from backend.hosting.gateway import create_gateway
from backend.hosting.provisioner import timer_units
from backend.hosting.templates import export_template
from backend.tests.test_deployments import prepare_source
from backend.tests.test_hosted_gateway import fixture_data, ORIGIN


@pytest.fixture
def template(tmp_path):
    source = tmp_path / "source"
    data = prepare_source(source)
    return asyncio.run(export_template(source, data["release"]["id"], tmp_path / "portable.json"))


def test_admin_deployments_are_durable_idempotent_and_do_not_reassign_existing_profiles(tmp_path, template, monkeypatch):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    store.set_role(users[0]["id"], "admin")
    from backend.hosting.worker import WorkerIdentityProvider
    identity = WorkerIdentityProvider(profiles[0].secret, profiles[0].profile_id, profiles[0].user_id)
    clock, calls = [1_000_000.0], []
    monkeypatch.setattr('backend.hosting.worker.time', SimpleNamespace(time=lambda: clock[0]))
    async def source(request):
        if not calls:
            clock[0] += 59
        calls.append(request)
        if await identity.authenticate({'headers': request.headers.raw}) is None:
            return httpx.Response(401)
        return httpx.Response(200, json=template if '/hosting-template/' in request.url.path else [
            {'id': template['source_release_id'], 'name': 'App', 'legion_id': 'legion', 'created_at': template['created_at']}])
    transport = httpx.MockTransport(source)
    with TestClient(create_gateway(settings, frontend, store=store, transport=transport), base_url=ORIGIN) as client:
        payload = {"request_id": str(uuid4()), "release_id": template["source_release_id"], "user_ids": [users[1]["id"]], "mode": "runtime"}
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert client.get("/hosting/admin/deployments").status_code == 403
        assert client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN}).status_code == 403
        assert client.get("/api/hosting-template/" + template["source_release_id"]).status_code == 403
        client.cookies.set("oaw_session", store.create_session(users[0]["id"]))
        assert client.post("/hosting/admin/deployments", json=payload).status_code == 403
        assert client.post("/hosting/admin/deployments", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
        assert not calls  # Authentication and origin rejection cannot wake the engineering worker.
        first = client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN})
        assert first.status_code == 202, first.text
        assert calls[0].url.path == '/api/deployment' and 'authorization' not in calls[0].headers
        assert calls[1].url.path == '/api/hosting-template/' + template['source_release_id']
        repeat = client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN})
        assert repeat.json() == first.json()
        assert len(client.get("/hosting/admin/deployments").json()["jobs"]) == 1
        assert client.post("/hosting/admin/deployments", json={**payload, "mode": "builder"}, headers={"Origin": ORIGIN}).status_code == 409
        assert client.post("/hosting/admin/deployments", json={**payload, "user_ids": [users[0]["id"]]}, headers={"Origin": ORIGIN}).status_code == 409
        assert client.post("/hosting/admin/deployments", json={**payload, "upstream": "file:///root"}, headers={"Origin": ORIGIN}).status_code == 409
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert client.get("/auth/me").json()["instance"]["id"] == profiles[1].profile_id
    assert DeploymentStore(settings.accounts_path.parent / "deployments.sqlite3").jobs()[0]["status"] == "queued"


def test_live_assignment_preserves_routes_rejects_foreign_profile_and_invalidates_old_tabs(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    store.set_role(users[0]["id"], "admin")
    path = settings.accounts_path.parent / "hosting.json"
    def save(current):
        write_private_json(path, {"version": 1, "public_origin": ORIGIN, "profiles": [asdict(p) for p in current]})
    save(profiles)
    extra = HostedProfile(str(uuid4()), users[1]["id"], "new", "New workspace", "runtime", "http://worker-new", "ab" * 32)
    calls = []
    def worker(request):
        calls.append(request)
        if request.url.path == '/api/deployment':
            return httpx.Response(401)
        return httpx.Response(200, json=[] if request.url.path == "/api/deployments" else {"host": request.url.host})
    with TestClient(create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(worker), settings_path=path), base_url=ORIGIN) as client:
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert client.get("/auth/me").json()["instance"]["id"] == profiles[1].profile_id
        save((*profiles, extra))
        assert client.get("/auth/me").json()["instance"]["id"] == profiles[1].profile_id
        client.cookies.set("oaw_session", store.create_session(users[0]["id"]))
        assert client.put("/hosting/admin/entry", json={"user_id": users[1]["id"], "profile_id": profiles[0].profile_id}, headers={"Origin": ORIGIN}).status_code == 409
        assert client.put("/hosting/admin/entry", json={"user_id": users[1]["id"], "profile_id": extra.profile_id}, headers={"Origin": ORIGIN}).status_code == 200
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert client.get("/auth/me").json()["instance"]["id"] == extra.profile_id
        count = len(calls)
        assert client.post("/api/nodes", json={}, headers={"Origin": ORIGIN, "X-OAW-Instance": profiles[1].profile_id}).status_code == 409
        assert len(calls) == count
        assert client.get("/api/runtime-app").json()["host"] == "worker-new"
        client.cookies.set("oaw_session", store.create_session(users[0]["id"]))
        assert client.put("/hosting/admin/entry", json={"user_id": users[1]["id"], "profile_id": profiles[1].profile_id}, headers={"Origin": ORIGIN}).status_code == 200
        assert len(client.get("/hosting/admin/deployments").json()["profiles"]) == 3


@pytest.mark.parametrize('operation', ['catalog', 'deploy'])
@pytest.mark.parametrize('change', ['logout', 'role', 'source'])
def test_engineering_access_changed_during_startup_never_reads_source_or_enqueues(tmp_path, monkeypatch, operation, change):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    store.set_role(users[0]['id'], 'admin')
    extra = replace(profiles[0], profile_id=str(uuid4()), upstream='http://engineering-new', secret='ab' * 32)
    settings = replace(settings, profiles=(*profiles, extra))
    token = store.create_session(users[0]['id'])
    deployments = DeploymentStore(tmp_path / 'deployments.sqlite3')
    calls = []
    def probe(request):
        calls.append(request)
        if change == 'logout':
            store.revoke_session(token)
        elif change == 'role':
            store.set_role(users[0]['id'], 'user')
        else:
            deployments.assign(users[0]['id'], extra.profile_id, 'operator')
        return httpx.Response(401)
    monkeypatch.setattr('backend.hosting.deployments.mint_worker_token',
                        lambda *args, **kwargs: pytest.fail('Changed engineering access must not be signed'))
    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(probe))
    with TestClient(gateway, base_url=ORIGIN) as client:
        client.cookies.set('oaw_session', token)
        if operation == 'catalog':
            response = client.get('/hosting/admin/deployments')
        else:
            response = client.post('/hosting/admin/deployments', headers={'Origin': ORIGIN}, json={
                'request_id': str(uuid4()), 'release_id': str(uuid4()), 'user_ids': [users[1]['id']], 'mode': 'runtime'})
        assert response.status_code == (409 if change == 'source' else 401)
    assert [(request.method, request.url.path) for request in calls] == [('GET', '/api/deployment')]
    assert deployments.jobs() == []


def test_deployment_timer_contains_only_operator_fixed_paths():
    units = timer_units("/srv/oaw-hosting", "/opt/open-agent-world")
    assert "User=root" in units["oaw-hosted-provisioner.service"]
    assert "OnUnitInactiveSec=5" in units["oaw-hosted-provisioner.timer"]
    with pytest.raises(ValueError):
        timer_units("/srv/oaw-hosting;evil", "/opt/open-agent-world")
    with pytest.raises(ValueError):
        timer_units("/srv/oaw-hosting", "/opt/open-agent-world", name="../../evil")


@pytest.mark.parametrize("mode", ["builder", "runtime"])
def test_template_bootstrap_uses_operator_factory_and_preserves_requested_interface(tmp_path, template, mode):
    from backend.hosting.templates import initialize_runtime
    from backend.hosting.worker import WorkerConfig, create_worker_services, create_worker_app, mint_worker_token
    config = WorkerConfig(str(uuid4()), str(uuid4()), "app", mode, ORIGIN, "cc" * 32,
                          tmp_path / "new-profile", agent_runtime="core.mock")
    path = tmp_path / "template.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    seen = []
    def factory(settings):
        seen.append(settings.tenant_scope.world_id)
        return create_worker_services(settings)
    asyncio.run(initialize_runtime(config, path, services_factory=factory))
    assert seen == [config.profile_id]
    assert (config.data_root / "deployment.json").exists() == (mode == "runtime")
    headers = {"Authorization": "Bearer " + mint_worker_token(config.secret, config.profile_id, config.user_id),
               "Origin": ORIGIN}
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>Static fallback</html>", encoding="utf-8")
    with TestClient(create_worker_app(config, services_factory=factory, frontend_directory=frontend), headers=headers) as client:
        assert client.get("/api/deployment").json()["mode"] == mode
        assert client.get("/api/world").status_code == (200 if mode == "builder" else 404)
        if mode == "builder":
            legion = next(n for n in client.get("/api/world").json()["nodes"] if n["type"] == "legion")
            release = client.post("/api/deployments", json={"legion_id": legion["id"], "name": "Hosted export"}).json()
            response = client.get("/api/hosting-template/" + release["id"])
            assert response.status_code == 200, response.text
            assert response.json()["source_release_id"] == release["id"]
    with pytest.raises(ValueError, match="overwrites"):
        asyncio.run(initialize_runtime(config, path, services_factory=factory))


@pytest.mark.parametrize("network_enabled", [True, False])
def test_hosted_publication_preserves_approved_sandbox_policy(tmp_path, network_enabled):
    from backend.main import create_app
    from backend.tests.test_deployments import node, settings
    from backend.hosting.templates import initialize_runtime
    from backend.hosting.worker import WorkerConfig
    from backend.persistence.database import Database

    source = tmp_path / "source"
    records = prepare_source(source)
    policy = {"network_enabled": network_enabled,
              "memory_bytes": 2147483648, "active_process_limit": 64, "command_timeout": 137}
    with TestClient(create_app(settings(source))) as client:
        node(client, "sandbox", "Research", parent_id=records["legion"]["id"],
             config={**policy, "environment_variables": {"PRIVATE_VALUE": "not-a-template-value"}})
        response = client.post("/api/deployments", json={"legion_id": records["legion"]["id"], "name": "With compute"})
        assert response.status_code == 201, response.text
        release = response.json()
    path = tmp_path / "template.json"
    template = asyncio.run(export_template(source, release["id"], path))
    sandbox = next(n for n in template["blueprint"]["nodes"] if n["type"] == "sandbox")
    assert {key: sandbox["config"][key] for key in policy} == policy
    assert not sandbox["config"].get("workspace_path")
    assert "PRIVATE_VALUE" not in json.dumps(template)
    worker = WorkerConfig(str(uuid4()), str(uuid4()), "research", "runtime", ORIGIN, "cd" * 32,
                          tmp_path / "user", agent_runtime="core.mock")
    asyncio.run(initialize_runtime(worker, path))
    database = Database(worker.data_root / "database/world.sqlite3")
    try:
        with database.locked() as db:
            restored = json.loads(db.execute("SELECT config_json FROM cards WHERE type='sandbox'").fetchone()[0])
    finally:
        database.close()
    assert {key: restored[key] for key in policy} == policy
