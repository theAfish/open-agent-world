"""Small authenticated HTTP/WebSocket gateway to separately stored worker processes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .accounts import AccountStore
from .auth import HostedAuth
from .configuration import GatewaySettings, unix_socket_path
from .worker import mint_worker_token

_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
                "transfer-encoding", "upgrade"}
_PRIVATE_HEADERS = {"authorization", "cookie", "host", "forwarded", "x-real-ip", "x-oaw-instance"}
_RESPONSE_HEADERS = {"cache-control", "set-cookie", "server", "x-powered-by"}


class Gateway:
    def __init__(self, settings: GatewaySettings, auth: HostedAuth, frontend_directory: Path, *, transport=None, settings_path=None):
        self.settings, self.auth = settings, auth
        self.frontend_directory = frontend_directory
        self.settings_path, self.transport = settings_path, transport
        self.settings_stamp = None
        from .deployments import DeploymentStore
        self.deployments = DeploymentStore(settings.accounts_path.parent / "deployments.sqlite3")
        self.http = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(300, connect=10), limits=httpx.Limits(max_connections=64))
        self.unix_clients = {
            profile.profile_id: httpx.AsyncClient(
                transport=transport or httpx.AsyncHTTPTransport(uds=unix_socket_path(profile.upstream)),
                trust_env=False, follow_redirects=False, timeout=httpx.Timeout(300, connect=10),
            ) for profile in settings.profiles if unix_socket_path(profile.upstream)
        }

    @property
    def profiles(self):
        choices = self.deployments.entries()
        entries = {}
        for profile in self.settings.profiles:
            if profile.user_id not in choices:
                entries.setdefault(profile.user_id, profile)
            elif choices[profile.user_id] == profile.profile_id:
                entries[profile.user_id] = profile
        return entries

    async def refresh(self):
        if self.settings_path is None:
            return
        stamp = self.settings_path.stat().st_mtime_ns
        if stamp == self.settings_stamp:
            return
        from .configuration import read_gateway_settings
        settings = read_gateway_settings(self.settings_path)
        if (settings.public_origin != self.settings.public_origin or settings.accounts_path != self.settings.accounts_path
                or settings.shared_models_origin != self.settings.shared_models_origin
                or settings.shared_environment_origin != self.settings.shared_environment_origin):
            raise ValueError("Restart the gateway to change hosting service configuration")
        for profile in settings.profiles:
            if unix_socket_path(profile.upstream) and profile.profile_id not in self.unix_clients:
                self.unix_clients[profile.profile_id] = httpx.AsyncClient(
                    transport=self.transport or httpx.AsyncHTTPTransport(uds=unix_socket_path(profile.upstream)),
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(300, connect=10))
        self.settings, self.settings_stamp = settings, stamp

    async def close(self):
        await self.http.aclose()
        for client in self.unix_clients.values():
            await client.aclose()

    def error(self, status: int, code: str, message: str, profile=None):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                            headers={"X-OAW-Instance": profile.profile_id} if profile else {})

    async def me(self, request: Request):
        await self.refresh()
        user = await self.auth.user_from_scope(request.scope)
        profile = self.profiles.get(user["id"]) if user else None
        return JSONResponse({"hosted": True, "authenticated": user is not None,
                             "user": {key: user[key] for key in ("id", "username", "display_name", "role")} if user else None,
                             "login_methods": self.auth.login_methods,
                             "shared_models": self.settings.shared_models_origin is not None,
                             "instance": profile.public() if profile else None})

    async def shell(self, request: Request):
        entry = self.frontend_directory / "index.html"
        if not entry.is_file():
            return self.error(503, "frontend_unavailable", "Build the frontend before starting the hosted service.")
        return FileResponse(entry, media_type="text/html")

    def check_origin(self, scope) -> bool:
        headers = scope.get("headers", [])
        origins = [v.decode("latin-1") for k, v in headers if k.lower() == b"origin"]
        unsafe = scope["type"] == "websocket" or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
        return origins == [self.settings.public_origin] or (not unsafe and not origins)

    def instance_matches(self, request, profile, *, required=False) -> bool:
        supplied = request.headers.getlist("x-oaw-instance") + request.query_params.getlist("instance")
        return (bool(supplied) or not required) and all(value == profile.profile_id for value in supplied)

    def upstream_headers(self, request, profile, user):
        connection = {value.strip().lower() for value in request.headers.get("connection", "").split(",")}
        headers = [(key, value) for key, value in request.headers.items()
                   if key.lower() not in _HOP_HEADERS | _PRIVATE_HEADERS | connection
                   and not key.lower().startswith(("x-forwarded-", "sec-websocket-"))]
        headers.append(("authorization", "Bearer " + mint_worker_token(
            profile.secret, profile.profile_id, profile.user_id, user["display_name"])))
        return headers

    async def proxy(self, request: Request):
        if not self.check_origin(request.scope):
            return self.error(403, "origin_rejected", "Origin rejected")
        user = await self.auth.user_from_scope(request.scope)
        if user is None:
            return self.error(401, "authentication_required", "Sign in to continue.")
        await self.refresh()
        if request.url.path.startswith("/api/hosting-template") and user["role"] != "admin":
            return self.error(403, "admin_required", "Administrator access required.")
        profile = self.profiles.get(user["id"])
        if profile is None:
            return self.error(403, "workspace_unassigned", "No workspace has been assigned to this account.")
        if not self.instance_matches(request, profile, required=request.method not in {"GET", "HEAD", "OPTIONS"}):
            return self.error(409, "instance_changed", "Reload this workspace.", profile)
        # copy_with preserves the configured authority even for // or encoded paths.
        raw_path = request.scope.get("raw_path", request.url.path.encode("utf-8"))
        query = request.scope.get("query_string", b"")
        origin = "http://localhost" if unix_socket_path(profile.upstream) else profile.upstream
        client = self.unix_clients.get(profile.profile_id, self.http)
        target = httpx.URL(origin).copy_with(raw_path=raw_path + (b"?" + query if query else b""))
        # Cold Sandbox preparation is independently bounded by the worker and
        # can exceed five minutes before a command is even admitted.
        sandbox_operation = request.method == "POST" and re.fullmatch(
            r"/api/(?:runtime-app/workspace/)?sandboxes/[^/]+/(?:start|execute|python/packages)", request.url.path)
        timeout = httpx.Timeout(300, connect=10, read=3600 if sandbox_operation else 300)
        try:
            outgoing = client.build_request(request.method, target, headers=self.upstream_headers(request, profile, user),
                                               content=request.stream(), timeout=timeout)
            # AsyncClient also applies its own upstream cookie jar. Those
            # cookies must never become worker authority or cross profiles on
            # the same hostname at different ports.
            outgoing.headers.pop("cookie", None)
            response = await client.send(outgoing, stream=True)
            client.cookies.clear()
        except httpx.ReadTimeout:
            return self.error(504, "workspace_operation_timeout",
                "The request timed out; the operation may still be running. Inspect its status before retrying.", profile)
        except httpx.HTTPError:
            return self.error(503, "workspace_unavailable", "This workspace is temporarily unavailable.", profile)
        connection = {part.strip().lower() for part in response.headers.get("connection", "").split(",")}
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in _HOP_HEADERS | _RESPONSE_HEADERS | connection}
        # Workers must not redirect the browser to their private network address.
        if "location" in headers and headers["location"].startswith(origin + "/"):
            headers["location"] = self.settings.public_origin + headers["location"][len(origin):]
        headers["X-OAW-Instance"] = profile.profile_id
        # In-memory transports can return a response whose body was decoded
        # eagerly. Network responses retain the raw stream and encoding headers.
        if response.is_stream_consumed:
            headers.pop("content-encoding", None)
            headers["content-length"] = str(len(response.content))

        async def content():
            try:
                if response.is_stream_consumed:
                    yield response.content
                else:
                    async for chunk in response.aiter_raw():
                        yield chunk
            finally:
                await response.aclose()

        return StreamingResponse(content(), status_code=response.status_code, headers=headers)

    async def socket(self, socket: WebSocket):
        await self.refresh()
        user = await self.auth.user_from_scope(socket.scope)
        profile = self.profiles.get(user["id"]) if user else None
        if not profile or not self.check_origin(socket.scope) or not self.instance_matches(socket, profile, required=True):
            return await socket.close(code=1008)
        if socket.url.path not in {"/ws/events", "/ws/visual"}:
            return await socket.close(code=1008)
        from websockets.asyncio.client import connect, unix_connect
        from websockets.exceptions import ConnectionClosed, InvalidHandshake
        socket_path = unix_socket_path(profile.upstream)
        target = httpx.URL("http://localhost" if socket_path else profile.upstream).copy_with(
            scheme="wss" if profile.upstream.startswith("https:") else "ws", path=socket.url.path,
            query=socket.scope.get("query_string", b""))
        # Reauthentication uses the original opaque session; logout and account
        # disable revoke it in SQLite and terminate existing connections promptly.
        try:
            options = dict(additional_headers=self.upstream_headers(socket, profile, user),
                           open_timeout=10, max_size=16 * 1024 * 1024)
            connection = (unix_connect(socket_path, uri=str(target), **options) if socket_path
                          else connect(str(target), proxy=None, **options))
            async with connection as upstream:
                await socket.accept()

                async def from_browser():
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        await upstream.send(message.get("text") if message.get("text") is not None else message["bytes"])

                async def from_worker():
                    async for value in upstream:
                        if isinstance(value, str):
                            await socket.send_text(value)
                        else:
                            await socket.send_bytes(value)

                async def revoked():
                    while True:
                        await asyncio.sleep(1)
                        current = await self.auth.user_from_scope(socket.scope)
                        if current is None or current["id"] != user["id"]:
                            return
                        await self.refresh()
                        selected = self.profiles.get(user["id"])
                        if selected is None or selected.profile_id != profile.profile_id:
                            return

                tasks = [asyncio.create_task(action()) for action in (from_browser, from_worker, revoked)]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake, WebSocketDisconnect):
            pass
        finally:
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=1008)


class GatewayHeaders:
    def __init__(self, app, origin):
        self.app, self.host = app, urlsplit(origin).netloc.lower()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        hosts = [v.decode("latin-1").lower() for k, v in scope.get("headers", []) if k.lower() == b"host"]
        if hosts != [self.host]:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse({"error": {"code": "host_rejected", "message": "Host rejected"}}, status_code=400)(scope, receive, send)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                values = {b"cache-control": b"no-store, private", b"x-content-type-options": b"nosniff",
                          b"referrer-policy": b"no-referrer", b"x-frame-options": b"DENY"}
                # Separate CSP policies intersect. Preserve an asset's stricter
                # default-src/sandbox rules while adding gateway constraints.
                policy = (b"content-security-policy", b"frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
                message = {**message, "headers": [(k, v) for k, v in message.get("headers", []) if k.lower() not in values]
                           + list(values.items()) + [policy]}
            await send(message)
        await self.app(scope, receive, protected_send)


def create_gateway(settings: GatewaySettings, frontend_directory: Path, *, oidc_settings=None, transport=None,
                   auth_transport=None, store=None, settings_path=None):
    store = store or AccountStore(settings.accounts_path)
    auth = HostedAuth(store, settings.public_origin, allow_local_http=settings.allow_local_http,
                      oidc_settings=oidc_settings, transport=auth_transport)
    gateway = Gateway(settings, auth, Path(frontend_directory), transport=transport, settings_path=settings_path)
    from .shared_models import SharedModelAdmin
    admin = SharedModelAdmin(gateway)
    from .deployments import DeploymentAdmin
    deployments = DeploymentAdmin(gateway, admin.authorize)
    from .shared_environment import SharedEnvironmentAdmin
    environment = SharedEnvironmentAdmin(gateway, admin.authorize)

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            await gateway.close()

    async def health(_):
        return JSONResponse({"status": "ok"})

    # Static assets are the only public project files. No profile directory is mounted.
    from starlette.staticfiles import StaticFiles
    from starlette.routing import Mount
    routes = [Route("/health/live", health), Route("/auth/me", gateway.me)]
    routes.extend(route for route in auth.routes if route.path != "/auth/me")
    routes.extend([Route("/hosting/admin/models", admin.models, methods=["GET", "PUT"]),
                   Route("/hosting/admin/environment", environment.settings, methods=["GET", "PUT"]),
                   Route("/hosting/internal/environment/{profile_id}", environment.worker_environment),
                   Route("/hosting/admin/deployments", deployments.catalog, methods=["GET"]),
                   Route("/hosting/admin/deployments", deployments.deploy, methods=["POST"]),
                   Route("/hosting/admin/entry", deployments.assign, methods=["PUT"]),
                   Route("/hosting/admin/models/discover", admin.discover, methods=["POST"]),
                   Route("/hosting/admin/status", admin.status),
                   Route("/hosting/internal/models/{profile_id}", admin.worker_catalog),
                   Route("/admin", gateway.shell), Route("/", gateway.shell), Route("/auth/login", gateway.shell),
                   Mount("/assets", StaticFiles(directory=Path(frontend_directory) / "assets", check_dir=False)),
                   WebSocketRoute("/ws/{path:path}", gateway.socket),
                   Route("/{path:path}", gateway.proxy, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])])
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.gateway = gateway
    return GatewayHeaders(auth.wrap(app), settings.public_origin)
