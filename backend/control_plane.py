"""Keep the trusted desktop API out of the Sandbox's public egress surface.

Sandbox runtimes deny host loopback and local destinations. This independent
ingress check also rejects public-address/NAT hairpin routes to the application.
ASGI servers must retain the actual socket peer (uvicorn --no-proxy-headers).
An authenticated remote integration can supply a host-owned bearer credential;
it is never supplied to Sandbox commands or automatically attached by a proxy.
"""

from __future__ import annotations

import hmac
from ipaddress import ip_address

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def is_loopback_peer(host: str) -> bool:
    """Only numeric socket addresses confer local trust, never DNS/Host names."""
    try:
        address = ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


class ControlPlaneMiddleware:
    def __init__(self, app: ASGIApp, *, token: str | None = None) -> None:
        self.app = app
        self._token = token.encode("utf-8") if token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        # Reject forwarded unauthenticated traffic even if an upstream ASGI
        # adapter rewrote the peer. A local reverse proxy is not caller auth.
        forwarded = any(
            name.lower() in {b"forwarded", b"x-real-ip"}
            or name.lower().startswith(b"x-forwarded-")
            for name, _ in headers
        )
        peer = scope.get("client")
        local = bool(peer and is_loopback_peer(peer[0]) and not forwarded)
        authorization = [value for name, value in headers if name.lower() == b"authorization"]
        authenticated = bool(
            self._token and len(authorization) == 1
            and hmac.compare_digest(authorization[0], b"Bearer " + self._token)
        )
        if local or authenticated:
            await self.app(scope, receive, send)
            return

        message = "Management access requires a local host connection or the host control-plane credential."
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": message})
        else:
            await JSONResponse(
                status_code=403,
                content={"error": {"code": "control_plane_access_denied", "message": message}},
            )(scope, receive, send)
