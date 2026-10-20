from dataclasses import asdict, replace
import json
import shutil
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from backend.config import Settings
from backend.deploy import create_deployment, validate_manifest
from backend.hosting.worker import (
    ISSUER, WorkerConfig, WorkerIdentityProvider, create_worker_app, hosted_scope,
    mint_worker_token, prepare_hosted_binding,
)
from backend.hosting.identity import ProfileAccess, ProfileAccessMiddleware, VerifiedIdentity
from backend.main import create_app
from backend.request_context import current_request_context
from backend.tests.test_deployments import PASSWORD, prepare_source


ORIGIN = "https://hosted.example"
SECRET = "fa" * 32


def config(root, **updates):
    return WorkerConfig(profile_id=str(uuid4()), user_id=str(uuid4()), application_id="studio",
                        mode="builder", public_origin=ORIGIN, secret=SECRET, data_root=root,
                        agent_runtime="core.mock", **updates)


def headers(worker):
    return {"Authorization": "Bearer " + mint_worker_token(worker.secret, worker.profile_id, worker.user_id, "Alice"),
            "Origin": ORIGIN}


@pytest.mark.asyncio
async def test_worker_tokens_pin_profile_owner_and_lifetime(monkeypatch):
    profile_id, user_id = str(uuid4()), str(uuid4())
    provider = WorkerIdentityProvider(SECRET, profile_id, user_id)

    async def verify(token):
        return await provider.authenticate({"headers": [(b"authorization", ("Bearer " + token).encode())]})

    token = mint_worker_token(SECRET, profile_id, user_id, "Alice")
    assert await verify(token) == VerifiedIdentity(ISSUER, user_id, "Alice")
    assert await verify(token[:-1] + ("a" if token[-1] != "a" else "b")) is None
    assert await verify(mint_worker_token(SECRET, str(uuid4()), user_id)) is None
    assert await verify(mint_worker_token(SECRET, profile_id, str(uuid4()))) is None
    assert await verify(mint_worker_token("bc" * 32, profile_id, user_id)) is None
    assert await provider.authenticate({"headers": [(b"x-user-id", user_id.encode())]}) is None
    assert await provider.authenticate({"headers": [(b"authorization", ("Bearer " + token).encode())] * 2}) is None
    current = time.time()
    monkeypatch.setattr("backend.hosting.worker.time.time", lambda: current + 31)
    assert await verify(token) is None
    monkeypatch.setattr("backend.hosting.worker.time.time", lambda: current - 10)
    assert await verify(token) is None
    with pytest.raises(ValueError):
        mint_worker_token(SECRET, profile_id, user_id, ttl=3600)


def test_worker_auth_context_origins_and_private_preferences(tmp_path):
    worker = config(tmp_path)
    app = create_worker_app(worker)

    @app.get("/test-context")
    def context(request: Request):
        context = current_request_context()
        assert context == request.state.request_context
        return {"actor": context.actor.id, "tenant": context.tenant.__dict__}

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/world").status_code == 401
        assert client.get("/api/world", headers={"X-User-ID": worker.user_id}).status_code == 401
        assert client.get("/health/ready").status_code == 200
        supplied = headers(worker)
        assert client.get("/api/world", headers=supplied).status_code == 200
        snapshot = client.get("/api/application", headers=supplied).json()
        assert snapshot["hosted"] is True
        assert snapshot["mode"] == "production"
        assert client.get("/api/application", headers=supplied).headers["cache-control"] == "no-store"
        context = client.get("/test-context", headers=supplied).json()
        assert context["actor"] == VerifiedIdentity(ISSUER, worker.user_id).actor_id
        assert context["tenant"] == {"organization_id": worker.user_id, "workspace_id": "studio", "world_id": worker.profile_id}
        for origin in ("https://evil.example", "null", None):
            bad = {"Authorization": supplied["Authorization"]}
            if origin is not None:
                bad["Origin"] = origin
            assert client.post("/api/nodes", headers=bad, json={"type": "text"}).status_code == 403
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/events", headers=bad):
                    pytest.fail("Cross-origin socket accepted")
        with client.websocket_connect("/ws/events", headers=supplied) as socket:
            assert socket.receive_json()["type"] == "connection_ready"
        assert client.get("/docs", headers=supplied).status_code == 404


def test_workers_isolate_data_and_reject_ownership_reassignment(tmp_path):
    alice, bob = config(tmp_path / "alice"), config(tmp_path / "bob")
    with TestClient(create_worker_app(alice)) as a, TestClient(create_worker_app(bob)) as b:
        response = a.post("/api/nodes", json={"type": "text", "name": "Private note", "content": "Alice secret"}, headers=headers(alice))
        assert response.status_code == 201, response.text
        node_id = response.json()["id"]
        assert b.get(f"/api/nodes/{node_id}", headers=headers(bob)).status_code == 404
        assert b.get("/api/world", headers=headers(alice)).status_code == 401
        assert b.get("/api/application", headers=headers(bob)).json()["profile_id"] != a.get("/api/application", headers=headers(alice)).json()["profile_id"]
    with TestClient(create_worker_app(alice)) as a:
        assert a.get(f"/api/nodes/{node_id}", headers=headers(alice)).status_code == 200
    with pytest.raises(ValueError, match="stored binding"):
        with TestClient(create_worker_app(replace(alice, user_id=bob.user_id))):
            pass
    assert json.loads((alice.data_root / "hosted-profile.json").read_text())["version"] == 2


def test_hosted_denies_host_management_and_native_execution_config(tmp_path, monkeypatch):
    worker = config(tmp_path)
    with TestClient(create_worker_app(worker)) as client:
        supplied = headers(worker)
        for method, path in (("get", "/api/diagnostics"), ("get", "/api/settings/storage"),
                             ("post", "/api/desktop/inspect-path"), ("post", "/api/packs/install"),
                             ("get", "/api/store/packs"), ("put", "/api/settings/sandbox"),
                             ("post", "/api/packs/factory/example/run"), ("post", "/api/runtime/python/plugins/reconcile")):
            assert getattr(client, method)(path, headers=supplied).status_code == 403, path
        for payload in ({"type": "sandbox", "config": {"workspace_path": "/other-user"}},
                        {"type": "openai.codex.agent"},
                        {"type": "agent", "config": {"runtime_provider_id": "openai.codex"}}):
            assert client.post("/api/nodes", json=payload, headers=supplied).status_code == 403
        assert client.put("/api/nodes/example/environment", headers=supplied,
                          json={"folders": {"private": {"path": "/another-user"}}}).status_code == 403
        assert client.post("/api/card-library/actions", headers=supplied,
                           json={"action": "set_plugin_enabled", "id": "openai.codex", "enabled": True}).status_code == 403
        for path in ("/api/nodes/example/actions/configure", "/api/runtime-app/workspace/nodes/example/actions/configure"):
            for destination in ("/private/library.sqlite3", "C:\\private\\library.sqlite3", "../../control/accounts.sqlite3"):
                assert client.post(path, headers=supplied, json={"arguments": {"path": destination}}).status_code == 403
        for path in ("/api/settings/models/discover", "/api/settings/models"):
            method = client.post if path.endswith("discover") else client.put
            assert method(path, headers=supplied, json={"auth_mode": "environment", "environment_variable": "SANDBOX_SECRET",
                                                       "base_url": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/sandboxes/example/diagnostics", headers=supplied,
                           json={"destination": "/other-user"}).status_code == 403
        # Omitted auth_mode is not a partial update: the model schema defaults
        # to api_key and must not fall back to the stored environment secret.
        from backend.security.model_connections import CatalogEdit, ModelConnectionStore
        store = ModelConnectionStore(client.app.state.services.llm_settings)
        store.save(CatalogEdit(connections=[{"id": "operator", "name": "Operator model", "auth_mode": "environment",
                                             "environment_variable": "OAW_TEST_SERVICE_SECRET"}]))
        monkeypatch.setenv("OAW_TEST_SERVICE_SECRET", "private-environment-secret")

        def unexpected_network(*args, **kwargs):
            pytest.fail("Model discovery tried to send operator environment credentials")

        monkeypatch.setattr("backend.security.model_discovery.httpx.AsyncClient", unexpected_network)
        omitted = client.post("/api/settings/models/discover", headers=supplied,
                              json={"id": "operator", "name": "Operator model", "base_url": "https://untrusted.example"})
        assert omitted.status_code == 422, omitted.text
        assert "private-environment-secret" not in omitted.text
        assert client.app.state.services.sandbox_backend is None
        assert not client.app.state.services.plugins.has_runtime_provider("openai.codex")
        # Ordinary text and canvas operations remain available.
        assert client.post("/api/nodes", json={"type": "text", "content": "Hello"}, headers=supplied).status_code == 201
    with pytest.raises(ValueError, match="remote Sandbox"):
        config(tmp_path / "native", sandbox_runtime="auto")


def test_hosted_deployment_uses_verified_identity_without_shared_login(tmp_path):
    source, shared, target = tmp_path / "source", tmp_path / "shared", tmp_path / "hosted"
    records = prepare_source(source)
    create_deployment(source, shared, records["release"]["id"], password=PASSWORD)
    worker = replace(config(target), mode="runtime")
    prepare_hosted_binding(target, profile_id=worker.profile_id, user_id=worker.user_id, application_id=worker.application_id)
    shutil.copytree(shared, target, dirs_exist_ok=True)
    manifest_path = target / "deployment.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(schema_version=2, auth_mode="hosted")
    manifest.pop("password")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    validate_manifest(manifest)
    with pytest.raises(ValueError, match="verified profile"):
        create_app(Settings.for_data_root(target))
    with pytest.raises(ValueError, match="shared password"):
        validate_manifest({**manifest, "password": {"salt": "00" * 16, "hash": "00" * 32}})
    app = create_worker_app(worker)
    with TestClient(app) as client:
        supplied = headers(worker)
        assert client.get("/api/deployment").status_code == 401
        info = client.get("/api/deployment", headers=supplied).json()
        assert info["mode"] == "runtime" and info["hosted"] is True and info["authenticated"] is True
        response = client.get("/api/runtime-app", headers=supplied)
        assert response.status_code == 200, response.text
        for secret in ("PRIVATE INSTRUCTION", "NOT PUBLIC", "password", records["hidden"]["id"]):
            assert secret not in response.text
        for method, path in (("get", "/api/world"), ("get", "/api/application"),
                             ("post", "/api/deployment/session"), ("delete", "/api/runtime-app/session")):
            assert getattr(client, method)(path, headers=supplied).status_code == 404
        chat = records["chat"]["id"]
        created = client.post(f"/api/runtime-app/workspace/conversations/{chat}/sessions", headers=supplied,
                              json={"title": "My private runtime session"})
        assert created.status_code == 201, created.text
        assert app.state.services.world.structure_locked is True


def test_unbound_desktop_database_requires_explicit_migration(tmp_path):
    with TestClient(create_app(Settings.for_data_root(tmp_path))):
        pass
    with pytest.raises(ValueError, match="offline"):
        prepare_hosted_binding(tmp_path, profile_id=str(uuid4()), user_id=str(uuid4()), application_id="studio")


def test_hosted_runtime_preserves_user_attribution_inside_router(tmp_path, monkeypatch, caplog):
    from backend import deployment_workspace
    from backend.deployment_runtime import runtime_router
    worker = config(tmp_path)
    scope = hosted_scope(worker.user_id, worker.application_id, worker.profile_id)
    access = ProfileAccess(WorkerIdentityProvider(SECRET, worker.profile_id, worker.user_id),
                           VerifiedIdentity(ISSUER, worker.user_id), scope, ORIGIN)
    manifest = {"id": "release", "name": "Studio", "created_at": "2026-10-19", "layout": {},
                "panels": [], "permissions": {}, "auth_mode": "hosted"}
    app = FastAPI()
    app.state.services = object()
    app.add_middleware(ProfileAccessMiddleware, access=access)
    monkeypatch.setattr(deployment_workspace, "workspace_snapshot",
                        lambda manifest, services: {"context": asdict(current_request_context())})
    app.include_router(runtime_router(manifest, hosted=True))
    with TestClient(app) as client:
        response = client.get("/api/runtime-app", headers=headers(worker))
        assert response.status_code == 200, response.text
        context = response.json()["context"]
        assert context["actor"]["kind"] == "user"
        assert context["actor"]["id"] == VerifiedIdentity(ISSUER, worker.user_id).actor_id
        assert context["tenant"] == asdict(scope)
        assert context["auth_method"] == "external_identity"
        # A denied published operation must not break the middleware's context
        # cleanup or the identity attached to the next request.
        denied = client.get("/api/runtime-app/workspace/nodes/unpublished/jobs", headers=headers(worker))
        assert denied.status_code == 404
        again = client.get("/api/runtime-app", headers=headers(worker))
        for key in ("actor", "tenant", "auth_method"):
            assert again.json()["context"][key] == context[key]
    assert current_request_context() is None
    assert "was created in a different Context" not in caplog.text
