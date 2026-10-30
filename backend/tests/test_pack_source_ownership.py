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


@pytest.fixture
def selected_with_source(tmp_path):
    from backend.packs.build import build_project
    from backend.tests.test_declarative_packs import project
    root = project(tmp_path)
    archive = build_project(root, tmp_path / "notes.oawpack")
    data = tmp_path / "profile"
    PackInstallationManager(data, create_builtin_registry()).install(archive.read_bytes())
    return root, data


def test_selected_archive_wins_when_matching_source_is_discovered_on_restart(selected_with_source):
    from backend.plugins import loader
    root, data = selected_with_source
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace('version="1.0.0"', 'version="2.0.0"'))
    registry = loader.load_plugin_registry(root.parent, data_root=data, load_entry_points=False, strict=True)
    assert registry.installed_packs["test.notes"].version == "1.0.0"
    assert registry.pack_sources["test.notes"] == "installed"
    assert next(plugin for plugin in registry.plugins() if plugin.id == "test.notes").version == "1.0.0"
    assert project.is_file()


@pytest.mark.parametrize("distribution,owner", [("test.notes", "other.notes"), ("other.notes", "test.notes")])
def test_selected_archive_does_not_suppress_unrelated_source_identity(selected_with_source, distribution, owner):
    from backend.plugins import loader
    root, data = selected_with_source
    declaration = root / "oaw.pack.toml"
    declaration.write_text(declaration.read_text().replace('id="test.notes"', f'id="{distribution}"')
                           + f'\n[plugin]\nowner="{owner}"\npacks=["{owner}.default"]\n')
    registry = loader.load_plugin_registry(root.parent, data_root=data, load_entry_points=False)
    assert registry.has_plugin(owner)
    assert "test.notes" not in registry.installed_packs
    assert any("bundled/development ownership" in failure["error"] for failure in registry.load_failures)


def test_explicit_source_tooling_can_load_source_instead_of_selected_archive(selected_with_source):
    from backend.plugins import loader
    root, data = selected_with_source
    registry = loader.load_plugin_registry(root.parent, data_root=data, load_entry_points=False,
                                           load_installed=False, load_unenrolled_sources=True, strict=True)
    assert registry.has_plugin("test.notes")
    assert registry.pack_sources["test.notes"] == "bundled"
    assert registry.pack_roots["test.notes"] == root
    assert registry.installed_packs == {}


def test_failed_selected_archive_does_not_fall_back_to_matching_source(selected_with_source, monkeypatch):
    from backend.plugins import loader
    root, data = selected_with_source
    def fail_startup(*args):
        raise ValueError("Selected archive is unavailable")
    monkeypatch.setattr(PackInstallationManager, "startup_entrypoint", fail_startup)
    registry = loader.load_plugin_registry(root.parent, data_root=data, load_entry_points=False)
    assert not registry.has_plugin("test.notes")
    assert registry.pack_sources["test.notes"] == "installed"
    assert registry.load_failures[0]["error"] == "Selected archive is unavailable"
    assert PackInstallationManager(data).selected()["test.notes"].version == "1.0.0"
