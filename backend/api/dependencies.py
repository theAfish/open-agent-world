from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request

from backend.request_context import RequestContext
from backend.services import ApplicationServices


def get_request_context(request: Request) -> RequestContext:
    context = getattr(request.state, "request_context", None)
    if not isinstance(context, RequestContext):
        raise RuntimeError("RequestContextMiddleware is not installed")
    return context


async def get_services(request: Request) -> AsyncIterator[ApplicationServices]:
    from backend.card_state import state_session
    with state_session(request.headers.get("X-OAW-State-Session") or request.query_params.get("state_session")):
        yield request.app.state.services
