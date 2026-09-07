from importlib.metadata import EntryPoint, EntryPoints
from pathlib import Path
import sys

import pytest

from backend.plugins import loader


def write_plugin(directory: Path, name: str, *, src: bool = False) -> EntryPoint:
    package = directory / name
    source = package / "src" if src else package
    source.mkdir(parents=True)
    (source / f"{name}.py").write_text(
        "from open_agent_world.plugin_api import PluginDefinition, PluginDescriptor\n"
        "def create_plugin():\n"
        "    return PluginDefinition(\n"
        f"        PluginDescriptor(id='test.{name}', version='1.0', plugin_api_version='1.0'),\n"
        "        lambda registration: None)\n",
        encoding="utf-8",
    )
    entry = EntryPoint(name=name, value=f"{name}:create_plugin", group=loader.ENTRY_POINT_GROUP)
    (package / "pyproject.toml").write_text(
        f'[project.entry-points."{loader.ENTRY_POINT_GROUP}"]\n{name} = "{entry.value}"\n',
        encoding="utf-8",
    )
    return entry


@pytest.fixture(autouse=True)
def isolate_discovery(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(loader, "entry_points", lambda: EntryPoints())


def test_loads_local_flat_and_src_packages_without_installation(tmp_path):
    write_plugin(tmp_path, "autoload_flat")
    write_plugin(tmp_path, "autoload_src", src=True)
    (tmp_path / "unrelated").mkdir()
    registry = loader.load_plugin_registry(tmp_path)
    assert {p.id for p in registry.plugins()} >= {"test.autoload_flat", "test.autoload_src"}


def test_local_entry_is_not_loaded_twice_when_also_installed(tmp_path, monkeypatch):
    entry = write_plugin(tmp_path, "autoload_editable")
    monkeypatch.setattr(loader, "entry_points", lambda: EntryPoints([entry]))
    registry = loader.load_plugin_registry(tmp_path)
    assert [p.id for p in registry.plugins()].count("test.autoload_editable") == 1


def test_installed_external_plugin_still_loads(tmp_path, monkeypatch):
    entry = write_plugin(tmp_path / "external", "autoload_external")
    monkeypatch.syspath_prepend(str(tmp_path / "external" / "autoload_external"))
    monkeypatch.setattr(loader, "entry_points", lambda: EntryPoints([entry]))
    registry = loader.load_plugin_registry(tmp_path / "empty")
    assert "test.autoload_external" in {p.id for p in registry.plugins()}


def test_broken_local_plugin_reports_manifest(tmp_path):
    entry = write_plugin(tmp_path, "autoload_broken")
    (tmp_path / entry.name / f"{entry.name}.py").write_text(
        "raise ImportError('missing dependency')\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="autoload_broken.*pyproject.toml.*missing dependency"):
        loader.load_plugin_registry(tmp_path)


def test_default_directory_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = loader.load_plugin_registry()
    agents = {node.id for node in registry.catalog().node_types if node.deck_id == "agents"}
    assert agents >= {"agent", "oaw.barracks", "openai.codex.agent"}


def test_default_backend_publishes_codex_card_in_agents_deck(client):
    response = client.get("/api/catalog")
    assert response.status_code == 200
    card = next(item for item in response.json()["node_types"] if item["id"] == "openai.codex.agent")
    assert card["deck_id"] == "agents"
    assert card["icon_url"] == "/api/plugins/openai.codex/assets/logo"
    assert card["frontend"] == {"settings": "settings"}
    assert card["user_creatable"] is True
    assert "workspace_path" in card["config_schema"]["properties"]
