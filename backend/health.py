"""Minimal probes. Optional providers are not prerequisites for serving the API."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter(tags=["system"])


@router.get("/health/live")
def liveness() -> JSONResponse:
    return JSONResponse({"status": "alive"}, headers={"Cache-Control": "no-store"})


@router.get("/health/ready")
def readiness(request: Request) -> JSONResponse:
    services = getattr(request.app.state, "services", None)
    ready = bool(getattr(request.app.state, "ready", False) and services is not None
                 and services.database.is_ready())
    # Do not expose paths, SQL errors, installed plugins or credentials.
    return JSONResponse(
        {"status": "ready" if ready else "not_ready"},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )
