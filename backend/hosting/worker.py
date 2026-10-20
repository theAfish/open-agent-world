"""One profile per worker, authenticated only by its trusted hosting gateway.

Workers must listen on a private network. A browser cookie, loopback socket or
caller-supplied user header never grants access to this application.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field, replace
import hmac
import json
from pathlib import Path
import re
import time
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

from backend.config import Settings
from backend.hosting.identity import ProfileAccess, VerifiedIdentity
from backend.request_context import TenantScope

ISSUER = "urn:oaw:hosted"
_TOKEN_DOMAIN = b"oaw-hosted-worker-v1\x00"


def _uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Hosted identifiers must be canonical UUIDs")
    return value


def _key(secret: str) -> bytes:
    if not isinstance(secret, str) or not re.fullmatch(r"[0-9a-fA-F]{64,128}", secret):
        raise ValueError("Worker secret must contain 32 to 64 random bytes encoded as hex")
    return bytes.fromhex(secret)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def mint_worker_token(secret: str, profile_id: str, user_id: str,
                      display_name: str | None = None, ttl: int = 30) -> str:
    """Issue a short-lived assertion for one exact worker and its stable owner."""
    key = _key(secret)
    _uuid(profile_id)
    _uuid(user_id)
    if type(ttl) is not int or not 1 <= ttl <= 60:
        raise ValueError("Worker token lifetime must be between 1 and 60 seconds")
    if display_name is not None and (not isinstance(display_name, str) or len(display_name) > 256):
        raise ValueError("Invalid display name")
    now = int(time.time())
    payload = {"v": 1, "profile_id": profile_id, "user_id": user_id,
               "display_name": display_name, "iat": now, "exp": now + ttl}
    body = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = _encode(hmac.digest(key, _TOKEN_DOMAIN + body.encode(), "sha256"))
    return body + "." + signature


class WorkerIdentityProvider:
    def __init__(self, secret: str, profile_id: str, user_id: str):
        self.key, self.profile_id, self.user_id = _key(secret), _uuid(profile_id), _uuid(user_id)

    async def authenticate(self, scope):
        headers = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        if len(headers) != 1 or not headers[0].startswith(b"Bearer ") or len(headers[0]) > 4096:
            return None
        try:
            body, signature = headers[0][7:].decode("ascii").split(".")
            expected = _encode(hmac.digest(self.key, _TOKEN_DOMAIN + body.encode("ascii"), "sha256"))
            if not hmac.compare_digest(signature, expected):
                return None
            raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"v", "profile_id", "user_id", "display_name", "iat", "exp"}:
                return None
            now = time.time()
            if (type(value["v"]) is not int or value["v"] != 1
                    or value["profile_id"] != self.profile_id or value["user_id"] != self.user_id
                    or type(value["iat"]) is not int or type(value["exp"]) is not int
                    or not 1 <= value["exp"] - value["iat"] <= 60
                    or value["iat"] > now + 5 or value["exp"] <= now):
                return None
            name = value["display_name"]
            if name is not None and (not isinstance(name, str) or len(name) > 256):
                return None
            return VerifiedIdentity(ISSUER, self.user_id, name)
        except (ValueError, TypeError, UnicodeError):
            return None


def hosted_scope(user_id: str, application_id: str, profile_id: str) -> TenantScope:
    _uuid(user_id)
    _uuid(profile_id)
    if not isinstance(application_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", application_id):
        raise ValueError("Invalid application ID")
    return TenantScope(user_id, application_id, profile_id)


def prepare_hosted_binding(root: Path, *, profile_id: str, user_id: str, application_id: str) -> None:
    """Bind a new directory before importing a separately sanitized template.

    Existing unbound databases are never assigned implicitly. Provisioning and
    serving invoke this while holding their respective profile process lock.
    """
    root = Path(root)
    tenant = hosted_scope(user_id, application_id, profile_id)
    expected = {"version": 2, "user_id": user_id, "profile_id": profile_id,
                "application_id": application_id, "tenant": asdict(tenant)}
    path = root / "hosted-profile.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != expected:
            raise ValueError("Hosted profile owner or scope differs from its stored binding")
        return
    if (root / "database/world.sqlite3").exists():
        raise ValueError("Use a new profile or explicitly migrate the existing profile offline")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation prevents accidentally replacing another owner's claim.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(expected, sort_keys=True))
    path.chmod(0o600)


@dataclass(frozen=True)
class HostedProfileAccess(ProfileAccess):
    profile_id: str = ""
    user_id: str = ""
    application_id: str = ""

    def bind_storage(self, root: Path) -> None:
        prepare_hosted_binding(root, profile_id=self.profile_id, user_id=self.user_id,
                               application_id=self.application_id)


@dataclass(frozen=True)
class WorkerConfig:
    profile_id: str
    user_id: str
    application_id: str
    mode: str
    public_origin: str
    secret: str = field(repr=False)
    data_root: Path
    agent_runtime: str | None = "google.adk"
    sandbox_runtime: str | None = None

    def __post_init__(self):
        hosted_scope(self.user_id, self.application_id, self.profile_id)
        _key(self.secret)
        if self.mode not in {"builder", "runtime"}:
            raise ValueError("Worker mode must be builder or runtime")
        origin = urlsplit(self.public_origin)
        if origin.scheme != "https" and not (origin.scheme == "http" and origin.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Hosted workers require HTTPS, except on loopback development origins")
        if self.sandbox_runtime is not None:
            raise ValueError("Provide a remote Sandbox through services_factory; native host runtimes are disabled")
        if self.agent_runtime == "openai.codex":
            raise ValueError("Local Codex is not available in hosted workers")
        object.__setattr__(self, "data_root", Path(self.data_root).resolve())


def create_worker_services(settings):
    """Load only operator-installed code from the shared application installation.

    Discovery excludes profile-installed wheels and ignores user enrollment
    metadata. PluginContext still points to this worker's own data directory.
    Pack management is also denied at HTTP ingress.
    """
    from backend.plugins.loader import load_plugin_registry
    from backend.services import create_services
    from backend.hosting.policy import unsafe_host_configuration
    registry = load_plugin_registry(data_root=settings.data_root, plugin_directories=(),
                                    load_installed=False, load_unenrolled_sources=True)
    services = create_services(settings, plugins=registry)
    if any(plugin.id == "openai.codex" for plugin in registry.plugins()):
        registry.set_enabled("openai.codex", False)
    # A private source profile may contain a local-only card. Fail closed rather
    # than restore its lifecycle using credentials available in the service.
    if any(unsafe_host_configuration({"type": card.type, "config": card.config})
           for card in services.world.list_cards()):
        services.close()
        raise ValueError("Hosted profiles cannot contain local Codex agents or host filesystem bindings")
    return services


def create_worker_app(config: WorkerConfig | Mapping, *, frontend_directory=None, services_factory=None):
    from backend.main import create_app
    from backend.hosting.policy import HostedPolicyMiddleware
    config = config if isinstance(config, WorkerConfig) else WorkerConfig(**config)
    manifest_path = config.data_root / "deployment.json"
    if config.mode == "runtime":
        if not manifest_path.is_file():
            raise ValueError("Runtime workers require a hosted deployment manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("auth_mode") != "hosted":
            raise ValueError("Runtime workers require hosted deployment authentication")
    elif manifest_path.exists():
        raise ValueError("Builder workers cannot serve deployment profiles")
    tenant = hosted_scope(config.user_id, config.application_id, config.profile_id)
    settings = replace(Settings.for_data_root(config.data_root), tenant_scope=tenant,
                       agent_runtime=config.agent_runtime, sandbox_runtime=None, marketplace_url=None)
    access = HostedProfileAccess(
        WorkerIdentityProvider(config.secret, config.profile_id, config.user_id),
        VerifiedIdentity(ISSUER, config.user_id), tenant, config.public_origin,
        profile_id=config.profile_id, user_id=config.user_id, application_id=config.application_id,
    )
    app = create_app(settings, profile_access=access, frontend_directory=frontend_directory,
                     services_factory=services_factory or create_worker_services)
    app.add_middleware(HostedPolicyMiddleware)
    return app
