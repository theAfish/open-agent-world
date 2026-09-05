from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.sandbox.base import SandboxBackend
from backend.sandbox.manager import SandboxManager
from backend.sandbox.models import (
    CommandResult, ResourceAccess, ResourceAttachment, SandboxInfo,
    SandboxNotFoundError, SandboxSecurityError, SandboxState, SandboxStateError,
    SandboxValidationError,
)
from backend.sandbox.registry import SandboxRuntime, SandboxRuntimeRegistration, SandboxRuntimeRegistry
from backend.services import create_services
from backend.world.models import CardCreate, CardPatch


class RecordingBackend(SandboxBackend):
    def __init__(self, root):
        self.root = root
        self.records = {}
        self.created = []
        self.commands = []
        self.fail_configure = False

    async def create(self, sandbox_id):
        self.created.append(sandbox_id)
        self.records[sandbox_id] = SandboxInfo(
            sandbox_id, SandboxState.STOPPED, self.root / sandbox_id,
            platform="linux", runtime_id="test-linux", shell=("/bin/sh", "-c"),
        )
        return self.records[sandbox_id]

    async def get(self, sandbox_id):
        if sandbox_id not in self.records:
            raise SandboxNotFoundError(sandbox_id)
        return self.records[sandbox_id]

    async def configure(self, sandbox_id, *, workspace_path, workspace_access):
        info = await self.get(sandbox_id)
        if info.state != SandboxState.STOPPED:
            raise SandboxStateError("Stop before configuring")
        if self.fail_configure:
            raise SandboxValidationError("Cannot grant this folder")
        self.records[sandbox_id] = replace(info, workspace_path=workspace_path,
                                          workspace_access=workspace_access,
                                          workspace=Path(workspace_path) if workspace_path else self.root / sandbox_id)
        return self.records[sandbox_id]

    async def start(self, sandbox_id):
        self.records[sandbox_id] = replace(await self.get(sandbox_id), state=SandboxState.READY)
        return self.records[sandbox_id]

    async def terminate(self, sandbox_id):
        self.records[sandbox_id] = replace(await self.get(sandbox_id), state=SandboxState.STOPPED)

    async def execute(self, sandbox_id, argv, *, timeout_seconds=None, env=None):
        assert (await self.get(sandbox_id)).state == SandboxState.READY
        self.commands.append(argv)
        return CommandResult(sandbox_id, tuple(argv), 0, "ok", "", 0.01)

    async def attach_resource(self, sandbox_id, resource_id, source, relative_path, access):
        return ResourceAttachment(sandbox_id, resource_id, source, relative_path, access)

    async def detach_resource(self, sandbox_id, resource_id):
        pass

    async def destroy(self, sandbox_id):
        self.records.pop(sandbox_id, None)


def make_manager(tmp_path, *, available=True):
    registry = SandboxRuntimeRegistry()
    backend = RecordingBackend(tmp_path / "runtime")

    async def probe():
        return available, None if available else "Missing isolation support"

    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("test-linux", "Test Linux", "linux", ("/bin/sh", "-c")),
        lambda: backend, probe, 10,
    ))
    return SandboxManager(tmp_path / "managed", registry), backend


@pytest.mark.asyncio
async def test_draft_does_not_provision_and_unavailable_never_executes(tmp_path):
    manager, backend = make_manager(tmp_path, available=False)
    await manager.create("draft")
    assert backend.created == []
    info = await manager.get("draft")
    assert not info.available and not info.runtime_locked
    assert "Missing isolation" in info.unavailable_reason
    await manager.terminate("draft")
    with pytest.raises(SandboxSecurityError, match="Missing isolation"):
        await manager.start("draft")
    assert backend.created == []
    await manager.destroy("draft")


@pytest.mark.asyncio
async def test_pinned_runtime_survives_restart_and_external_data_survives_delete(tmp_path):
    manager, backend = make_manager(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "work.txt").write_text("user work")
    await manager.create("lab")
    await manager.configure_options("lab", {"workspace_path": str(folder)})
    assert backend.created == []
    started = await manager.start("lab")
    assert started.runtime_id == "test-linux" and started.runtime_locked
    await manager.terminate("lab")
    restored = SandboxManager(manager.root, manager.registry)
    assert (await restored.get("lab")).workspace == folder
    with pytest.raises(SandboxStateError, match="fixed"):
        await restored.configure_options("lab", {"runtime": "another", "workspace_path": str(folder)})
    await restored.destroy("lab")
    assert (folder / "work.txt").read_text() == "user work"


@pytest.mark.asyncio
async def test_workspace_rebind_requires_stopped_and_failure_keeps_previous_binding(tmp_path):
    manager, backend = make_manager(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    await manager.create("lab")
    await manager.configure_options("lab", {"workspace_path": str(first)})
    await manager.start("lab")
    with pytest.raises(SandboxStateError, match="Stop"):
        await manager.configure_options("lab", {"workspace_path": str(second)})
    await manager.terminate("lab")
    backend.fail_configure = True
    with pytest.raises(SandboxValidationError):
        await manager.configure_options("lab", {"workspace_path": str(second)})
    assert (await manager.get("lab")).workspace_path == str(first)
    assert (await SandboxManager(manager.root, manager.registry).get("lab")).workspace_path == str(first)


@pytest.mark.asyncio
async def test_failed_initial_configuration_cleans_only_new_runtime(tmp_path):
    manager, backend = make_manager(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    await manager.create("lab")
    await manager.configure_options("lab", {"workspace_path": str(folder)})
    backend.fail_configure = True
    with pytest.raises(SandboxValidationError):
        await manager.start("lab")
    assert backend.records == {}
    assert not (await manager.get("lab")).runtime_locked
    assert folder.is_dir()


@pytest.mark.asyncio
async def test_restart_reconciles_interrupted_native_workspace_change(tmp_path):
    manager, backend = make_manager(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    await manager.create("lab")
    config = {"workspace_path": str(first)}
    await manager.configure_options("lab", config)
    await manager.start("lab")
    await manager.terminate("lab")
    # Crash after native manifest write but before the binding/world update.
    await backend.configure("lab", workspace_path=str(second), workspace_access=ResourceAccess.READ_WRITE)
    restored = SandboxManager(manager.root, manager.registry)
    await restored.configure_options("lab", config)
    assert (await restored.get("lab")).workspace == first


@pytest.mark.asyncio
async def test_draft_attachment_paths_are_normalized_and_conflicts_rejected(tmp_path):
    manager, _ = make_manager(tmp_path)
    await manager.create("lab")
    first, second = manager.root / "first.txt", manager.root / "second.txt"
    first.write_text("a")
    second.write_text("b")
    await manager.attach_resource("lab", "first", first, "inputs\\notes.txt", ResourceAccess.READ_ONLY)
    with pytest.raises(SandboxValidationError, match="unique"):
        await manager.attach_resource("lab", "second", second, "inputs/notes.txt", ResourceAccess.READ_ONLY)
    with pytest.raises(SandboxValidationError, match="safe relative"):
        await manager.attach_resource("lab", "second", second, "../notes.txt", ResourceAccess.READ_ONLY)


@pytest.mark.asyncio
async def test_failed_world_save_restores_native_binding(tmp_path, monkeypatch):
    manager, backend = make_manager(tmp_path)
    services = create_services(Settings.for_data_root(manager.root), sandbox_backend=manager)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    try:
        card = await services.create_card(CardCreate(type="sandbox", config={"workspace_path": str(first)}))
        await services.start_sandbox(card.id)
        await services.stop_sandbox(card.id)

        def failed_save(*args, **kwargs):
            raise OSError("database write failed")

        monkeypatch.setattr(services.world, "update_card", failed_save)
        with pytest.raises(OSError, match="database write failed"):
            await services.update_card(card.id, CardPatch(config={"workspace_path": str(second)}))
        assert (await backend.get(card.id)).workspace == first
        assert services.world.get_card(card.id).config["workspace_path"] == str(first)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_repeated_status_reads_do_not_reprobe_runtime_until_explicit_refresh(tmp_path):
    manager, _ = make_manager(tmp_path)
    calls = []
    registry = SandboxRuntimeRegistry()

    async def probe():
        calls.append("probe")
        return True, None

    registry.register(SandboxRuntimeRegistration(
        SandboxRuntime("local", "Local", "linux", ("/bin/sh", "-c")), lambda: RecordingBackend(tmp_path), probe))
    manager.registry = registry
    await manager.create("lab")
    await manager.get("lab")
    await manager.get("lab")
    assert len(calls) == 1
    await registry.describe(refresh=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_probe_failure_does_not_report_existing_command_as_stopped(tmp_path):
    manager, backend = make_manager(tmp_path)
    await manager.create("lab")
    await manager.start("lab")
    backend.records["lab"] = replace(backend.records["lab"], state=SandboxState.RUNNING)
    manager.registry._results["test-linux"] = replace(manager.registry._results["test-linux"], available=False, reason="Probe failed")
    info = await manager.get("lab")
    assert info.state == SandboxState.RUNNING
    assert not info.available


@pytest.mark.asyncio
async def test_cannot_bind_app_storage_ancestor_or_relative_directory(tmp_path):
    manager, _ = make_manager(tmp_path)
    await manager.create("lab")
    for path in (str(tmp_path), str(manager.root), "."):
        with pytest.raises(SandboxValidationError):
            await manager.configure_options("lab", {"workspace_path": path})


def test_api_saves_real_folder_uses_runtime_shell_and_preserves_settings_on_failure(tmp_path):
    manager, backend = make_manager(tmp_path)
    settings = Settings.for_data_root(manager.root)
    services = create_services(settings, sandbox_backend=manager)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    try:
        with TestClient(create_app(settings, services=services)) as client:
            response = client.post("/api/nodes", json={"type": "sandbox"})
            assert response.status_code == 201, response.text
            card_id = response.json()["id"]
            assert backend.created == []
            catalog = client.get("/api/sandbox/runtimes").json()
            assert catalog["default_runtime"] == "test-linux"
            patched = client.patch(f"/api/nodes/{card_id}", json={"config": {"workspace_path": str(first)}})
            assert patched.status_code == 200, patched.text
            assert client.post(f"/api/sandboxes/{card_id}/start").status_code == 200
            executed = client.post(f"/api/sandboxes/{card_id}/execute", json={"command": "echo hello"})
            assert executed.status_code == 200, executed.text
            assert backend.commands[-1] == ["/bin/sh", "-c", "echo hello"]
            refused = client.patch(f"/api/nodes/{card_id}", json={"config": {"workspace_path": str(second)}})
            assert refused.status_code == 409
            assert services.world.get_card(card_id).config["workspace_path"] == str(first)
            client.post(f"/api/sandboxes/{card_id}/stop")
            assert client.delete(f"/api/nodes/{card_id}").status_code == 200
            assert first.is_dir()
    finally:
        services.close()
