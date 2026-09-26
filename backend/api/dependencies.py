from __future__ import annotations

from fastapi import Request

from backend.request_context import RequestContext, context_from_scope, request_context_scope
from backend.services import ApplicationServices


async def get_request_context(request: Request) -> RequestContext:
    return context_from_scope(request.scope)


async def get_services(request: Request):
    from backend.card_state import state_session
    # The state-session header selects card state, never caller identity or tenant.
    with request_context_scope(context_from_scope(request.scope)), state_session(
        request.headers.get("X-OAW-State-Session") or request.query_params.get("state_session")
    ):
        yield request.app.state.services
