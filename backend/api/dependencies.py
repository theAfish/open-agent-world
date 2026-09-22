from __future__ import annotations

from fastapi import Request

from backend.services import ApplicationServices


async def get_services(request: Request):
    from backend.card_state import state_session
    with state_session(request.headers.get("X-OAW-State-Session") or request.query_params.get("state_session")):
        yield request.app.state.services
