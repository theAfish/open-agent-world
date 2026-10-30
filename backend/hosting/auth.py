"""Browser authentication shared by hosted canvas and deployed applications."""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from .accounts import AccountStore, LoginThrottled
from .identity import VerifiedIdentity
if TYPE_CHECKING:
    from .oidc import OidcSettings


SESSION_COOKIE = "oaw_session"
INTERNAL_ISSUER = "urn:oaw:users"
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def public_user(user: dict | None) -> dict | None:
    return {key: user[key] for key in ("id", "username", "display_name")} if user else None


class HostedAuth:
    def __init__(self, store: AccountStore, public_origin: str, *, allow_local_http: bool = False,
                 oidc_settings: OidcSettings | None = None, session_seconds: int = 86400, transport=None):
        origin = urlsplit(public_origin)
        local_http = allow_local_http and origin.scheme == "http" and origin.hostname in {"127.0.0.1", "localhost", "::1"}
        if (not origin.netloc or origin.username or origin.password or origin.path or origin.query or origin.fragment
                or origin.scheme != "https" and not local_http):
            raise ValueError("Hosted public origin requires HTTPS without a path; local HTTP must be explicitly enabled")
        if not 60 <= session_seconds <= 86400:
            raise ValueError("Session duration must be between 60 and 86400 seconds")
        if oidc_settings is not None and oidc_settings.public_origin != public_origin:
            raise ValueError("OIDC and hosted public origins must match")
        self.store, self.public_origin, self.session_seconds = store, public_origin, session_seconds
        self.secure = origin.scheme == "https"
        self.oidc = None
        if oidc_settings:
            from .oidc import OidcSessionIdentity
            self.oidc = OidcSessionIdentity(oidc_settings, transport=transport)
        # Each scrypt operation consumes 32 MiB. Bound concurrent hashing on the
        # small shared gateway, independently of the general ASGI thread pool.
        self._password_slots = asyncio.Semaphore(2)
        self.login_methods = ["password"] + (["oidc"] if self.oidc else [])
        self.routes = [Route("/auth/me", self.me), Route("/auth/login", self.login, methods=["POST"]),
                       Route("/auth/logout", self.logout, methods=["POST"])]
        if self.oidc:
            self.routes.extend([Route("/auth/oidc/login", self.oidc_login),
                                Route("/auth/oidc/callback", self.oidc_callback)])

    def wrap(self, app):
        """Wrap the gateway containing ``routes``; keeps OIDC state separate."""
        if not self.oidc:
            return app
        from starlette.middleware.sessions import SessionMiddleware
        return SessionMiddleware(app, secret_key=self.oidc.settings.session_secret, session_cookie="oaw_oidc",
                                 max_age=600, same_site="lax", https_only=self.secure)

    @staticmethod
    def token_from_scope(scope) -> str | None:
        connection = HTTPConnection(scope)
        # Fail closed on ambiguous cookies (including separate Cookie headers),
        # instead of allowing a subdomain/path cookie to shadow a session.
        count = sum(part.strip().partition("=")[0] == SESSION_COOKIE
                    for header in connection.headers.getlist("cookie") for part in header.split(";"))
        return connection.cookies.get(SESSION_COOKIE) if count == 1 else None

    async def user_from_scope(self, scope) -> dict | None:
        return await run_in_threadpool(self.store.get_session, self.token_from_scope(scope))

    async def authenticate(self, scope) -> VerifiedIdentity | None:
        user = await self.user_from_scope(scope)
        return VerifiedIdentity(INTERNAL_ISSUER, user["id"], user["display_name"]) if user else None

    def _valid_origin(self, request: Request) -> bool:
        return request.headers.getlist("origin") == [self.public_origin]

    def _set_cookie(self, response, token: str, seconds: int):
        response.set_cookie(SESSION_COOKIE, token, max_age=seconds, path="/", secure=self.secure,
                            httponly=True, samesite="lax")

    async def me(self, request: Request):
        user = await self.user_from_scope(request.scope)
        return JSONResponse({"hosted": True, "authenticated": user is not None, "user": public_user(user),
                             "login_methods": self.login_methods}, headers=_NO_STORE)

    async def login(self, request: Request):
        if not self._valid_origin(request):
            return JSONResponse({"error": "Origin rejected"}, status_code=403, headers=_NO_STORE)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "JSON body required"}, status_code=415, headers=_NO_STORE)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 8192:
                return JSONResponse({"error": "Request too large"}, status_code=413, headers=_NO_STORE)
            body.extend(chunk)
        try:
            value = json.loads(body)
            if (not isinstance(value, dict) or not isinstance(value.get("username"), str)
                    or not isinstance(value.get("password"), str)):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return JSONResponse({"error": "Invalid sign-in request"}, status_code=400, headers=_NO_STORE)
        # The trusted reverse proxy can set the ASGI peer. Never parse arbitrary
        # X-Forwarded-For headers supplied by a browser here.
        remote_key = request.client.host if request.client else "unknown"
        try:
            await asyncio.wait_for(self._password_slots.acquire(), timeout=5)
            try:
                user = await run_in_threadpool(self.store.authenticate_password, value["username"], value["password"], remote_key)
            finally:
                self._password_slots.release()
        except (LoginThrottled, TimeoutError):
            return JSONResponse({"error": "Too many sign-in attempts; try again later"}, status_code=429,
                                headers={**_NO_STORE, "Retry-After": "900"})
        if user is None:
            return JSONResponse({"error": "Invalid username or password"}, status_code=401, headers=_NO_STORE)
        try:
            token = await run_in_threadpool(self.store.create_session, user["id"], self.session_seconds,
                                           credential_version=user["_credential_version"])
        except ValueError:
            return JSONResponse({"error": "Invalid username or password"}, status_code=401, headers=_NO_STORE)
        await run_in_threadpool(self.store.revoke_session, self.token_from_scope(request.scope))
        response = JSONResponse({"authenticated": True, "user": public_user(user)}, headers=_NO_STORE)
        self._set_cookie(response, token, self.session_seconds)
        return response

    async def logout(self, request: Request):
        if not self._valid_origin(request):
            return JSONResponse({"error": "Origin rejected"}, status_code=403, headers=_NO_STORE)
        await run_in_threadpool(self.store.revoke_session, self.token_from_scope(request.scope))
        if self.oidc:
            request.session.clear()
        response = JSONResponse({"signed_out": True}, headers=_NO_STORE)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=self.secure, httponly=True, samesite="lax")
        return response

    async def oidc_login(self, request: Request):
        import httpx
        try:
            metadata = await self.oidc.client.load_server_metadata()
            if metadata.get("issuer") != self.oidc.settings.issuer:
                raise ValueError("Issuer mismatch")
            request.session.clear()
            response = await self.oidc.client.authorize_redirect(request, self.public_origin + "/auth/oidc/callback")
            response.headers.update(_NO_STORE)
            return response
        except (httpx.HTTPError, ValueError):
            return JSONResponse({"error": "Sign-in service unavailable"}, status_code=503, headers=_NO_STORE)

    async def oidc_callback(self, request: Request):
        # Reuse the established Authlib flow: signed state, nonce, PKCE, issuer,
        # audience, expiry and the ID token's cryptographic signature are checked
        # by OidcSessionIdentity. Its temporary identity never becomes our session.
        result = await self.oidc.callback(request)
        identity = request.session.pop("identity", None)
        request.session.clear()
        if result.status_code != 303 or not identity:
            return JSONResponse({"error": "Sign-in failed; start a new login"}, status_code=401, headers=_NO_STORE)
        user = await run_in_threadpool(self.store.resolve_identity, identity["issuer"], identity["subject"])
        if user is None:
            return JSONResponse({"error": "Sign-in failed; contact the administrator"}, status_code=401, headers=_NO_STORE)
        seconds = min(self.session_seconds, int(identity["expires_at"] - time.time()))
        try:
            token = await run_in_threadpool(self.store.create_session, user["id"], seconds,
                                           credential_version=user["_credential_version"])
        except ValueError:
            return JSONResponse({"error": "Sign-in failed; start a new login"}, status_code=401, headers=_NO_STORE)
        await run_in_threadpool(self.store.revoke_session, self.token_from_scope(request.scope))
        response = RedirectResponse("/", status_code=303, headers=_NO_STORE)
        self._set_cookie(response, token, seconds)
        return response
