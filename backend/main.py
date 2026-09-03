from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import __version__
from backend.api import api_router
from backend.api.websocket import websocket_route
from backend.config import Settings
from backend.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    RuntimeUnavailableError,
)
from backend.agents import (
    AgentConfigurationError,
    AgentDependencyError,
    AgentNotFoundError,
    AgentRuntimeError,
    AgentStateError,
)
from backend.sandbox import (
    SandboxError,
    SandboxNotFoundError,
    SandboxSecurityError,
    SandboxStateError,
    SandboxValidationError,
)
from backend.services import ApplicationServices, create_services


def create_app(
    settings: Settings | None = None,
    *,
    services: ApplicationServices | None = None,
) -> FastAPI:
    selected_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        active_services = services or create_services(selected_settings)
        application.state.services = active_services
        try:
            await active_services.startup()
            yield
        finally:
            await active_services.shutdown()
            if owned:
                active_services.close()

    application = FastAPI(
        title="Open Agent World",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router)
    application.add_api_websocket_route("/ws/events", websocket_route)

    @application.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        del request
        status_code = 422
        if isinstance(exc, NotFoundError):
            status_code = 404
        elif isinstance(exc, PermissionDeniedError):
            status_code = 403
        elif isinstance(exc, ConflictError):
            status_code = 409
        elif isinstance(exc, RuntimeUnavailableError):
            status_code = 503
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @application.exception_handler(AgentRuntimeError)
    async def handle_agent_runtime_error(
        request: Request, exc: AgentRuntimeError
    ) -> JSONResponse:
        del request
        status_code = 500
        code = "agent_runtime_error"
        if isinstance(exc, AgentNotFoundError):
            status_code, code = 404, "agent_not_found"
        elif isinstance(exc, AgentStateError):
            status_code, code = 409, "agent_state_error"
        elif isinstance(exc, AgentConfigurationError):
            status_code, code = 422, "agent_configuration_error"
        elif isinstance(exc, AgentDependencyError):
            status_code, code = 503, "agent_dependency_error"
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": str(exc)}},
        )

    @application.exception_handler(SandboxError)
    async def handle_sandbox_error(
        request: Request, exc: SandboxError
    ) -> JSONResponse:
        del request
        status_code = 500
        code = "sandbox_error"
        if isinstance(exc, SandboxNotFoundError):
            status_code, code = 404, "sandbox_not_found"
        elif isinstance(exc, SandboxStateError):
            status_code, code = 409, "sandbox_state_error"
        elif isinstance(exc, SandboxValidationError):
            status_code, code = 422, "sandbox_validation_error"
        elif isinstance(exc, SandboxSecurityError):
            status_code, code = 503, "sandbox_security_error"
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": str(exc)}},
        )

    return application


def get_services(application: FastAPI | None = None) -> ApplicationServices:
    """Return the app service container for integration code and diagnostics."""

    target = application or app
    if not hasattr(target.state, "services"):
        raise RuntimeError("application services are available after FastAPI startup")
    return target.state.services


app = create_app()
