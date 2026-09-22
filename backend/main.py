from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
import json
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import __version__
from backend.api import api_router
from backend.api.websocket import websocket_route
from backend.config import Settings
from backend.control_plane import ControlPlaneMiddleware
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
from backend.sandbox.models import SandboxNetworkError
from backend.application import router as application_router


def create_app(
    settings: Settings | None = None,
    *,
    services: ApplicationServices | None = None,
    development=None,
    frontend_directory=None,
) -> FastAPI:
    selected_settings = settings or Settings.from_environment()
    manifest_path = selected_settings.data_root / "deployment.json"
    deployment = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if deployment is not None:
        from backend.deploy import validate_manifest
        validate_manifest(deployment)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from backend.storage_location import prepare_storage
        with ExitStack() as stack:
            owned = services is None
            effective_settings = prepare_storage(selected_settings, stack) if owned else selected_settings
            if deployment is None and (effective_settings.data_root / "deployment.json").exists():
                raise RuntimeError("This storage pointer refers to a deployment. Start it with scripts/deploy.py --serve.")
            active_services = services or create_services(effective_settings)
            application.state.services = active_services
            try:
                await active_services.startup()
                if deployment is not None:
                    versions = {p.id: p.version for p in active_services.plugins.catalog().plugins}
                    if versions != deployment["plugin_versions"]:
                        raise RuntimeError("Deployment plugin versions changed. Rebuild and republish with matching plugins.")
                    active_services.world.structure_locked = True
                yield
            finally:
                try:
                    await active_services.shutdown()
                    if development is not None and development.pending and active_services.sandbox_backend:
                        # Normal shutdown logs individual Sandbox failures and continues.
                        # Reset must instead stop if any native cleanup is still failing.
                        for card in active_services.world.list_cards():
                            if card.type == "sandbox":
                                try:
                                    await active_services.sandbox_backend.terminate(card.id)
                                except SandboxNotFoundError:
                                    pass
                finally:
                    if owned:
                        active_services.close()
                application.state.clean_shutdown = True

    application = FastAPI(
        title="Open Agent World",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if deployment else "/docs",
        redoc_url=None if deployment else "/redoc",
        openapi_url=None if deployment else "/openapi.json",
    )
    if deployment is None:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )
    if deployment is None:
        application.add_middleware(ControlPlaneMiddleware, token=selected_settings.control_plane_token)
        application.include_router(api_router)
        application.include_router(application_router)
        from backend.deployments import router as deployments_router
        application.include_router(deployments_router)
        application.add_api_websocket_route("/ws/events", websocket_route)
        from backend.visual_observation import visual_websocket
        application.add_api_websocket_route("/ws/visual", visual_websocket)
    else:
        from backend.deployment_runtime import runtime_router
        application.include_router(runtime_router(deployment, secure_cookie=deployment.get("secure_cookie", False)))

        @application.middleware("http")
        async def runtime_headers(request: Request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
    application.state.clean_shutdown = False
    if development is not None and deployment is None:
        if selected_settings.application_mode != "development":
            raise ValueError("Development controls require development mode")
        development.install(application)

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
        if deployment:
            logging.getLogger(__name__).warning("Deployment operation failed: %s", exc)
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": "Operation unavailable. Refresh and retry; contact the operator if it persists." if deployment else exc.message}},
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
        if deployment:
            logging.getLogger(__name__).warning("Deployment agent failed: %s", exc)
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": "Agent unavailable. Contact the operator." if deployment else str(exc)}},
        )

    @application.exception_handler(SandboxError)
    async def handle_sandbox_error(
        request: Request, exc: SandboxError
    ) -> JSONResponse:
        del request
        status_code = 500
        code = "sandbox_error"
        from backend.sandbox.models import SandboxOperationError
        if deployment:
            logging.getLogger(__name__).warning("Deployment Sandbox failed: %s", exc)
            return JSONResponse(status_code=409, content={"error": {"code": "sandbox_unavailable", "message": "Workspace unavailable. Contact the operator."}})
        if isinstance(exc, SandboxOperationError):
            return JSONResponse(status_code=409 if exc.retryable else 503, content=exc.feedback())
        if isinstance(exc, SandboxNotFoundError):
            status_code, code = 404, "sandbox_not_found"
        elif isinstance(exc, SandboxStateError):
            status_code, code = 409, "sandbox_state_error"
        elif isinstance(exc, SandboxValidationError):
            status_code, code = 422, "sandbox_validation_error"
        elif isinstance(exc, SandboxNetworkError):
            status_code, code = 503, "network_setup_failed"
        elif isinstance(exc, SandboxSecurityError):
            status_code, code = 503, "sandbox_security_error"
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": str(exc)}},
        )

    if frontend_directory is not None:
        from starlette.staticfiles import StaticFiles
        application.mount("/", StaticFiles(directory=frontend_directory, html=True), name="frontend")
    return application


def get_services(application: FastAPI | None = None) -> ApplicationServices:
    """Return the app service container for integration code and diagnostics."""

    target = application or app
    if not hasattr(target.state, "services"):
        raise RuntimeError("application services are available after FastAPI startup")
    return target.state.services


app = create_app()
