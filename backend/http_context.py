"""Request correlation is diagnostic metadata, never authentication."""

from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger(__name__)
_REQUEST_ID = re.compile(rb"[A-Za-z0-9._-]{1,128}")


def request_id(scope: Scope) -> str:
    return scope.setdefault("state", {}).setdefault("request_id", uuid4().hex)


def error_response(
    request: Request, *, status_code: int, code: str, message: str,
    retryable: bool = False, extra: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    correlation = request_id(request.scope)
    return JSONResponse(
        status_code=status_code,
        content={**(extra or {}), "error": {
            "code": code, "message": message,
            "request_id": correlation, "retryable": retryable,
        }},
        headers={**(headers or {}), "X-Request-ID": correlation},
    )


class RequestIdMiddleware:
    """Pure ASGI wrapper so streaming bodies and context variables stay intact."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        values = [value for name, value in scope.get("headers", [])
                  if name.lower() == b"x-request-id"]
        correlation = (
            values[0].decode("ascii")
            if len(values) == 1 and _REQUEST_ID.fullmatch(values[0])
            else uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = correlation
        started = time.monotonic()
        status = 500

        async def correlated_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(name, value) for name, value in message.get("headers", [])
                           if name.lower() != b"x-request-id"]
                message = {**message, "headers": [
                    *headers, (b"x-request-id", correlation.encode("ascii")),
                ]}
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        finally:
            if scope["type"] == "http":
                # No URLs, query strings, bodies, or credentials in access logs.
                context = scope["state"].get("request_context")
                logger.info(
                    "HTTP request request_id=%s method=%s status=%s elapsed_ms=%.1f",
                    correlation, scope.get("method"), status,
                    (time.monotonic() - started) * 1000,
                    extra={"request_id": correlation,
                           "actor_id": context.actor.id if context else None,
                           "organization_id": context.tenant.organization_id if context else None},
                )
