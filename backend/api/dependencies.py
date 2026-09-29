from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request

from backend.request_context import RequestContext, context_from_scope, bind_request_context
from backend.services import ApplicationServices


def get_request_context(request: Request) -> RequestContext:
    return context_from_scope(request.scope)


async def get_services(request: Request) -> AsyncIterator[ApplicationServices]:
    from backend.card_state import state_session
    # The state-session header selects card state, never caller identity or tenant.
    with bind_request_context(context_from_scope(request.scope)), state_session(
        request.headers.get("X-OAW-State-Session") or request.query_params.get("state_session")
    ):
        yield request.app.state.services
