"""Explicit cloud entry point: uvicorn backend.cloud.aliyun.app:create_app --factory.

One owner, process, data volume and remote Sandbox volume per deployment. This
composition is never selected by desktop configuration or OS auto-discovery.
"""
from dataclasses import replace
import json
import os
from pathlib import Path

from starlette.responses import JSONResponse

from backend.config import Settings
from backend.hosting.identity import ProfileAccess, VerifiedIdentity
from backend.hosting.oidc import OidcSessionIdentity
from backend.main import create_app as core_app
from backend.plugins.loader import load_plugin_registry
from backend.request_context import TenantScope
from backend.services import create_services

from .sandbox import AliyunSandboxSettings, registry_factory
from .identity import oidc_settings, require_env


class CloudSurfaceMiddleware:
    """Keep deployment administration out of the end-user profile API."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path, method = scope["path"], scope["method"]
            blocked = path.startswith(("/api/desktop/", "/api/deployments", "/api/settings/storage", "/api/development"))
            blocked |= method not in {"GET", "HEAD"} and path.startswith(("/api/packs", "/api/store/"))
            if blocked:
                return await JSONResponse({"error": {"code": "host_administration_disabled",
                    "message": "This operation is managed by the deployment operator"}}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


def approved_plugins(data_root: Path | None = None):
    """Use the normal Pack lifecycle for operator-installed source code only."""
    from pydantic import Field
    from backend.world.models import SandboxConfig

    class CloudSandboxConfig(SandboxConfig):
        # Apply the provider default to every Sandbox, regardless of its Pack.
        command_timeout: float = Field(default=60, gt=0, le=36000)

    registry = load_plugin_registry(data_root=data_root, load_installed=False,
        load_entry_points=False, load_unenrolled_sources=True,
        sandbox_config_model=CloudSandboxConfig)
    if registry.has_plugin("openai.codex"):
        registry.mark_unavailable("openai.codex", "hosting_policy",
            ValueError("Local Codex execution is not available in hosted workspaces"), source="hosting_policy")
    return registry


def create_app():
    # Explicitly refuse the personal data-directory fallback.
    root = Path(require_env("OPEN_AGENT_WORLD_DATA_ROOT")).resolve()
    workspace_id = require_env("OAW_WORKSPACE_ID")
    if os.environ.get("OAW_ALIYUN_ALLOW_EPHEMERAL_PROFILE") != "1":
        marker = root / ".oaw-persistent-volume"
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != workspace_id:
            raise ValueError("Provision persistent profile storage and its workspace marker; see deploy/aliyun/README.md")
    oidc = oidc_settings()
    identity = OidcSessionIdentity(oidc)
    tenant = TenantScope(require_env("OAW_ORGANIZATION_ID"), workspace_id)
    access = ProfileAccess(identity, VerifiedIdentity(oidc.issuer, require_env("OAW_OWNER_SUBJECT")),
                           tenant, oidc.public_origin, "/auth/login")
    remote = AliyunSandboxSettings(
        region=require_env("OAW_ALIYUN_REGION"), api_key=require_env("OAW_ALIYUN_SANDBOX_API_KEY"),
        template=require_env("OAW_ALIYUN_SANDBOX_TEMPLATE"), volume=require_env("OAW_ALIYUN_WORKSPACE_VOLUME"),
        profile_id=workspace_id, lifetime_seconds=int(os.environ.get("OAW_ALIYUN_SANDBOX_LIFETIME", "600")),
        metadata=json.loads(os.environ.get("OAW_ALIYUN_SANDBOX_METADATA", "{}")),
        python_requirements=tuple(json.loads(os.environ.get("OAW_ALIYUN_SANDBOX_PYTHON_REQUIREMENTS", "[]"))),
    )
    settings = replace(Settings.from_environment(), data_root=root, database_path=root / "database/world.sqlite3",
        storage_config_path=None, control_plane_token=None, tenant_scope=tenant,
        sandbox_runtime="aliyun.agent-sandbox", plugin_directories=(), application_mode="production",
        inherit_runtime_environment=False)

    def services_factory(settings):
        return create_services(settings, plugins=approved_plugins(settings.data_root),
                                   sandbox_registry_factory=registry_factory(remote))

    frontend = Path(__file__).resolve().parents[3] / "frontend/dist"
    application = core_app(settings, profile_access=access, services_factory=services_factory,
                           frontend_directory=frontend if frontend.is_dir() else None)
    application.add_middleware(CloudSurfaceMiddleware)
    return identity.wrap(application)
