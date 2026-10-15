"""Optional OIDC browser login; the identity service owns accounts/passwords.

Only verified identity claims and an expiry enter the signed, HttpOnly cookie.
Access/refresh/ID tokens and client secrets never enter browser storage.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import time
import re
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from .identity import VerifiedIdentity


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    discovery_url: str
    client_id: str
    client_secret: str = field(repr=False)
    session_secret: str = field(repr=False)
    public_origin: str
    session_seconds: int = 3600
    allow_local_http: bool = False
    scopes: tuple[str, ...] = ("openid", "profile")
    token_endpoint_auth_method: str = "client_secret_basic"

    def __post_init__(self):
        if len(self.session_secret) < 32 or not self.client_id or not self.client_secret:
            raise ValueError("OIDC requires a client ID, client secret and stable session secret of at least 32 characters")
        if not 60 <= self.session_seconds <= 3600:
            raise ValueError("OIDC session duration must be between 60 and 3600 seconds")
        if ("openid" not in self.scopes or len(set(self.scopes)) != len(self.scopes)
                or any(not re.fullmatch(r'[\x21\x23-\x5b\x5d-\x7e]+', scope) for scope in self.scopes)):
            raise ValueError("OIDC scopes must be unique OAuth scope tokens and include openid")
        if self.token_endpoint_auth_method not in {"client_secret_basic", "client_secret_post"}:
            raise ValueError("OIDC requires client_secret_basic or client_secret_post authentication")
        for value in (self.issuer, self.discovery_url, self.public_origin):
            url = urlsplit(value)
            local_http = self.allow_local_http and url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}
            if (not url.netloc or url.username or url.password or url.fragment
                    or (url.scheme != "https" and not local_http)):
                raise ValueError("OIDC endpoints require HTTPS (local HTTP must be explicitly enabled)")
        url = urlsplit(self.public_origin)
        if url.path or url.query:
            raise ValueError("OIDC public origin must not contain a path or query")


class OidcSessionIdentity:
    """Use only beneath our signed SessionMiddleware, not a client-supplied scope."""

    def __init__(self, settings: OidcSettings, *, transport=None):
        from authlib.integrations.starlette_client import OAuth

        self.settings = settings
        client_kwargs = {"scope": " ".join(settings.scopes),
                         "token_endpoint_auth_method": settings.token_endpoint_auth_method,
                         "code_challenge_method": "S256", "timeout": 15,
                         "follow_redirects": False}
        if transport is not None:
            client_kwargs["transport"] = transport
        self.client = OAuth().register(
            "identity", client_id=settings.client_id, client_secret=settings.client_secret,
            server_metadata_url=settings.discovery_url, client_kwargs=client_kwargs,
        )

    async def authenticate(self, scope):
        value = scope.get("session", {}).get("identity")
        if not isinstance(value, dict):
            return None
        if (value.get("issuer") != self.settings.issuer or not isinstance(value.get("subject"), str)
                or not value["subject"] or not isinstance(value.get("expires_at"), (int, float))
                or value["expires_at"] <= time.time()):
            return None
        return VerifiedIdentity(value["issuer"], value["subject"], value.get("display_name"))

    async def login(self, request: Request):
        metadata = await self.client.load_server_metadata()
        if metadata.get("issuer") != self.settings.issuer:
            return JSONResponse({"error": "Identity provider issuer mismatch"}, status_code=503)
        # No caller-selected redirect; also discard any previous login session.
        request.session.clear()
        return await self.client.authorize_redirect(request, self.settings.public_origin + "/auth/callback")

    async def callback(self, request: Request):
        from authlib.integrations.base_client.errors import OAuthError
        from joserfc.errors import JoseError
        import httpx

        try:
            token = await self.client.authorize_access_token(request)
            claims = token.get("userinfo") if token.get("id_token") else None
            if (not claims or claims.get("iss") != self.settings.issuer
                    or not isinstance(claims.get("sub"), str) or not claims["sub"]):
                raise ValueError("Invalid OIDC identity")
            expires_at = min(float(claims["exp"]), time.time() + self.settings.session_seconds)
            if expires_at <= time.time():
                raise ValueError("Expired OIDC identity")
        except (OAuthError, JoseError, httpx.HTTPError, KeyError, TypeError, ValueError):
            request.session.clear()
            return JSONResponse({"error": "Sign-in failed; start a new login"}, status_code=401,
                                headers={"Cache-Control": "no-store"})
        request.session.clear()
        request.session["identity"] = {
            "issuer": claims["iss"], "subject": claims["sub"],
            "display_name": claims.get("name"), "expires_at": expires_at,
        }
        return RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})

    async def logout(self, request: Request):
        if request.headers.get("origin") != self.settings.public_origin:
            return JSONResponse({"error": "Origin rejected"}, status_code=403)
        request.session.clear()
        return JSONResponse({"signed_out": True}, headers={"Cache-Control": "no-store"})

    def wrap(self, application):
        @asynccontextmanager
        async def lifespan(_):
            async with application.router.lifespan_context(application):
                yield

        app = Starlette(routes=[
            Route("/auth/login", self.login), Route("/auth/callback", self.callback),
            Route("/auth/logout", self.logout, methods=["POST"]), Mount("/", application),
        ], lifespan=lifespan)
        app.add_middleware(SessionMiddleware, secret_key=self.settings.session_secret,
                           session_cookie="oaw_identity", max_age=self.settings.session_seconds,
                           same_site="lax", https_only=self.settings.public_origin.startswith("https://"))
        return app
