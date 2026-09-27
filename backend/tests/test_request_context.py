from __future__ import annotations

import asyncio
import re

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
import pytest

from backend.api.dependencies import get_request_context
from backend.request_context import (
    ActorKind,
    RequestContextMiddleware,
    bind_request_context,
    create_local_request_context,
    current_request_context,
    require_request_context,
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
    with pytest.raises(RuntimeError, match="No OAW request context"):
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
    app.add_middleware(RequestContextMiddleware)

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
