import asyncio
from dataclasses import asdict, replace
import json
from uuid import uuid4

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


def test_admin_deployments_are_durable_idempotent_and_do_not_reassign_existing_profiles(tmp_path, template):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    store.set_role(users[0]["id"], "admin")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=template if "/hosting-template/" in r.url.path
                                                            else [{"id": template["source_release_id"], "name": "App",
                                                                   "legion_id": "legion", "created_at": template["created_at"]}]))
    with TestClient(create_gateway(settings, frontend, store=store, transport=transport), base_url=ORIGIN) as client:
        payload = {"request_id": str(uuid4()), "release_id": template["source_release_id"], "user_ids": [users[1]["id"]], "mode": "runtime"}
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert client.get("/hosting/admin/deployments").status_code == 403
        assert client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN}).status_code == 403
        assert client.get("/api/hosting-template/" + template["source_release_id"]).status_code == 403
        client.cookies.set("oaw_session", store.create_session(users[0]["id"]))
        assert client.post("/hosting/admin/deployments", json=payload).status_code == 403
        assert client.post("/hosting/admin/deployments", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
        first = client.post("/hosting/admin/deployments", json=payload, headers={"Origin": ORIGIN})
        assert first.status_code == 202, first.text
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
