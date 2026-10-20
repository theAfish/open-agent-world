"""Alibaba Sandbox composition for the provider-neutral hosted worker.

The ECS gateway authenticates users; every worker retains its own profile and
database. Only commands run in Alibaba Sandbox. Desktop entry points are not
changed by this opt-in services factory.
"""
from dataclasses import replace
import json
import os

from backend.hosting.policy import unsafe_host_configuration
from backend.services import create_services

from .app import approved_plugins
from .identity import require_env
from .sandbox import AliyunSandboxSettings, registry_factory


def create_hosted_services(settings):
    tenant = settings.tenant_scope
    profile_id = require_env("OAW_ALIYUN_PROFILE_ID")
    if tenant is None or not tenant.world_id or tenant.world_id != profile_id:
        raise ValueError("Alibaba Sandbox configuration must belong to this hosted profile")
    store = os.environ.get("OAW_ALIYUN_WORKSPACE_STORE", "volume")
    volume = require_env("OAW_ALIYUN_WORKSPACE_VOLUME") if store == "volume" else ""
    if store == "volume" and volume != f"oaw-{profile_id}":
        raise ValueError("Each hosted profile requires its own oaw-<profile UUID> Volume")
    remote = AliyunSandboxSettings(
        region=require_env("OAW_ALIYUN_REGION"),
        api_key=require_env("OAW_ALIYUN_SANDBOX_API_KEY"),
        template=require_env("OAW_ALIYUN_SANDBOX_TEMPLATE"),
        volume=volume, profile_id=profile_id,
        workspace_store=store,
        lifetime_seconds=int(os.environ.get("OAW_ALIYUN_SANDBOX_LIFETIME", "600")),
        metadata=json.loads(os.environ.get("OAW_ALIYUN_SANDBOX_METADATA", "{}")),
        unavailable_reason=None if os.environ.get("OAW_ALIYUN_SANDBOX_READY") == "1" else (
            "Cloud workspace storage has not passed persistence verification; contact the deployment operator."
        ),
    )
    settings = replace(settings, sandbox_runtime="aliyun.agent-sandbox",
                       plugin_directories=(), storage_config_path=None)
    services = create_services(settings, plugins=approved_plugins(),
                               sandbox_registry_factory=registry_factory(remote))
    if any(unsafe_host_configuration({"type": card.type, "config": card.config})
           for card in services.world.list_cards()):
        services.close()
        raise ValueError("Hosted profiles cannot contain host filesystem bindings or local agents")
    return services
