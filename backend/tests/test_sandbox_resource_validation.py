from pathlib import Path

import pytest

from backend.sandbox.darwin_seatbelt import DarwinSeatbeltBackend
from backend.sandbox.linux import LinuxSandboxBackend
from backend.sandbox.macos_container import MacosContainerSandboxBackend
from backend.sandbox.models import ResourceAccess, SandboxValidationError


@pytest.fixture(params=[LinuxSandboxBackend, MacosContainerSandboxBackend, DarwinSeatbeltBackend])
def sandbox_backend(request, tmp_path):
    return request.param(tmp_path / "managed")


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", [
    "outside", "directory", "missing", "symlink",
    "sandbox-runtimes", "sandboxes", "sandbox-bindings", "runtime",
])
async def test_attachment_rejects_invalid_managed_source(
    sandbox_backend, tmp_path, monkeypatch, source_kind,
):
    await sandbox_backend.create("box")
    managed_root = tmp_path / "managed"
    source = managed_root / "assets" / "input.txt"
    if source_kind == "outside":
        source = tmp_path / "outside.txt"
    elif source_kind in {"sandbox-runtimes", "sandboxes", "sandbox-bindings", "runtime"}:
        source = managed_root / source_kind / "input.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    if source_kind == "directory":
        source.mkdir()
    elif source_kind != "missing":
        source.write_text("input", encoding="utf-8")
    if source_kind == "symlink":
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == source or original_is_symlink(path))

    with pytest.raises(SandboxValidationError):
        await sandbox_backend.attach_resource("box", "input", source, "input.txt", ResourceAccess.READ_ONLY)
    assert not (await sandbox_backend.get("box")).attachments


@pytest.mark.asyncio
async def test_missing_attachment_can_be_reloaded_for_cleanup(sandbox_backend, tmp_path):
    await sandbox_backend.create("box")
    managed_root = tmp_path / "managed"
    source = managed_root / "input.txt"
    source.write_text("input", encoding="utf-8")
    await sandbox_backend.attach_resource("box", "input", source, "input.txt", ResourceAccess.READ_ONLY)
    source.unlink()

    restarted_backend = type(sandbox_backend)(managed_root)
    restored = await restarted_backend.get("box")
    assert restored.attachments[0].source == source.resolve()
    await restarted_backend.detach_resource("box", "input")
    assert not (await restarted_backend.get("box")).attachments
