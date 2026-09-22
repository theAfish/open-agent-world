"""A separate, deliberately small operator API. Management routers are never mounted."""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services

COOKIE = "oaw_operator"


def password_record(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    return {"salt": salt, "hash": hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()}


def runtime_router(manifest: dict, *, secure_cookie: bool = False):
    router = APIRouter()
    sessions: dict[str, float] = {}
    attempts = defaultdict(deque)

    def same_origin(request: Request):
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
            raise HTTPException(403, "Cross-origin changes are not allowed")

    def operator(request: Request):
        token = request.cookies.get(COOKIE, "")
        if sessions.get(token, 0) <= time.monotonic():
            sessions.pop(token, None)
            raise HTTPException(401, "Sign in to this application")
        if request.method not in {"GET", "HEAD"}:
            same_origin(request)

    @router.get("/api/deployment")
    def info(request: Request):
        return {"mode": "runtime", "name": manifest["name"], "release_id": manifest["id"],
                "authenticated": sessions.get(request.cookies.get(COOKIE, ""), 0) > time.monotonic()}

    class Login(BaseModel):
        model_config = ConfigDict(extra="forbid")
        password: str = Field(min_length=1, max_length=1024)

    @router.post("/api/deployment/session")
    async def login(body: Login, request: Request, response: Response):
        same_origin(request)
        # Socket peer is intentional: a reverse proxy shares one conservative rate limit.
        peer = request.client.host if request.client else "unknown"
        now = time.monotonic()
        if peer not in attempts and len(attempts) >= 4096:
            for address, failures in list(attempts.items()):
                if not failures or failures[-1] < now - 60:
                    del attempts[address]
            if len(attempts) >= 4096:
                raise HTTPException(429, "Too many attempts. Try again in one minute.")
        queue = attempts[peer]
        while queue and queue[0] < now - 60:
            queue.popleft()
        if len(queue) >= 10:
            raise HTTPException(429, "Too many attempts. Try again in one minute.")
        queue.append(now)
        import asyncio
        record = await asyncio.to_thread(password_record, body.password, manifest["password"]["salt"])
        if not hmac.compare_digest(record["hash"], manifest["password"]["hash"]):
            raise HTTPException(401, "Incorrect access password")
        queue.clear()
        for token, expiry in list(sessions.items()):
            if expiry <= now:
                del sessions[token]
        if len(sessions) >= 1024:
            raise HTTPException(503, "Too many active sessions")
        token = secrets.token_urlsafe(32)
        sessions[token] = now + 12 * 3600
        response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie, samesite="strict", max_age=12 * 3600, path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True}

    protected = APIRouter(prefix="/api/runtime-app", dependencies=[Depends(operator)])

    @protected.delete("/session")
    def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get(COOKIE, ""), None)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @protected.get("")
    def bootstrap(services=Depends(get_services)):
        from backend.deployment_workspace import workspace_snapshot
        return {**{key: manifest[key] for key in ("id", "name", "created_at", "layout", "panels", "permissions")},
                **workspace_snapshot(manifest, services)}

    from backend.deployment_workspace import workspace_router
    protected.include_router(workspace_router(manifest))
    router.include_router(protected)
    return router
