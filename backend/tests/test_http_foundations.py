from __future__ import annotations

import logging
import re
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.errors import ConflictError
from backend.http_context import RequestIdMiddleware
from backend.main import create_app
from backend.persistence.database import Database


def test_request_id_covers_success_cors_denial_and_errors(client):
    headers = {"X-Request-ID": "client.trace-17", "Origin": "http://localhost:5173"}
    response = client.get("/api/health", headers=headers)
    assert response.headers["x-request-id"] == "client.trace-17"
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]

    remote = TestClient(client.app, client=("203.0.113.20", 12))
    denied = remote.get("/api/world", headers=headers)
    assert denied.status_code == 403
    assert denied.headers["x-request-id"] == "client.trace-17"
    assert denied.json()["error"]["request_id"] == "client.trace-17"
    assert denied.json()["error"]["retryable"] is False

    invalid = client.post("/api/nodes", json={}, headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["request_id"] == "client.trace-17"
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert all("input" not in item for item in invalid.json()["detail"])
    missing = client.get("/not-a-route", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Not Found"
    assert missing.json()["error"]["request_id"] == "client.trace-17"


@pytest.mark.parametrize("value", ["", "x" * 129, "line\nforgery", "with spaces", "not/as/path"])
def test_untrusted_correlation_header_is_replaced(client, value):
    response = client.get("/api/health", headers={"X-Request-ID": value})
    assert re.fullmatch("[a-f0-9]{32}", response.headers["x-request-id"])


def test_generated_ids_are_distinct_and_logged_without_query(client, caplog):
    with caplog.at_level(logging.INFO, logger="backend.http_context"):
        first = client.get("/api/health?secret=do-not-log")
        second = client.get("/api/health")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert first.headers["x-request-id"] in caplog.text
    assert "do-not-log" not in caplog.text


def test_domain_http_and_unhandled_errors_keep_correlation(tmp_path):
    app = create_app(Settings.for_data_root(tmp_path))

    @app.get("/conflict")
    async def conflict():
        raise ConflictError("The resource changed.")

    @app.get("/http-error")
    async def http_error():
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError("private-server-detail")

    client = TestClient(app, raise_server_exceptions=False)
    for path, status, code in [("/conflict", 409, "conflict"),
                               ("/http-error", 401, "http_error"),
                               ("/unexpected", 500, "internal_error")]:
        response = client.get(path, headers={"X-Request-ID": "incident-123"})
        assert response.status_code == status
        assert response.headers["x-request-id"] == "incident-123"
        assert response.json()["error"]["request_id"] == "incident-123"
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["retryable"] is False
        assert "private-server-detail" not in response.text
    assert client.get("/http-error").headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_duplicate_request_ids_are_replaced_and_streaming_is_untouched():
    chunks = [b"first", b"second"]

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"x-request-id", b"wrong-inner-id")]})
        for chunk in chunks:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "method": "GET", "headers": [
        (b"x-request-id", b"first-id"), (b"x-request-id", b"second-id"),
    ]}
    await RequestIdMiddleware(app)(scope, receive, send)
    headers = messages[0]["headers"]
    assert headers == [(b"x-request-id", scope["state"]["request_id"].encode())]
    assert re.fullmatch("[a-f0-9]{32}", scope["state"]["request_id"])
    assert [message["body"] for message in messages[1:]] == [*chunks, b""]


def test_readiness_tracks_startup_shutdown_and_database(tmp_path):
    app = create_app(Settings.for_data_root(tmp_path))
    probe = TestClient(app)
    assert probe.get("/health/live").json() == {"status": "alive"}
    assert probe.get("/health/ready").status_code == 503
    with TestClient(app) as started:
        assert started.get("/health/ready").json() == {"status": "ready"}
        app.state.ready = False
        assert started.get("/health/ready").status_code == 503
        assert started.get("/health/live").status_code == 200
        app.state.ready = True
    assert probe.get("/health/ready").status_code == 503
    assert probe.get("/health/live").headers["cache-control"] == "no-store"


def test_database_probe_fails_closed_when_busy_or_closed(tmp_path):
    db = Database(tmp_path / "world.sqlite3")
    assert db.is_ready()
    acquired, release = threading.Event(), threading.Event()

    def hold_database():
        with db.locked():
            acquired.set()
            release.wait(5)

    thread = threading.Thread(target=hold_database)
    thread.start()
    assert acquired.wait(5)
    try:
        assert not db.is_ready()
    finally:
        release.set()
        thread.join(5)
    db.close()
    assert not db.is_ready()


def test_health_does_not_bypass_desktop_ingress(tmp_path):
    app = create_app(Settings.for_data_root(tmp_path))
    remote = TestClient(app, client=("203.0.113.20", 12))
    for path in ("/health/live", "/health/ready", "/api/health"):
        assert remote.get(path).status_code == 403
