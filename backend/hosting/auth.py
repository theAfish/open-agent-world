"""Browser authentication shared by hosted canvas and deployed applications."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from .accounts import (
    DEFAULT_SESSION_SECONDS,
    PERSISTENT_SESSION_SECONDS,
    AccountConflict,
    AccountStore,
    CodeThrottled,
    LoginThrottled,
    detect_contact_channel,
)
from .identity import VerifiedIdentity
from .verification import VerificationDeliveryError, VerificationDispatcher

if TYPE_CHECKING:
    from .oidc import OidcSettings


SESSION_COOKIE = "oaw_session"
INTERNAL_ISSUER = "urn:oaw:users"
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def public_user(user: dict | None) -> dict | None:
    return {key: user[key] for key in ("id", "username", "display_name")} if user else None


def account_summary(user: dict, *, active_sessions: int | None = None) -> dict:
    payload = {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "email": user.get("email"),
        "phone_masked": user.get("phone_masked"),
        "has_password": bool(user.get("has_password")),
        "session_persistent": bool(user.get("session_persistent")),
        "session_expires_at": user.get("session_expires_at"),
    }
    if active_sessions is not None:
        payload["active_sessions"] = active_sessions
    return payload


class HostedAuth:
    def __init__(self, store: AccountStore, public_origin: str, *, allow_local_http: bool = False,
                 oidc_settings: OidcSettings | None = None,
                 verification: VerificationDispatcher | None = None,
                 session_seconds: int = DEFAULT_SESSION_SECONDS,
                 persistent_session_seconds: int = PERSISTENT_SESSION_SECONDS,
                 on_user_registered: Callable[[dict], object] | None = None,
                 transport=None):
        origin = urlsplit(public_origin)
        local_http = allow_local_http and origin.scheme == "http" and origin.hostname in {"127.0.0.1", "localhost", "::1"}
        if (not origin.netloc or origin.username or origin.password or origin.path or origin.query or origin.fragment
                or origin.scheme != "https" and not local_http):
            raise ValueError("Hosted public origin requires HTTPS without a path; local HTTP must be explicitly enabled")
        if not 60 <= session_seconds <= PERSISTENT_SESSION_SECONDS:
            raise ValueError(f"Session duration must be between 60 and {PERSISTENT_SESSION_SECONDS} seconds")
        if not session_seconds <= persistent_session_seconds <= PERSISTENT_SESSION_SECONDS:
            raise ValueError("Invalid persistent session duration")
        if oidc_settings is not None and oidc_settings.public_origin != public_origin:
            raise ValueError("OIDC and hosted public origins must match")
        self.store = store
        self.public_origin = public_origin
        self.session_seconds = session_seconds
        self.persistent_session_seconds = persistent_session_seconds
        self.verification = verification
        self.on_user_registered = on_user_registered
        self.secure = origin.scheme == "https"
        self.oidc = None
        if oidc_settings:
            from .oidc import OidcSessionIdentity
            self.oidc = OidcSessionIdentity(oidc_settings, transport=transport)
        # Each scrypt operation consumes 32 MiB. Bound concurrent hashing on the
        # small shared gateway, independently of the general ASGI thread pool.
        self._password_slots = asyncio.Semaphore(2)
        self.registration_enabled = self.verification is not None
        self.login_methods = (
            ["password"]
            + (["code"] if self.verification else [])
            + (["oidc"] if self.oidc else [])
        )
        self.routes = [
            Route("/auth/me", self.me),
            Route("/auth/login", self.login, methods=["POST"]),
            Route("/auth/logout", self.logout, methods=["POST"]),
            Route("/auth/account", self.account, methods=["GET", "PATCH"]),
            Route("/auth/account/password", self.account_password, methods=["POST"]),
            Route("/auth/account/sessions/revoke-others", self.revoke_other_sessions, methods=["POST"]),
        ]
        if self.verification:
            self.routes.extend([
                Route("/auth/code/send", self.send_code, methods=["POST"]),
                Route("/auth/login/code", self.login_code, methods=["POST"]),
                Route("/auth/register/email", self.register_email, methods=["POST"]),
                Route("/auth/register/complete", self.register_complete, methods=["POST"]),
                Route("/auth/password/reset", self.reset_password, methods=["POST"]),
                Route("/auth/account/bind", self.account_bind, methods=["POST"]),
            ])
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

    async def user_from_scope(self, scope, *, touch: bool = False) -> dict | None:
        token = self.token_from_scope(scope)
        return await run_in_threadpool(lambda: self.store.get_session(token, touch=touch))

    async def authenticate(self, scope) -> VerifiedIdentity | None:
        user = await self.user_from_scope(scope)
        return VerifiedIdentity(INTERNAL_ISSUER, user["id"], user["display_name"]) if user else None

    def _valid_origin(self, request: Request) -> bool:
        return request.headers.getlist("origin") == [self.public_origin]

    def _set_cookie(self, response, token: str, seconds: int):
        response.set_cookie(SESSION_COOKIE, token, max_age=seconds, path="/", secure=self.secure,
                            httponly=True, samesite="lax")

    def apply_renewed_cookie(self, request: Request, response, user: dict | None) -> None:
        token = self.token_from_scope(request.scope)
        if user and token and user.get("session_renewed"):
            self._set_cookie(response, token, int(user["session_renewed"]))

    def _error(self, status: int, code: str, message: str, extra_headers: dict | None = None) -> JSONResponse:
        headers = {**_NO_STORE, **(extra_headers or {})}
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)

    async def _read_json_dict(self, request: Request, *, max_bytes: int = 8192) -> tuple[dict | None, JSONResponse | None]:
        if not self._valid_origin(request):
            return None, JSONResponse({"error": "Origin rejected"}, status_code=403, headers=_NO_STORE)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return None, JSONResponse({"error": "JSON body required"}, status_code=415, headers=_NO_STORE)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > max_bytes:
                return None, JSONResponse({"error": "Request too large"}, status_code=413, headers=_NO_STORE)
            body.extend(chunk)
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError()
            return value, None
        except (ValueError, UnicodeError, RecursionError):
            return None, JSONResponse({"error": "Invalid request"}, status_code=400, headers=_NO_STORE)

    def _session_duration(self, remember_me: bool) -> tuple[int, bool]:
        if remember_me:
            return self.persistent_session_seconds, True
        return self.session_seconds, False

    async def _issue_session_response(self, request: Request, user: dict, *, remember_me: bool,
                                      payload: dict) -> JSONResponse:
        seconds, persistent = self._session_duration(remember_me)
        token = await run_in_threadpool(
            lambda: self.store.create_session(
                user["id"], seconds, credential_version=user["_credential_version"], persistent=persistent
            )
        )
        await run_in_threadpool(self.store.revoke_session, self.token_from_scope(request.scope))
        response = JSONResponse(payload, headers=_NO_STORE)
        self._set_cookie(response, token, seconds)
        return response

    async def me(self, request: Request):
        user = await self.user_from_scope(request.scope, touch=True)
        payload = {
            "hosted": True,
            "authenticated": user is not None,
            "user": public_user(user),
            "login_methods": self.login_methods,
        }
        if self.registration_enabled:
            payload["registration_enabled"] = True
        response = JSONResponse(payload, headers=_NO_STORE)
        self.apply_renewed_cookie(request, response, user)
        return response

    async def login(self, request: Request):
        value, err = await self._read_json_dict(request)
        if err is not None:
            return err
        remember_me = value.get("remember_me", False)
        if (not isinstance(value.get("username"), str) or not isinstance(value.get("password"), str)
                or type(remember_me) is not bool):
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
            return await self._issue_session_response(
                request, user, remember_me=remember_me,
                payload={"authenticated": True, "user": public_user(user)},
            )
        except ValueError:
            return JSONResponse({"error": "Invalid username or password"}, status_code=401, headers=_NO_STORE)

    async def send_code(self, request: Request):
        value, err = await self._read_json_dict(request, max_bytes=4096)
        if err is not None:
            return err
        target = value.get("target")
        purpose = value.get("purpose")
        if not isinstance(target, str) or not isinstance(purpose, str):
            return self._error(400, "invalid_request", "Target and purpose are required.")
        if purpose in {"bind_email", "bind_phone"}:
            user = await self.user_from_scope(request.scope)
            if user is None:
                return self._error(401, "authentication_required", "Sign in to continue.")
        try:
            channel, normalized = detect_contact_channel(target)
            if purpose in {"register_email", "bind_email"} and channel != "email":
                raise ValueError("Enter a valid email address.")
            if purpose in {"register_phone", "bind_phone"} and channel != "sms":
                raise ValueError("Enter a valid phone number.")
        except ValueError as exc:
            return self._error(422, "invalid_target", str(exc))

        remote_key = request.client.host if request.client else "unknown"
        try:
            normalized, code = await run_in_threadpool(
                self.store.issue_verification_code, channel, normalized, purpose, remote_key
            )
            await self.verification.send(channel, normalized, code, purpose)
        except AccountConflict as exc:
            return self._error(409, exc.code, str(exc))
        except CodeThrottled as exc:
            return self._error(429, "code_throttled", "Please wait before requesting another code.",
                               {"Retry-After": str(exc.retry_after)})
        except VerificationDeliveryError as exc:
            return self._error(503, "delivery_failed", str(exc))
        except ValueError as exc:
            return self._error(422, "invalid_target", str(exc))
        return JSONResponse({"sent": True, "channel": channel, "cooldown_seconds": 60}, headers=_NO_STORE)

    async def login_code(self, request: Request):
        value, err = await self._read_json_dict(request, max_bytes=4096)
        if err is not None:
            return err
        target = value.get("target")
        code = value.get("code")
        remember_me = value.get("remember_me", False)
        if not isinstance(target, str) or not isinstance(code, str) or type(remember_me) is not bool:
            return self._error(400, "invalid_request", "Invalid sign-in request.")
        remote_key = request.client.host if request.client else "unknown"
        try:
            user = await run_in_threadpool(self.store.authenticate_code, target, code, remote_key)
        except LoginThrottled:
            return self._error(429, "throttled", "Too many sign-in attempts; try again later.", {"Retry-After": "900"})
        except ValueError:
            return self._error(401, "invalid_credentials", "Invalid verification code or unregistered account.")
        if user is None:
            return self._error(401, "invalid_credentials", "Invalid verification code or unregistered account.")
        try:
            return await self._issue_session_response(
                request, user, remember_me=remember_me,
                payload={"authenticated": True, "user": public_user(user)},
            )
        except ValueError:
            return self._error(401, "invalid_credentials", "Account is unavailable.")

    async def register_email(self, request: Request):
        value, err = await self._read_json_dict(request)
        if err is not None:
            return err
        username = value.get("username")
        email = value.get("email")
        code = value.get("code")
        password = value.get("password")
        display_name = value.get("display_name")
        if (not isinstance(username, str) or not isinstance(email, str)
                or not isinstance(code, str) or not isinstance(password, str)
                or (display_name is not None and not isinstance(display_name, str))):
            return self._error(400, "invalid_request", "Username, email, verification code, and password are required.")
        remote_key = request.client.host if request.client else "unknown"
        try:
            await asyncio.wait_for(self._password_slots.acquire(), timeout=5)
            try:
                ticket = await run_in_threadpool(
                    lambda: self.store.start_email_registration(
                        email, code, password, remote_key, username=username, display_name=display_name
                    )
                )
            finally:
                self._password_slots.release()
        except AccountConflict as exc:
            return self._error(409, exc.code, str(exc))
        except (LoginThrottled, TimeoutError):
            return self._error(429, "throttled", "Too many attempts; try again later.", {"Retry-After": "900"})
        except ValueError as exc:
            return self._error(422, "invalid_registration", str(exc))
        if ticket is None:
            return self._error(401, "invalid_code", "Invalid or expired email verification code.")
        return JSONResponse(ticket, headers=_NO_STORE)

    async def register_complete(self, request: Request):
        value, err = await self._read_json_dict(request, max_bytes=4096)
        if err is not None:
            return err
        ticket = value.get("register_ticket")
        phone = value.get("phone")
        code = value.get("code")
        remember_me = value.get("remember_me", True)
        if (not isinstance(ticket, str) or not isinstance(phone, str) or not isinstance(code, str)
                or type(remember_me) is not bool):
            return self._error(400, "invalid_request", "Registration ticket, phone, and code are required.")
        remote_key = request.client.host if request.client else "unknown"
        try:
            user, merged = await run_in_threadpool(self.store.complete_registration, ticket, phone, code, remote_key)
        except AccountConflict as exc:
            return self._error(409, exc.code, str(exc))
        except PermissionError as exc:
            return self._error(401, "invalid_code", str(exc))
        except LoginThrottled:
            return self._error(429, "throttled", "Too many attempts; try again later.", {"Retry-After": "900"})
        except ValueError as exc:
            return self._error(422, "invalid_registration", str(exc))

        if self.on_user_registered is not None:
            result = self.on_user_registered(user)
            if inspect.isawaitable(result):
                await result

        try:
            return await self._issue_session_response(
                request, user, remember_me=remember_me,
                payload={"authenticated": True, "merged": merged, "user": public_user(user)},
            )
        except ValueError:
            return self._error(401, "account_unavailable", "Account is unavailable.")

    async def reset_password(self, request: Request):
        value, err = await self._read_json_dict(request)
        if err is not None:
            return err
        target = value.get("target")
        code = value.get("code")
        password = value.get("password")
        remember_me = value.get("remember_me", True)
        if (not isinstance(target, str) or not isinstance(code, str) or not isinstance(password, str)
                or type(remember_me) is not bool):
            return self._error(400, "invalid_request", "Account contact, code, and new password are required.")
        remote_key = request.client.host if request.client else "unknown"
        try:
            await asyncio.wait_for(self._password_slots.acquire(), timeout=5)
            try:
                user = await run_in_threadpool(self.store.reset_password_with_code, target, code, password, remote_key)
            finally:
                self._password_slots.release()
        except (LoginThrottled, TimeoutError):
            return self._error(429, "throttled", "Too many attempts; try again later.", {"Retry-After": "900"})
        except ValueError as exc:
            return self._error(422, "invalid_password", str(exc))
        if user is None:
            return self._error(401, "invalid_code", "Invalid verification code or unregistered account.")
        return await self._issue_session_response(
            request, user, remember_me=remember_me,
            payload={"authenticated": True, "user": public_user(user)},
        )

    async def account(self, request: Request):
        user = await self.user_from_scope(request.scope)
        if user is None:
            return self._error(401, "authentication_required", "Sign in to continue.")
        if request.method == "GET":
            count = await run_in_threadpool(self.store.count_sessions, user["id"])
            return JSONResponse({"user": account_summary(user, active_sessions=count)}, headers=_NO_STORE)
        value, err = await self._read_json_dict(request, max_bytes=4096)
        if err is not None:
            return err
        display_name = value.get("display_name")
        if display_name is not None and not isinstance(display_name, str):
            return self._error(400, "invalid_request", "Invalid display name.")
        try:
            updated = await run_in_threadpool(lambda: self.store.update_profile(user["id"], display_name=display_name))
            count = await run_in_threadpool(self.store.count_sessions, user["id"])
        except ValueError as exc:
            return self._error(422, "invalid_profile", str(exc))
        updated["session_persistent"] = user.get("session_persistent", False)
        updated["session_expires_at"] = user.get("session_expires_at")
        return JSONResponse({"user": account_summary(updated, active_sessions=count)}, headers=_NO_STORE)

    async def account_password(self, request: Request):
        user = await self.user_from_scope(request.scope)
        if user is None:
            return self._error(401, "authentication_required", "Sign in to continue.")
        value, err = await self._read_json_dict(request)
        if err is not None:
            return err
        new_password = value.get("new_password")
        current_password = value.get("current_password")
        if not isinstance(new_password, str) or (current_password is not None and not isinstance(current_password, str)):
            return self._error(400, "invalid_request", "Invalid password update request.")
        try:
            await asyncio.wait_for(self._password_slots.acquire(), timeout=5)
            try:
                updated = await run_in_threadpool(
                    lambda: self.store.change_password(user["id"], new_password, current_password=current_password)
                )
            finally:
                self._password_slots.release()
        except PermissionError as exc:
            return self._error(401, "invalid_password", str(exc))
        except TimeoutError:
            return self._error(429, "throttled", "Too many attempts; try again later.", {"Retry-After": "60"})
        except ValueError as exc:
            return self._error(422, "invalid_password", str(exc))
        return await self._issue_session_response(
            request, updated, remember_me=bool(user.get("session_persistent")),
            payload={"updated": True, "user": account_summary(updated, active_sessions=1)},
        )

    async def account_bind(self, request: Request):
        user = await self.user_from_scope(request.scope)
        if user is None:
            return self._error(401, "authentication_required", "Sign in to continue.")
        value, err = await self._read_json_dict(request, max_bytes=4096)
        if err is not None:
            return err
        target = value.get("target")
        code = value.get("code")
        if not isinstance(target, str) or not isinstance(code, str):
            return self._error(400, "invalid_request", "Contact and verification code are required.")
        remote_key = request.client.host if request.client else "unknown"
        try:
            updated = await run_in_threadpool(self.store.bind_contact, user["id"], target, code, remote_key)
            count = await run_in_threadpool(self.store.count_sessions, user["id"])
        except AccountConflict as exc:
            return self._error(409, exc.code, str(exc))
        except PermissionError as exc:
            return self._error(401, "invalid_code", str(exc))
        except LoginThrottled:
            return self._error(429, "throttled", "Too many attempts; try again later.", {"Retry-After": "900"})
        except ValueError as exc:
            return self._error(422, "invalid_target", str(exc))
        updated["session_persistent"] = user.get("session_persistent", False)
        updated["session_expires_at"] = user.get("session_expires_at")
        return JSONResponse({"user": account_summary(updated, active_sessions=count)}, headers=_NO_STORE)

    async def revoke_other_sessions(self, request: Request):
        if not self._valid_origin(request):
            return JSONResponse({"error": "Origin rejected"}, status_code=403, headers=_NO_STORE)
        user = await self.user_from_scope(request.scope)
        if user is None:
            return self._error(401, "authentication_required", "Sign in to continue.")
        token = self.token_from_scope(request.scope)
        revoked = await run_in_threadpool(self.store.revoke_other_sessions, user["id"], token)
        count = await run_in_threadpool(self.store.count_sessions, user["id"])
        return JSONResponse({"revoked": revoked, "active_sessions": count}, headers=_NO_STORE)

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
