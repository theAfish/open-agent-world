"""Offline hosted code upgrades that retain the existing profile and user data.

Run inside the worker's service identity and filesystem boundary, after the
operator has stopped ingress and saved a recoverable backup. Plugin migrations
use their ordinary startup lifecycle; this module never imports a new template.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import sqlite3

from backend.config import Settings
from backend.deploy import validate_manifest
from backend.deployments import PublishRequest, published_workspaces, release_spec
from backend.storage_location import LOCK, file_lock

from .configuration import write_private_json
from .worker import WorkerConfig, create_worker_services, hosted_scope, prepare_hosted_binding


def refresh_manifest(old: dict, new: dict) -> tuple[dict, list[str]]:
    """Refresh installed Pack contracts on the already published card surfaces."""
    if old["layout"] != new["layout"]:
        raise ValueError("Workspace layout changed; an explicit workspace migration is required")
    def panels(value):
        return [{key: item for key, item in panel.items() if key != "sections"}
                for panel in value["panels"]]
    if panels(old) != panels(new) or set(old["permissions"]) != set(new["permissions"]):
        raise ValueError("Published card scope changed")
    previous, current = old.get("plugin_access", {}), new.get("plugin_access", {})
    if set(previous) != set(current):
        raise ValueError("Published Pack card scope changed")
    for node, grants in old["permissions"].items():
        if node not in previous and grants != new["permissions"][node]:
            raise ValueError("Core public permissions changed")
    result = {**old, **{key: new[key] for key in (
        "panels", "permissions", "plugin_access", "plugin_versions", "runtime_nodes") if key in new}}
    if "workspaces" in old:
        current_workspaces = new.get("workspaces", [])
        if [workspace["legion_id"] for workspace in old["workspaces"]] != [
                workspace["legion_id"] for workspace in current_workspaces]:
            raise ValueError("Published workspace scope or order changed")
        result["workspaces"] = [refresh_manifest(previous, current)[0]
                                for previous, current in zip(old["workspaces"], current_workspaces)]
    changed = [node for node in previous if previous[node] != current[node]
               or old["permissions"][node] != new["permissions"][node]]
    return result, changed


async def migrate_profile(config: WorkerConfig, *, services_factory=None) -> dict:
    """Migrate a stopped, bound profile without changing its identity or storage."""
    root = config.data_root
    database = root / "database/world.sqlite3"
    if not database.is_file() or not (root / "hosted-profile.json").is_file():
        raise ValueError("Upgrade requires an existing bound hosted profile")
    with file_lock(root / LOCK):
        prepare_hosted_binding(root, profile_id=config.profile_id, user_id=config.user_id,
                               application_id=config.application_id)
        if config.mode == "runtime":
            from .template_updates import recover_template_manifest
            recover_template_manifest(root)
        manifest_path = root / "deployment.json"
        old = None
        if config.mode == "runtime":
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_manifest(old)
            if old.get("auth_mode") != "hosted":
                raise ValueError("Upgrade requires hosted deployment authentication")
        elif manifest_path.exists():
            raise ValueError("Builder workers cannot upgrade deployment profiles")
        # Startup normally recovers interrupted runs. An operator upgrade must
        # not silently change unfinished work while checking a new installation.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("SELECT 1 FROM runs WHERE status IN ('created','running','waiting') LIMIT 1").fetchone():
                raise ValueError("Finish or stop active runs before upgrading")
        settings = replace(Settings.for_data_root(root), agent_runtime=config.agent_runtime,
                           sandbox_runtime=None, marketplace_url=None, inherit_runtime_environment=False,
                           tenant_scope=hosted_scope(config.user_id, config.application_id, config.profile_id))
        services = (services_factory or create_worker_services)(settings)
        result, changed = None, []
        try:
            before = {card.id: card.status for card in services.world.list_cards()}
            await services.startup()
            failures = [failure for failure in services.plugins.load_failures
                        if failure["phase"] != "hosting_policy"]
            if failures:
                raise ValueError("Pack startup or migration failed; inspect the operator logs")
            if any(card.status == "error" and before.get(card.id) != "error"
                   for card in services.world.list_cards()):
                raise ValueError("Card startup or migration failed; inspect the operator logs")
            if old is not None:
                new = release_spec(services, PublishRequest(
                    legion_ids=[workspace["legion_id"] for workspace in published_workspaces(old)],
                    name=old["name"], allow_terminal=any("terminal" in grants for grants in old["permissions"].values())))
                result, changed = refresh_manifest(old, new)
                validate_manifest(result)
        finally:
            try:
                await services.shutdown()
            finally:
                services.close()
        if result is not None:
            write_private_json(manifest_path, result)
        return {"profile": config.profile_id, "ready": True, "changed_contracts": changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    value = json.loads(args.config.read_text(encoding="utf-8"))
    value["data_root"] = args.config.resolve().parent / value["data_root"]
    factory = None
    if name := os.environ.get("OAW_HOSTED_SERVICES_FACTORY"):
        module, attribute = name.split(":", 1)
        factory = getattr(importlib.import_module(module), attribute)
    print(json.dumps(asyncio.run(migrate_profile(WorkerConfig(**value), services_factory=factory))))


if __name__ == "__main__":
    main()
