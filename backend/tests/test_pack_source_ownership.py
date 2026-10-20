"""Archive selection reserves loaded source release and Python package IDs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.packs.installation import PackInstallationManager
from backend.packs.spec import BackendComponent, PackSpec
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.loader import load_installed_packs
from backend.plugins.registry import PluginDefinition, PluginDescriptor
from backend.tests.test_pack_installation import isolate_modules
from backend.tests.test_pack_unification import unified


def source_registry(tmp_path: Path, *, distribution="source.release", module="oaw_source_owned", extra_module=None):
    registry = create_builtin_registry()
    registry.install(PluginDefinition(PluginDescriptor(id="source.owner", version="1.0.0", plugin_api_version="1.23"),
                                      lambda registration: None))
    root = tmp_path / "source"
    root.mkdir()
    packages = [f"src/{module}"] + ([f"src/{extra_module}"] if extra_module else [])
    (root / "pyproject.toml").write_text('[tool.hatch.build.targets.wheel]\npackages=' + json.dumps(packages), encoding="utf-8")
    registry.pack_specs["source.owner"] = PackSpec(distribution_id=distribution, owner="source.owner", version="1.0.0",
        name="Source", packs=(), backend=BackendComponent(), entrypoints={"factory": f"{module}:create_plugin"}, source_root=root)
    registry.pack_roots["source.owner"] = root
    registry.pack_sources["source.owner"] = "development"
    return registry


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("secondary", [True, False])
def test_source_adapter_and_unimported_engine_roots_block_before_preparation(tmp_path, monkeypatch, enabled, secondary):
    registry = source_registry(tmp_path,
        module="oaw_source_owned" if secondary else "oaw_pack_test_schema",
        extra_module="oaw_pack_test_schema" if secondary else None)
    registry.set_enabled("source.owner", enabled)
    manager = PackInstallationManager(tmp_path / "profile", registry)
    monkeypatch.setattr(manager, "_prepare", lambda *args: pytest.fail("Ownership conflict must precede preparation"))
    with pytest.raises(ValueError, match="oaw_pack_test_schema.*bundled/development ownership"):
        manager.prepare(unified())
    assert manager.operations() == []
    assert manager.selected() == {}
    assert not (tmp_path / "profile" / "runtime").exists()


@pytest.mark.parametrize("identity", ["test.greeter-release", "test.runtime", "test.runtime.cards"])
def test_source_distribution_id_is_reserved_separately_from_owner_and_pack_ids(tmp_path, monkeypatch, identity):
    registry = source_registry(tmp_path, distribution=identity)
    manager = PackInstallationManager(tmp_path / "profile", registry)
    monkeypatch.setattr(manager, "_prepare", lambda *args: pytest.fail("Ownership conflict must precede preparation"))
    with pytest.raises(ValueError, match="conflicts with bundled/development ownership"):
        manager.prepare(unified())
    assert manager.operations() == []


def test_loaded_archive_is_not_misclassified_as_source_during_own_upgrade(tmp_path):
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    manager.install(unified())
    load_installed_packs(registry, tmp_path)
    # Registry entries for an installed release use the same normalized models;
    # these must be excluded from bundled/development ownership reservations.
    manager.inspect(unified("0.2.0"))


def test_unrelated_source_keeps_read_only_archive_inspection_working(tmp_path):
    registry = source_registry(tmp_path)
    manager = PackInstallationManager(tmp_path / "profile", registry)
    assert manager.inspect(unified()).manifest.id == "test.greeter-release"
    assert manager.operations() == []
