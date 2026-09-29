from __future__ import annotations

import asyncio
import re
from dataclasses import FrozenInstanceError, asdict

import httpx
import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.dependencies import get_request_context, get_services
from backend.control_plane import ControlPlaneMiddleware
from backend.deployment_runtime import COOKIE, password_record, runtime_router
from backend.errors import PermissionDeniedError
from backend.http_context import RequestIdMiddleware
from backend.request_context import (
    ActorKind, ActorRef, LOCAL_ACTOR, LOCAL_TENANT, RequestContext,
    bind_request_context, context_from_scope, create_local_request_context,
    current_request_context, require_request_context,
)

_GENERATED_REQUEST_ID = re.compile(r"req_[0-9a-f]{32}\Z")


def test_local_context_has_stable_personal_identity_and_scope():
    context = create_local_request_context("desktop-request-1")

    assert context.request_id == "desktop-request-1"
    assert context.actor.kind is ActorKind.USER
    assert context.actor.id == "local-user"
    assert context.tenant.organization_id == "local-organization"
    assert context.tenant.workspace_id == "local-workspace"
    assert context.tenant.world_id == "local-world"
    assert context.auth_method == "local"


def test_binding_is_nested_and_restored():
    outer = create_local_request_context("outer")
    inner = create_local_request_context("inner")

    assert current_request_context() is None
    with bind_request_context(outer):
        assert require_request_context() is outer
        with bind_request_context(inner):
            assert require_request_context() is inner
        assert require_request_context() is outer
    assert current_request_context() is None
    with pytest.raises(PermissionDeniedError, match="trusted request context"):
        require_request_context()


@pytest.mark.asyncio
async def test_context_is_isolated_between_async_tasks():
    first = create_local_request_context("first")
    second = create_local_request_context("second")

    async def capture(context):
        with bind_request_context(context):
            await asyncio.sleep(0)
            return require_request_context()

    observed = await asyncio.gather(capture(first), capture(second))

    assert observed == [first, second]
    assert current_request_context() is None


def context_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ControlPlaneMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/context")
    async def context_endpoint(request: Request):
        context = get_request_context(request)
        assert require_request_context() is context
        return {
            "request_id": context.request_id,
            "actor_id": context.actor.id,
            "organization_id": context.tenant.organization_id,
        }

    @app.websocket("/context")
    async def websocket_context(websocket: WebSocket):
        await websocket.accept()
        context = websocket.state.request_context
        assert require_request_context() is context
        await websocket.send_json({"request_id": context.request_id})
        await websocket.close()

    return app


def test_http_middleware_propagates_valid_request_id_and_context():
    with TestClient(context_app()) as client:
        response = client.get("/context", headers={"X-Request-ID": "desktop.request:42"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "desktop.request:42"
    assert response.json() == {
        "request_id": "desktop.request:42",
        "actor_id": "local-user",
        "organization_id": "local-organization",
    }


@pytest.mark.parametrize("value", ["contains spaces", "contains/slash", "x" * 129])
def test_http_middleware_replaces_unsafe_request_id(value):
    with TestClient(context_app()) as client:
        response = client.get("/context", headers={"X-Request-ID": value})

    generated = response.headers["X-Request-ID"]
    assert _GENERATED_REQUEST_ID.fullmatch(generated)
    assert response.json()["request_id"] == generated


def test_websocket_receives_the_same_request_context():
    with TestClient(context_app()) as client:
        with client.websocket_connect(
            "/context",
            headers={"X-Request-ID": "websocket-request-1"},
        ) as websocket:
            assert websocket.receive_json() == {"request_id": "websocket-request-1"}


def test_main_application_registers_and_exposes_request_id(client):
    response = client.get(
        "/api/catalog",
        headers={
            "Origin": "http://localhost:5173",
            "X-Request-ID": "catalog-request-1",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "catalog-request-1"
    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]


def management_app(token=None):
    app = FastAPI()
    app.state.services = object()
    app.add_middleware(ControlPlaneMiddleware, token=token)

    @app.get("/probe")
    async def probe(context=Depends(get_request_context), services=Depends(get_services)):
        assert services is app.state.services
        assert require_request_context() == context
        return asdict(context)

    @app.websocket("/probe")
    async def socket_probe(websocket: WebSocket):
        context = context_from_scope(websocket.scope)
        assert require_request_context() == context
        await websocket.accept()
        await websocket.send_json(asdict(context))
        await websocket.close()

    return app


def test_context_is_immutable_and_scopes_reset_after_errors():
    first = RequestContext("first", ActorRef("system", "worker"), LOCAL_TENANT, "internal")
    second = RequestContext("second", ActorRef("system", "worker-2"), LOCAL_TENANT, "internal")
    assert current_request_context() is None
    with pytest.raises(PermissionDeniedError):
        require_request_context()
    with pytest.raises(FrozenInstanceError):
        first.actor.id = "forged"
    with bind_request_context(first):
        assert require_request_context() is first
        with pytest.raises(RuntimeError):
            with bind_request_context(second):
                assert require_request_context() is second
                raise RuntimeError("operation failed")
        assert require_request_context() is first
    assert current_request_context() is None


def test_management_identity_comes_from_authentication_not_headers():
    secret = "test-control-plane-secret"
    app = management_app(secret)
    forged = {"X-Actor-ID": "administrator", "X-Tenant-ID": "other", "X-Organization-ID": "other",
              "X-OAW-State-Session": "other-session"}
    with TestClient(app) as local:
        result = local.get("/probe", headers=forged).json()
        assert result["actor"] == asdict(LOCAL_ACTOR)
        assert result["tenant"] == asdict(LOCAL_TENANT)
        assert result["auth_method"] == "local_socket"
        assert result["request_id"]
        with local.websocket_connect("/probe") as socket:
            assert socket.receive_json()["actor"] == result["actor"]
        assert local.get("/probe", headers={"X-Forwarded-For": "127.0.0.1", **forged}).status_code == 403
    with TestClient(app, client=("203.0.113.1", 1234)) as remote:
        denied = remote.get("/probe", headers=forged)
        assert denied.status_code == 403
        assert denied.json()["error"]["request_id"]
        assert denied.json()["error"]["retryable"] is False
        with pytest.raises(WebSocketDisconnect):
            with remote.websocket_connect("/probe"):
                pytest.fail("untrusted WebSocket accepted")
        headers = {**forged, "Authorization": "Bearer " + secret}
        allowed = remote.get("/probe", headers=headers)
        assert allowed.json()["actor"] == asdict(ActorRef("host_credential", "control-plane"))
        assert allowed.json()["auth_method"] == "host_bearer"
        assert secret not in allowed.text
        with remote.websocket_connect("/probe", headers=headers) as socket:
            assert socket.receive_json()["actor"]["kind"] == "host_credential"
    assert current_request_context() is None


@pytest.mark.asyncio
async def test_context_does_not_leak_between_concurrent_requests():
    app = management_app()
    both_started = asyncio.Event()
    contexts = []

    @app.get("/concurrent")
    async def concurrent(context=Depends(get_request_context), services=Depends(get_services)):
        contexts.append(context)
        if len(contexts) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        assert require_request_context() is context
        assert await asyncio.to_thread(require_request_context) is context
        return context.request_id

    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(client.get("/concurrent"), client.get("/concurrent"))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() != responses[1].json()
    assert current_request_context() is None


def test_get_services_without_authenticated_scope_fails_closed():
    app = FastAPI()
    app.state.services = object()

    @app.get("/unprotected")
    async def unprotected(services=Depends(get_services)):
        pytest.fail("services escaped the authentication boundary")

    with TestClient(app) as client, pytest.raises(PermissionDeniedError):
        client.get("/unprotected", headers={"X-Actor-ID": "administrator"})


def test_deployment_context_distinguishes_public_and_password_sessions(monkeypatch):
    from backend import deployment_workspace

    password = "operator-test-password"
    manifest = {"id": "release-test", "name": "Test", "created_at": "2026-09-26", "layout": {},
                "panels": [], "permissions": {}, "password": password_record(password)}
    app = FastAPI()
    app.state.services = object()
    observed = []

    class ObserveContext:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            try:
                await self.app(scope, receive, send)
            finally:
                observed.append(scope.get("state", {}).get("request_context"))

    app.add_middleware(ObserveContext)
    monkeypatch.setattr(deployment_workspace, "workspace_snapshot",
                        lambda manifest, services: {"context": asdict(require_request_context())})
    app.include_router(runtime_router(manifest))
    with TestClient(app, client=("203.0.113.1", 1234)) as client:
        assert client.get("/api/deployment").status_code == 200
        public = observed[-1]
        assert public.actor == ActorRef("anonymous", "deployment-public")
        assert public.auth_method == "deployment_public"
        assert client.get("/api/runtime-app").status_code == 401
        assert observed[-1] is None
        assert client.post("/api/deployment/session", json={"password": password}).status_code == 200
        token = client.cookies[COOKIE]
        first = client.get("/api/runtime-app", headers={"X-Actor-ID": "user:admin", "X-Tenant-ID": "other"})
        context = first.json()["context"]
        assert context["actor"]["kind"] == "deployment_session"
        assert len(context["actor"]["id"]) == 64
        assert context["tenant"] == asdict(LOCAL_TENANT)
        assert context["auth_method"] == "deployment_cookie"
        assert token not in first.text
        assert client.get("/api/runtime-app").json()["context"]["actor"] == context["actor"]
        assert client.delete("/api/runtime-app/session", headers={"Origin": "https://evil.example"}).status_code == 403
        assert observed[-1] is None
        assert client.delete("/api/runtime-app/session").status_code == 200
        assert client.get("/api/runtime-app").status_code == 401
        assert observed[-1] is None
        assert client.post("/api/deployment/session", json={"password": password}).status_code == 200
        assert client.get("/api/runtime-app").json()["context"]["actor"] != context["actor"]
    assert current_request_context() is None
