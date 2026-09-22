"""Copy, verify, then switch default workspaces; retain source files as backup."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from backend.errors import ConflictError, ResourceValidationError
from backend.events import EventType
from backend.storage_location import _copy, _fingerprint, _reparse
from backend.world.models import CardBatchPatch, CardPatch
from .models import SandboxState
from .settings import SandboxSettingsStore


def _plain_path(path: Path) -> None:
    for part in (path, *path.parents):
        if _reparse(part):
            raise ResourceValidationError(f"Workspace migration cannot traverse links or junctions: {part}")


def _copy_workspace(source: Path, target: Path) -> None:
    _plain_path(source)
    _plain_path(target)
    if source == target:
        return
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise ResourceValidationError("Workspace migration requires separate source and destination folders")
    if source.exists() and not source.is_dir():
        raise ResourceValidationError(f"Workspace is not a directory: {source}")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        # A retry after interruption can reuse an already verified copy, never merge/overwrite it.
        if source.is_dir() and target.is_dir() and _fingerprint(source, exclude_metadata=False) == _fingerprint(target, exclude_metadata=False):
            return
        raise ConflictError(f"Workspace destination contains different files: {target}. Choose an empty location.")
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return
    before = _fingerprint(source, exclude_metadata=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(f".oaw-workspace-{uuid4().hex}")
    stage.mkdir()
    # On failure retain the staging copy for recovery. Never recursively delete user data.
    _copy(source, stage, exclude_metadata=False)
    if before != _fingerprint(stage, exclude_metadata=False) or before != _fingerprint(source, exclude_metadata=False):
        raise ConflictError("Workspace changed during migration; original location was retained. Retry after stopping file writers.")
    _plain_path(target)
    if target.exists():
        target.rmdir()  # Only an empty destination may be replaced.
    stage.rename(target)


async def save_settings(services, request):
    async with services._node_mutation():
        store = SandboxSettingsStore(services.database, services.settings.data_root)
        root = store.validator.validate_workspace(request.workspace_root)
        request = request.model_copy(update={"workspace_root": root})
        old = store.read()
        # Changing environment defaults must not relocate custom workspaces or
        # require stopping running Sandboxes when the location is unchanged.
        if old.workspace_root == root and "environment_variables" in request.model_fields_set:
            return store.save(request)
        if old.workspace_root is None and root is None:
            return store.save(request)
        old_root = Path(old.workspace_root) if old.workspace_root else services.settings.data_root
        new_root = Path(root) if root else services.settings.data_root
        if old_root != new_root and old.workspace_root and root and (old_root.is_relative_to(new_root) or new_root.is_relative_to(old_root)):
            raise ResourceValidationError("Choose a workspace location outside the current default folder")
        nodes = services.world.list_cards()
        sandboxes = [node for node in nodes if node.type == "sandbox" and not (
            root and node.config.get("workspace_path") and Path(node.config["workspace_path"]).parent == new_root)]
        codex_source, codex_target = old_root / "codex-workspace", new_root / "codex-workspace"
        migrate_codex = codex_source != codex_target and (codex_source.exists() or codex_source.is_symlink())
        if not sandboxes and not migrate_codex and old.workspace_root == root:
            return store.save(request)
        agents = [node.id for node in nodes if services.plugins.has_trait(node.type, "core.agent")]
        maintenance = services.run_manager.workspace_maintenance(agents) if services.run_manager else nullcontext()
        async with maintenance:
            backend = services.sandbox_backend
            copies = []
            updates = []
            transactions = []
            backup_paths = []
            for node in sandboxes:
                services.resources.artifacts.assert_source_idle(node.id)
                info = await backend.get(node.id)
                if info.state != SandboxState.STOPPED or node.id in services._sandbox_stopping:
                    raise ConflictError(f"Stop Sandbox {node.name} before migrating workspace locations")
                source = Path(node.config["workspace_path"]) if node.config.get("workspace_path") else await backend.managed_workspace(node.id)
                # Missing explicit directories are errors, not silently empty projects.
                if (node.config.get("workspace_path") or info.runtime_locked) and not source.is_dir():
                    raise ResourceValidationError(f"Workspace source is missing: {source}")
                if root is None and node.config.get("workspace_access") == "read_only":
                    raise ResourceValidationError("Read-only workspaces require an external location; choose a folder instead of clearing the default")
                target = new_root / node.id if root else await backend.prepare_managed_workspace(node.id)
                config = {**node.config, "workspace_path": str(target) if root else None,
                          "workspace_access": node.config.get("workspace_access", "read_write") if root else "read_write"}
                copies.append((source, target))
                updates.append(CardBatchPatch(node_id=node.id, patch=CardPatch(config=config)))
            if migrate_codex:
                copies.append((codex_source, codex_target))
            try:
                for source, target in copies:
                    if source != target and source.is_dir():
                        backup_paths.append(str(source))
                    await asyncio.to_thread(_copy_workspace, source, target)
                context = services._node_lifecycle_context()
                for item in updates:
                    current = services.world.get_card(item.node_id)
                    updated = services.world.preview_update_card(item.node_id, item.patch)
                    lifecycle = services.plugins.node_type(current.type).lifecycle
                    transaction = await lifecycle.prepare_update(context, current, updated, item.patch)
                    transactions.append(transaction)
                    await transaction.commit()
                # No awaits inside this shared-connection transaction: settings and cards switch together.
                with services.database.transaction(immediate=True):
                    cards = services.world.update_cards(updates) if updates else []
                    saved = store.save(request)
                    if copies:
                        # List exact old workspaces, never their parent runtime/data directories.
                        # A directory reused as a destination must not be offered for cleanup.
                        targets = [target for _, target in copies]
                        store.record_backups([path for path in backup_paths if not any(
                            Path(path).is_relative_to(target) or target.is_relative_to(Path(path)) for target in targets)])
            except BaseException as error:
                for transaction in reversed(transactions):
                    rollback_error = await services._rollback_lifecycle(transaction, error)
                    if rollback_error is not None:
                        error.add_note(f"Workspace binding rollback failed: {rollback_error}")
                if isinstance(error, OSError):
                    raise ResourceValidationError(f"Could not migrate workspaces; original files retained: {error}") from error
                raise
            for card in cards:
                await services.events.publish(EventType.CARD_UPDATED, node_id=card.id,
                    payload={"node": services.enrich_card(card).model_dump(mode="json")})
                await services.events.publish(EventType.SANDBOX_STATE_CHANGED, node_id=card.id,
                    payload={"state": "stopped"})
            return saved
