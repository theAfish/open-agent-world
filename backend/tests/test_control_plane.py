from dataclasses import replace
from ipaddress import ip_address
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import Settings
from backend.control_plane import ControlPlaneMiddleware
from backend.main import create_app
from backend.sandbox.environment import minimal_windows_environment


@pytest.mark.parametrize("peer", [
    "192.168.1.10", "10.0.2.2", "10.0.2.3", "100.64.0.1", "8.8.8.8",
    "::ffff:192.168.1.10", "2001:4860:4860::8888", "fe80::1", "localhost", "testclient",
])
def test_untrusted_peer_cannot_read_or_mutate_world(tmp_path, peer):
    app = create_app(Settings.for_data_root(tmp_path))
    with TestClient(app) as local:
        before = local.get("/api/world").json()
        remote = TestClient(app, client=(peer, 1234))
        for headers in ({}, {"Host": "localhost:8000"}, {"X-Forwarded-For": "127.0.0.1"},
                        {"Forwarded": "for=127.0.0.1;host=localhost"}, {"Authorization": "Bearer guessed"}):
            response = remote.post("/api/nodes", json={"type": "text"}, headers=headers)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "control_plane_access_denied"
        assert remote.get("/api/world").status_code == 403
        assert local.get("/api/world").json() == before
        with pytest.raises(WebSocketDisconnect) as denied:
            with remote.websocket_connect("/ws/events"):
                pytest.fail("untrusted WebSocket was accepted")
        assert denied.value.code == 1008


@pytest.mark.parametrize("peer", ["127.0.0.1", "127.0.0.2", "::1", "::ffff:127.0.0.1", "::ffff:7f00:1"])
def test_literal_local_peers_keep_existing_api_access(tmp_path, peer):
    app = create_app(Settings.for_data_root(tmp_path))
    with TestClient(app, client=(peer, 1234)) as local:
        assert local.get("/api/catalog").status_code == 200
        with local.websocket_connect("/ws/events") as socket:
            assert socket.receive_json()["type"] == "connection_ready"


@pytest.mark.parametrize("header", ["Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP"])
def test_forwarded_requests_need_auth_even_with_rewritten_local_peer(tmp_path, header):
    app = create_app(Settings.for_data_root(tmp_path))
    local = TestClient(app)
    assert local.get("/api/catalog", headers={header: "127.0.0.1"}).status_code == 403


def test_remote_bearer_auth_is_host_private_and_applies_to_websocket(tmp_path, monkeypatch):
    secret = "controlled-test-host-credential-739281"
    monkeypatch.setenv("OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN", secret)
    monkeypatch.setenv("OPEN_AGENT_WORLD_DATA_ROOT", str(tmp_path))
    settings = Settings.from_environment()
    assert secret not in repr(settings)
    assert "OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN" not in minimal_windows_environment(tmp_path)
    app = create_app(replace(settings, agent_runtime="core.mock"))
    with TestClient(app, client=("203.0.113.10", 1234)) as remote:
        assert remote.get("/api/catalog").status_code == 403
        headers = {"Authorization": "Bearer " + secret, "X-Forwarded-For": "198.51.100.4"}
        assert remote.get("/api/catalog", headers=headers).status_code == 200
        assert secret not in remote.get("/api/settings", headers=headers).text
        with remote.websocket_connect("/ws/events", headers=headers) as socket:
            assert socket.receive_json()["type"] == "connection_ready"


@pytest.mark.parametrize("value", ["", "short", "a" * 31 + "\n", "a" * 31 + "\x7f", "a" * 31 + "\u2603"])
def test_invalid_management_credentials_fail_without_echoing_values(monkeypatch, value):
    monkeypatch.setenv("OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN", value)
    with pytest.raises(ValueError, match="printable non-whitespace ASCII"):
        Settings.from_environment()


@pytest.mark.asyncio
async def test_missing_socket_peer_and_duplicate_auth_fail_closed():
    reached = []

    async def app(scope, receive, send):
        reached.append(scope)

    middleware = ControlPlaneMiddleware(app, token="host-secret")
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    for headers in ([], [(b"authorization", b"Bearer host-secret"), (b"authorization", b"Bearer host-secret")]):
        await middleware({"type": "http", "headers": headers}, receive, send)
    assert not reached
    assert [item["status"] for item in messages if item["type"] == "http.response.start"] == [403, 403]


def test_network_setup_errors_have_separate_diagnostic_code(tmp_path):
    from backend.sandbox.models import SandboxNetworkError

    app = create_app(Settings.for_data_root(tmp_path))

    @app.get("/failure")
    def failure():
        raise SandboxNetworkError("network helper failed readiness")

    response = TestClient(app).get("/failure")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "network_setup_failed"


def test_real_socket_ingress_rejects_interface_and_forged_forwarding(tmp_path):
    """Exercise the real listener; runtime tests additionally probe from guests."""
    addresses = sorted({
        item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        if not ip_address(item[4][0]).is_loopback
    })
    if not addresses:
        pytest.skip("no non-loopback host IPv4 interface for ingress acceptance")
    app = create_app(replace(Settings.for_data_root(tmp_path), agent_runtime="core.mock"))
    listener = socket.socket()
    listener.bind(("0.0.0.0", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, proxy_headers=False, log_level="error"))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(trust_env=False, timeout=3) as client:
            local = f"http://127.0.0.1:{port}"
            before = client.get(local + "/api/world").json()
            assert client.get(local + "/api/catalog").status_code == 200
            assert client.get(local + "/api/catalog", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
            for address in addresses:
                target = f"http://{address}:{port}"
                for headers in ({}, {"Host": "localhost", "X-Forwarded-For": "127.0.0.1"}):
                    response = client.post(target + "/api/nodes", headers=headers, json={"type": "text"})
                    assert response.status_code == 403, (address, response.text)
            assert client.get(local + "/api/world").json() == before
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        listener.close()
    assert not worker.is_alive()
