import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.hosting.accounts import AccountStore
from backend.hosting.configuration import GatewaySettings, HostedProfile
from backend.hosting.gateway import create_gateway
from backend.hosting.worker import WorkerConfig, WorkerIdentityProvider, create_worker_app


ORIGIN = "https://oaw.example"
PASSWORD = "a sufficiently long password"


def fixture_data(tmp_path, *, password=None):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    users = [store.create_user(name, name.title(), password) for name in ("alice", "bob", "unassigned")]
    profiles = tuple(HostedProfile(str(uuid4()), user["id"], "studio", user["display_name"] + " studio", "builder",
                                   f"http://worker-{index}:8000", f"{index + 1:02x}" * 32)
                     for index, user in enumerate(users[:2]))
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html>Hosted login shell</html>", encoding="utf-8")
    (frontend / "assets/app.js").write_text("// public bundle", encoding="utf-8")
    settings = GatewaySettings(ORIGIN, store.path, profiles)
    return store, users, profiles, frontend, settings


@pytest.mark.asyncio
async def test_gateway_routes_two_users_to_real_isolated_workers_and_strips_client_authority(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    workers = {profile.upstream: create_worker_app(WorkerConfig(
        profile.profile_id, profile.user_id, profile.application_id, profile.mode, ORIGIN, profile.secret,
        tmp_path / f"worker-{index}", agent_runtime="core.mock")) for index, profile in enumerate(profiles)}
    calls = []

    async def dispatch(request):
        calls.append(request)
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        assert origin in workers
        return await httpx.ASGITransport(app=workers[origin]).handle_async_request(request)

    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(dispatch))
    async with AsyncExitStack() as stack:
        for worker in workers.values():
            await stack.enter_async_context(worker.router.lifespan_context(worker))
        await stack.enter_async_context(gateway.app.router.lifespan_context(gateway.app))
        client = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN))
        assert (await client.get("/api/world")).status_code == 401
        alice_token = store.create_session(users[0]["id"])
        client.cookies.set("oaw_session", alice_token)
        info = await client.get("/auth/me")
        assert info.json()["instance"] == profiles[0].public()
        assert "secret" not in info.text and "worker-0" not in info.text
        headers = {"Origin": ORIGIN, "X-OAW-Instance": profiles[0].profile_id,
                   "Authorization": "Bearer browser-forgery", "Forwarded": "for=evil;host=evil.example",
                   "X-Forwarded-Host": "evil.example", "X-Forwarded-For": "1.2.3.4", "X-Real-IP": "1.2.3.4",
                   "Connection": "keep-alive, X-Secret-Connection", "X-Secret-Connection": "drop-me"}
        response = await client.post("/api/nodes", json={"type": "text", "name": "Private note", "content": "Alice only"}, headers=headers)
        assert response.status_code == 201, response.text
        node_id = response.json()["id"]
        forwarded = calls[-1]
        for name in ("cookie", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "x-oaw-instance", "x-secret-connection"):
            assert name not in forwarded.headers
        assert forwarded.headers["host"] == "worker-0:8000"
        assert forwarded.headers["authorization"] != "Bearer browser-forgery"
        identity = await WorkerIdentityProvider(profiles[0].secret, profiles[0].profile_id, profiles[0].user_id).authenticate(
            {"headers": [(b"authorization", forwarded.headers["authorization"].encode())]})
        assert identity.subject == users[0]["id"]
        assert response.headers["x-oaw-instance"] == profiles[0].profile_id
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["x-frame-options"] == "DENY"
        client.cookies.set("oaw_session", store.create_session(users[1]["id"]))
        assert (await client.get(f"/api/nodes/{node_id}")).status_code == 404
        assert calls[-1].url.host == "worker-1"
        # A stale browser tab cannot send a mutation after switching accounts.
        before = len(calls)
        stale = await client.post("/api/nodes", headers=headers, json={"type": "text"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "instance_changed"
        assert (await client.post("/api/nodes", headers={"Origin": ORIGIN}, json={"type": "text"})).status_code == 409
        assert (await client.get("/api/world?instance=" + profiles[0].profile_id)).status_code == 409
        assert len(calls) == before
        # Account assignment is server-owned; query and header identities cannot reroute.
        client.cookies.set("oaw_session", store.create_session(users[2]["id"]))
        assert (await client.get("/api/world?user_id=" + users[0]["id"])).status_code == 403
        store.revoke_session(alice_token)
        client.cookies.set("oaw_session", alice_token)
        assert (await client.get("/api/world")).status_code == 401


def test_gateway_auth_shell_assets_and_security_headers(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path, password=PASSWORD)
    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})))
    with TestClient(gateway, base_url=ORIGIN) as client:
        assert client.get("/auth/login").text == "<html>Hosted login shell</html>"
        assert client.get("/").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/accounts.sqlite3").status_code == 401
        response = client.post("/auth/login", json={"username": "alice", "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        assert client.get("/auth/me").json()["user"]["id"] == users[0]["id"]
        assert client.get("/auth/me").json()["instance"]["id"] == profiles[0].profile_id
        assert client.get("/api/world").json() == {"ok": True}
        assert client.get("/auth/me", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("/auth/me", headers=[("Host", "oaw.example"), ("Host", "evil.example")]).status_code == 400
        assert client.get("/api/world", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/nodes", headers={"X-OAW-Instance": profiles[0].profile_id}, json={}).status_code == 403
        assert client.get("/api/world", headers=[("Origin", ORIGIN), ("Origin", "https://evil.example")]).status_code == 403
        token = client.cookies["oaw_session"]
        assert client.get("/auth/me", headers={"Cookie": f"oaw_session={token}; oaw_session={token}"}).json()["authenticated"] is False
        assert client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
        assert client.get("/api/world").status_code == 401


def test_gateway_streams_mock_body_filters_worker_cookies_and_keeps_authority(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"private body", headers={"Set-Cookie": "worker=secret",
            "Cache-Control": "public", "Server": "worker-secret", "Connection": "X-Private", "X-Private": "hide",
            "Location": profiles[0].upstream + "/next", "Content-Type": "text/plain",
            "Content-Security-Policy": "default-src 'none'; sandbox"})

    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(upstream))
    with TestClient(gateway, base_url=ORIGIN) as client:
        client.cookies.set("oaw_session", store.create_session(users[0]["id"]))
        response = client.get("/api/%2F%2Fevil.example/private?target=https://evil.example")
        assert response.text == "private body"
        assert calls[-1].url.host == "worker-0"
        assert response.headers["location"] == ORIGIN + "/next"
        for name in ("set-cookie", "server", "connection", "x-private"):
            assert name not in response.headers
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers.get_list("content-security-policy") == [
            "default-src 'none'; sandbox", "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"]


def test_worker_cookie_jar_never_crosses_same_host_upstreams(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    profiles = tuple(replace(profile, upstream=f"http://workers.internal:{8000 + index}")
                     for index, profile in enumerate(profiles))
    settings = replace(settings, profiles=profiles)
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"Set-Cookie": "worker-session=alice-secret; Path=/"})

    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(upstream))
    with TestClient(gateway, base_url=ORIGIN) as client:
        for user in users[:2]:
            client.cookies.set("oaw_session", store.create_session(user["id"]))
            response = client.get("/api/world")
            assert response.status_code == 200
            assert "set-cookie" not in response.headers
        assert [request.url.port for request in calls] == [8000, 8001]
        assert all("cookie" not in request.headers for request in calls)


def test_sandbox_cold_start_keeps_response_channel_open_without_replaying_timeouts(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    calls = []

    def upstream(request):
        calls.append(request)
        if request.url.path.endswith('/execute'):
            raise httpx.ReadTimeout('response deadline', request=request)
        return httpx.Response(200, json={'ok': True})

    gateway = create_gateway(settings, frontend, store=store, transport=httpx.MockTransport(upstream))
    with TestClient(gateway, base_url=ORIGIN) as client:
        client.cookies.set('oaw_session', store.create_session(users[0]['id']))
        headers = {'Origin': ORIGIN, 'X-OAW-Instance': profiles[0].profile_id}
        for prefix in ('/api', '/api/runtime-app/workspace'):
            for action in ('start', 'python/packages', 'execute'):
                response = client.post(f'{prefix}/sandboxes/s/{action}', headers=headers, json={})
                assert calls[-1].extensions['timeout']['read'] == 3600
                assert calls[-1].extensions['timeout']['connect'] == 10
                if action == 'execute':
                    assert response.status_code == 504
                    assert response.json()['error']['code'] == 'workspace_operation_timeout'
                    assert 'may still be running' in response.json()['error']['message']
        assert len(calls) == 6  # A response timeout never retries a mutation.
        client.get('/api/world')
        assert calls[-1].extensions['timeout']['read'] == 300


class FakeWorkerSocket:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def send(self, value):
        await self.queue.put(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()


@pytest.mark.parametrize("revocation", ["logout", "disable"])
def test_live_websocket_checks_revocation_and_never_forwards_browser_cookie(tmp_path, monkeypatch, revocation):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    connections = []

    @asynccontextmanager
    async def connect(url, **kwargs):
        connections.append((url, kwargs))
        yield FakeWorkerSocket()

    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    gateway = create_gateway(settings, frontend, store=store)
    token = store.create_session(users[0]["id"])
    with TestClient(gateway, base_url=ORIGIN) as client:
        headers = {"Origin": ORIGIN, "Cookie": "oaw_session=" + token, "Authorization": "Bearer browser-forgery",
                   "Forwarded": "for=evil", "X-Forwarded-For": "1.2.3.4"}
        url = "wss://oaw.example/ws/events?instance=" + profiles[0].profile_id
        with client.websocket_connect(url, headers=headers) as socket:
            socket.send_text("hello")
            assert socket.receive_text() == "hello"
            target, forwarded = connections[0]
            assert target.startswith("ws://worker-0:8000/ws/events")
            upstream = dict(forwarded["additional_headers"])
            for name in ("cookie", "forwarded", "x-forwarded-for", "sec-websocket-key"):
                assert name not in upstream
            assert upstream["authorization"] != "Bearer browser-forgery"
            if revocation == "logout":
                response = client.post("/auth/logout", headers={"Origin": ORIGIN, "Cookie": "oaw_session=" + token})
                assert response.status_code == 200
            else:
                store.set_enabled(users[0]["id"], False)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_text()
            assert closed.value.code == 1008


def test_websocket_denies_invalid_session_origin_instance_and_upstream_handshake(tmp_path, monkeypatch):
    from websockets.exceptions import InvalidStatus
    from websockets.datastructures import Headers
    from websockets.http11 import Response
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    calls = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        calls.append(args)
        raise InvalidStatus(Response(403, "Forbidden", Headers()))
        yield  # pragma: no cover

    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    gateway = create_gateway(settings, frontend, store=store)
    token = store.create_session(users[0]["id"])
    with TestClient(gateway, base_url=ORIGIN) as client:
        url = "wss://oaw.example/ws/events"
        valid_headers = {"Origin": ORIGIN, "Cookie": "oaw_session=" + token}
        for target, headers in ((url, valid_headers), (url + "?instance=" + profiles[1].profile_id, valid_headers),
                                (url + "?instance=" + profiles[0].profile_id, {"Origin": "https://evil.example", "Cookie": "oaw_session=" + token}),
                                (url + "?instance=" + profiles[0].profile_id, {"Origin": ORIGIN})):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(target, headers=headers):
                    pytest.fail("Rejected socket connected")
        assert not calls
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(url + "?instance=" + profiles[0].profile_id, headers=valid_headers):
                pytest.fail("Rejected upstream connected")
        assert len(calls) == 1


def test_registry_rejects_ambiguous_routes_and_hides_credentials(tmp_path):
    _, _, profiles, _, settings = fixture_data(tmp_path)
    assert "secret" not in profiles[0].public()
    assert profiles[0].secret not in repr(profiles[0])
    for field in ("profile_id", "upstream", "secret"):
        duplicate = replace(profiles[1], **{field: getattr(profiles[0], field)})
        with pytest.raises(ValueError, match="unique"):
            GatewaySettings(ORIGIN, settings.accounts_path, (profiles[0], duplicate))
