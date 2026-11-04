"""Portable unit tests; native socket/OS boundaries are exercised on Linux too."""
import asyncio
from contextlib import AsyncExitStack
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.hosting.configuration import GatewaySettings, HostedProfile, default_upstream, unix_socket_path
from backend.hosting.systemd import _plain_tree, activated_socket, linux_path, render_units, service_user


@pytest.fixture
def settings(tmp_path):
    profiles = tuple(HostedProfile(str(uuid4()), str(uuid4()), "test", "Private", "builder",
                                  f"http://localhost:{port}", secret * 32)
                     for port, secret in ((8001, "ab"), (8002, "cd")))
    return GatewaySettings("https://oaw.example", tmp_path / "accounts.sqlite3", profiles)


@pytest.mark.parametrize("value", ["/srv/data/../etc", "/srv//data", "/srv/a\nb", "/srv/a%u", "/srv/a$b",
                                  "/srv/a b", "/", "/srv", "/home/user/data", "relative"])
def test_systemd_paths_cannot_inject_directives_or_escape_roots(value):
    with pytest.raises(ValueError):
        linux_path(value)


@pytest.mark.parametrize("value", ["unix://remote/run/a.sock", "unix:///run/../a.sock", "unix:///run/a.sock?x=1",
                                  "unix:///run/a%2fb.sock", "unix:///run//a.sock", "unix:///run/a", "unix:@abstract", "unix:/run/a.sock"])
def test_unix_socket_addresses_are_literal_filesystem_paths(value):
    with pytest.raises(ValueError):
        unix_socket_path(value)


def test_native_units_have_private_storage_users_and_gateway_only_sockets(settings):
    units = render_units(settings, directory="/srv/private-oaw", application="/opt/oaw", name="example")
    accounts = {service_user("example", profile.profile_id) for profile in settings.profiles}
    assert len(accounts) == 2 and service_user("example") not in accounts
    assert service_user("other") != service_user("example")
    for profile in settings.profiles:
        worker = units[f"example-worker-{profile.profile_id}.service"]
        assert f"BindReadOnlyPaths=/srv/private-oaw/instances/{profile.profile_id}:/var/lib/example-profile" in worker
        assert f"BindPaths=/srv/private-oaw/instances/{profile.profile_id}/data:/var/lib/example-profile/data" in worker
        assert "InaccessiblePaths=/srv/private-oaw /run/example" in worker
        assert "ProtectHome=yes" in worker and "NoNewPrivileges=yes" in worker and "ProtectProc=invisible" in worker
        assert "IPAddressDeny=link-local 100.100.100.200/32" in worker
        assert f"SocketUser={service_user('example')}" in units[f"example-worker-{profile.profile_id}.socket"]
        assert profile.secret not in "".join(units.values())
        assert unix_socket_path(default_upstream(profile.profile_id, "example")) == f"/run/example/{profile.profile_id}.sock"
    assert "uv sync" not in "".join(units.values()) and "pip install" not in "".join(units.values())
    for app in ("/srv/private-oaw", "/srv/private-oaw/code"):
        with pytest.raises(ValueError, match="separate"):
            render_units(settings, directory="/srv/private-oaw", application=app)
    with pytest.raises(ValueError):
        render_units(settings, directory="/srv/private-oaw", application="/opt/oaw", name="a\nUser=root")


def test_ownership_walk_rejects_hardlinks_before_mutation(tmp_path):
    target = tmp_path / "original"
    target.write_text("private")
    os.link(target, tmp_path / "alias")
    with pytest.raises(ValueError, match="hard links"):
        _plain_tree(tmp_path)
    assert target.read_text() == "private"


def test_systemd_preserves_only_explicit_resource_assignment_read_access(tmp_path, monkeypatch):
    from backend.hosting import systemd
    documents = {"agenticfs.json": (0o640, '{"volume_id":"retained-volume"}'),
                 "operator-secret.json": (0o600, '{"private":"operator-only"}'),
                 "worker.json": (0o640, '{}'), "template.json": (0o640, '{}')}
    for name, (_, content) in documents.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda path, *args, **kwargs:
        SimpleNamespace(st_mode=stat.S_IFREG | documents[path.name][0])
        if path.parent == tmp_path and path.name in documents else original_stat(path, *args, **kwargs))
    trusted = []
    monkeypatch.setattr(systemd, "_trusted", trusted.append)
    resources = systemd._worker_resource_files(tmp_path)
    assert resources == ("agenticfs.json",)
    assert trusted == [tmp_path / "agenticfs.json"]


@pytest.mark.parametrize("name,content", [("invalid.assignment.json", '{}'), ("assignment.json", '[]')])
def test_systemd_rejects_invalid_group_readable_assignments_before_permissions_change(tmp_path, monkeypatch, name, content):
    from backend.hosting import systemd
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda candidate, *args, **kwargs:
        SimpleNamespace(st_mode=stat.S_IFREG | 0o640) if candidate == path else original_stat(candidate, *args, **kwargs))
    monkeypatch.setattr(systemd, "_trusted", lambda _: None)
    with pytest.raises(ValueError, match="Resource assignment"):
        systemd._worker_resource_files(tmp_path)


def test_systemd_rejects_untrusted_group_readable_resource_assignment(tmp_path, monkeypatch):
    from backend.hosting import systemd
    path = tmp_path / "assignment.json"
    path.write_text('{}', encoding="utf-8")
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda candidate, *args, **kwargs:
        SimpleNamespace(st_mode=stat.S_IFREG | 0o640) if candidate == path else original_stat(candidate, *args, **kwargs))
    def untrusted(_):
        raise ValueError("Assignment must be root-owned")
    monkeypatch.setattr(systemd, "_trusted", untrusted)
    with pytest.raises(ValueError, match="root-owned"):
        systemd._worker_resource_files(tmp_path)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux service descriptors and symlink permissions")
def test_ownership_walk_rejects_symlinks_before_mutation(tmp_path):
    (tmp_path / "outside").symlink_to("/etc")
    with pytest.raises(ValueError, match="symlinks"):
        _plain_tree(tmp_path)


def test_worker_rejects_spoofed_socket_activation(monkeypatch):
    monkeypatch.setenv("LISTEN_PID", str(os.getpid() + 1))
    monkeypatch.setenv("LISTEN_FDS", "1")
    with pytest.raises(ValueError, match="systemd"):
        activated_socket(3)
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    with pytest.raises(ValueError, match="systemd"):
        activated_socket(4)


def test_installation_refuses_a_host_that_ignores_network_filter(monkeypatch):
    from backend.hosting import systemd
    original = systemd._run

    def unfiltered_host(*args):
        # Simulate systemd accepting the directive but launching without a filter.
        return original(sys.executable, *args[args.index("-c"):])

    monkeypatch.setattr(systemd, "_run", unfiltered_host)
    with pytest.raises(ValueError, match="IPAddressDeny is not enforced"):
        systemd.check_network_filter(Path(sys.executable), "oaw-test")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux filesystem sockets")
@pytest.mark.asyncio
async def test_real_unix_http_and_websocket_transports_keep_users_separate(tmp_path):
    import httpx
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, WebSocketRoute
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed
    from backend.hosting.accounts import AccountStore
    from backend.hosting.gateway import create_gateway
    from backend.hosting.worker import WorkerIdentityProvider

    store = AccountStore(tmp_path / "accounts.sqlite3")
    users = [store.create_user(name) for name in ("alice", "bob")]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    servers, tasks, profiles = [], [], []

    def worker(profile):
        verifier = WorkerIdentityProvider(profile.secret, profile.profile_id, profile.user_id)

        async def request(request):
            identity = await verifier.authenticate(request.scope)
            if identity is None:
                return JSONResponse({'error': 'authentication_required'}, status_code=401)
            assert identity and identity.subject == profile.user_id
            assert "cookie" not in request.headers
            return JSONResponse({"profile": profile.profile_id})

        async def websocket(ws):
            identity = await verifier.authenticate(ws.scope)
            assert identity and identity.subject == profile.user_id
            assert "cookie" not in ws.headers
            await ws.accept()
            await ws.send_text(profile.profile_id)
            try:
                while True:
                    await ws.send_text(await ws.receive_text())
            except Exception:
                pass

        return Starlette(routes=[Route('/api/world', request), Route('/api/deployment', request),
                                 WebSocketRoute('/ws/events', websocket)])

    # /tmp keeps socket addresses below Linux's 108-byte sockaddr_un limit.
    with tempfile.TemporaryDirectory(prefix="oaw-uds-") as sockets:
        try:
            for index, user in enumerate(users):
                path = f"{sockets}/{index}.sock"
                profile = HostedProfile(str(uuid4()), user["id"], "test", "Private", "builder", "unix://" + path,
                                        f"{index + 1:02x}" * 32)
                profiles.append(profile)
                server = uvicorn.Server(uvicorn.Config(worker(profile), uds=path, lifespan="off", access_log=False, log_level="warning"))
                servers.append(server)
                tasks.append(asyncio.create_task(server.serve()))
            app = create_gateway(GatewaySettings(origin, store.path, tuple(profiles), True), tmp_path, store=store)
            server = uvicorn.Server(uvicorn.Config(app, lifespan="on", access_log=False, log_level="warning"))
            servers.append(server)
            tasks.append(asyncio.create_task(server.serve(sockets=[listener])))
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    await asyncio.sleep(.01)
            async with AsyncExitStack() as stack:
                clients = [await stack.enter_async_context(httpx.AsyncClient(base_url=origin, trust_env=False)) for _ in users]
                for client, user, profile in zip(clients, users, profiles):
                    client.cookies.set("oaw_session", store.create_session(user["id"]))
                    assert (await client.get('/api/world')).json() == {"profile": profile.profile_id}
                url = origin.replace("http:", "ws:") + "/ws/events?instance=" + profiles[0].profile_id
                async with connect(url, origin=origin, additional_headers={"Cookie": "oaw_session=" + clients[0].cookies["oaw_session"]}, proxy=None) as ws:
                    assert await asyncio.wait_for(ws.recv(), 5) == profiles[0].profile_id
                    await ws.send("private event")
                    assert await asyncio.wait_for(ws.recv(), 5) == "private event"
                    store.revoke_session(clients[0].cookies["oaw_session"])
                    with pytest.raises(ConnectionClosed) as error:
                        await asyncio.wait_for(ws.recv(), 5)
                    assert error.value.rcvd.code == 1008
                assert (await clients[0].get('/api/world')).status_code == 401
                assert (await clients[1].get('/api/world')).json() == {"profile": profiles[1].profile_id}
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.wait_for(asyncio.gather(*tasks), 10)
            listener.close()
