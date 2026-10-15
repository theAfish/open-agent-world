"""Explicit cloud entry point: uvicorn backend.cloud.aliyun.app:create_app --factory.

One owner, process, data volume and remote Sandbox volume per deployment. This
composition is never selected by desktop configuration or OS auto-discovery.
"""
from dataclasses import replace
from importlib import import_module
import json
import os
from pathlib import Path
import sys

from starlette.responses import JSONResponse

from backend.config import Settings
from backend.hosting.identity import ProfileAccess, VerifiedIdentity
from backend.hosting.oidc import OidcSessionIdentity, OidcSettings
from backend.main import create_app as core_app
from backend.plugins.builtin import create_builtin_registry
from backend.request_context import TenantScope
from backend.services import create_services

from .sandbox import AliyunSandboxSettings, registry_factory


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


def approved_plugins():
    # Do not scan user-installed wheels or arbitrary installed entry points.
    registry = create_builtin_registry()
    plugins = Path(__file__).resolve().parents[3] / "plugins"
    for directory, module in (("structure_viewer", "oaw_structure_viewer"),
                               ("matcreator", "oaw_matcreator"), ("sqlite", "oaw_sqlite")):
        package_root = plugins / directory
        sys.path.insert(0, str(package_root / "src" if (package_root / "src").is_dir() else package_root))
        plugin = import_module(module).create_plugin()
        if directory == "matcreator":
            from .matcreator import hosted_matcreator
            plugin = hosted_matcreator(plugin)
        registry.install(plugin)
    return registry


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required cloud configuration: {name}")
    return value


def create_app():
    # Explicitly refuse the personal data-directory fallback.
    root = Path(require_env("OPEN_AGENT_WORLD_DATA_ROOT")).resolve()
    workspace_id = require_env("OAW_WORKSPACE_ID")
    if os.environ.get("OAW_ALIYUN_ALLOW_EPHEMERAL_PROFILE") != "1":
        marker = root / ".oaw-persistent-volume"
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != workspace_id:
            raise ValueError("Provision persistent profile storage and its workspace marker; see deploy/aliyun/README.md")
    oidc = OidcSettings(
        issuer=require_env("OAW_OIDC_ISSUER"), discovery_url=require_env("OAW_OIDC_DISCOVERY_URL"),
        client_id=require_env("OAW_OIDC_CLIENT_ID"), client_secret=require_env("OAW_OIDC_CLIENT_SECRET"),
        session_secret=require_env("OAW_SESSION_SECRET"), public_origin=require_env("OAW_PUBLIC_ORIGIN"),
        allow_local_http=os.environ.get("OAW_ALLOW_LOCAL_HTTP") == "1",
        scopes=tuple(os.environ.get("OAW_OIDC_SCOPES", "openid profile").split()),
        token_endpoint_auth_method=os.environ.get("OAW_OIDC_TOKEN_AUTH_METHOD", "client_secret_basic"),
    )
    identity = OidcSessionIdentity(oidc)
    tenant = TenantScope(require_env("OAW_ORGANIZATION_ID"), workspace_id)
    access = ProfileAccess(identity, VerifiedIdentity(oidc.issuer, require_env("OAW_OWNER_SUBJECT")),
                           tenant, oidc.public_origin, "/auth/login")
    remote = AliyunSandboxSettings(
        region=require_env("OAW_ALIYUN_REGION"), api_key=require_env("OAW_ALIYUN_SANDBOX_API_KEY"),
        template=require_env("OAW_ALIYUN_SANDBOX_TEMPLATE"), volume=require_env("OAW_ALIYUN_WORKSPACE_VOLUME"),
        profile_id=workspace_id, lifetime_seconds=int(os.environ.get("OAW_ALIYUN_SANDBOX_LIFETIME", "600")),
        metadata=json.loads(os.environ.get("OAW_ALIYUN_SANDBOX_METADATA", "{}")),
    )
    settings = replace(Settings.from_environment(), data_root=root, database_path=root / "database/world.sqlite3",
        storage_config_path=None, control_plane_token=None, tenant_scope=tenant,
        sandbox_runtime="aliyun.agent-sandbox", plugin_directories=(), application_mode="production")

    def services_factory(settings):
        return create_services(settings, plugins=approved_plugins(), sandbox_registry_factory=registry_factory(remote))

    frontend = Path(__file__).resolve().parents[3] / "frontend/dist"
    application = core_app(settings, profile_access=access, services_factory=services_factory,
                           frontend_directory=frontend if frontend.is_dir() else None)
    application.add_middleware(CloudSurfaceMiddleware)
    return identity.wrap(application)
