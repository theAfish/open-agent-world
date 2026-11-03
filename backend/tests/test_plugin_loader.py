from importlib.metadata import EntryPoint, EntryPoints
from pathlib import Path
import sys

import pytest

from backend.plugins import loader


def write_plugin(directory: Path, name: str, *, src: bool = False, requires_plugins: tuple[str, ...] = ()) -> EntryPoint:
    package = directory / name
    source = package / "src" if src else package
    source.mkdir(parents=True)
    (source / f"{name}.py").write_text(
        "from open_agent_world.plugin_api import PluginDefinition, PluginDescriptor\n"
        "def create_plugin():\n"
        "    return PluginDefinition(\n"
        f"        PluginDescriptor(id='test.{name}', version='1.0', plugin_api_version='1.20', requires_plugins={requires_plugins!r}),\n"
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


def test_context_receives_validated_source_environment_without_installing(tmp_path):
    from open_agent_world.plugin_api import PackEnvironmentManager, PythonEnvironmentSpec
    entry = write_plugin(tmp_path, 'autoload_context')
    root = tmp_path / entry.name
    project = root / 'pyproject.toml'
    project.write_text('[project]\nname = "autoload-context"\nversion = "1.0"\n' + project.read_text(encoding='utf-8'), encoding='utf-8')
    module = root / (entry.name + '.py')
    module.write_text(module.read_text(encoding='utf-8').replace(
        'def create_plugin():',
        'def create_plugin(*, context):\n    global CONTEXT\n    CONTEXT = context'), encoding='utf-8')
    (root / 'oaw.pack.toml').write_text('''schema_version = 3
[distribution]
id = "test.context-release"
[plugin]
owner = "test.autoload_context"
packs = []
[environments.worker]
target = "host-worker"
verify_imports = ["json"]
''', encoding='utf-8')
    profile = tmp_path / 'profile'
    registry = loader.load_plugin_registry(root, data_root=profile, load_unenrolled_sources=True, strict=True)
    context = sys.modules[entry.name].CONTEXT
    environment = context.environment('worker')
    assert isinstance(environment.manager, PackEnvironmentManager)
    assert environment.spec == PythonEnvironmentSpec.from_declaration(
        'test.autoload_context', 'worker', registry.pack_specs['test.autoload_context'].environments['worker'], root)
    assert environment.manager.root == profile / 'runtime'
    assert environment.status()['status'] == 'missing'
    with pytest.raises(RuntimeError, match='not ready'):
        environment.read()
    assert not (profile / 'runtime/environments').exists()
    with pytest.raises(ValueError, match='has no environment'):
        context.environment('someone-elses-worker')


@pytest.mark.parametrize('declaration', [
    {'target': 'sandbox'},
    {'target': 'host-worker', 'provider': 'external', 'management': 'external'},
    {'target': 'host-worker', 'platforms': ('unsupported-platform',)},
])
def test_context_rejects_environments_owned_by_other_targets_or_platforms(tmp_path, declaration):
    from open_agent_world.plugin_api import PluginContext
    from backend.packs.spec import EnvironmentSpec, read_source_pack
    from backend.tests.test_pack_spec import source
    root = source(tmp_path)
    pack = read_source_pack(root).model_copy(update={'environments': {'worker': EnvironmentSpec(**declaration)}})
    with pytest.raises(ValueError):
        PluginContext(tmp_path / 'profile', root, pack).environment('worker')
    assert not (tmp_path / 'profile').exists()


def test_legacy_context_does_not_guess_a_declaration_from_the_filesystem(tmp_path):
    context = loader.create_plugin(lambda *, context: context, data_root=tmp_path, source_root=tmp_path)
    with pytest.raises(ValueError, match='host-loaded Pack declaration'):
        context.environment('worker')


def test_installed_external_plugin_still_loads(tmp_path, monkeypatch):
    entry = write_plugin(tmp_path / "external", "autoload_external")
    monkeypatch.syspath_prepend(str(tmp_path / "external" / "autoload_external"))
    monkeypatch.setattr(loader, "entry_points", lambda: EntryPoints([entry]))
    registry = loader.load_plugin_registry(tmp_path / "empty")
    assert "test.autoload_external" in {p.id for p in registry.plugins()}


def test_retired_editable_source_cannot_reappear_from_stale_entrypoint(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    source = tmp_path / 'removed-project'
    source.mkdir()
    entry = SimpleNamespace(name='retired', value='retired:create_plugin',
        dist=SimpleNamespace(read_text=lambda name: json.dumps({'url': source.as_uri(), 'dir_info': {'editable': True}})),
        load=lambda: pytest.fail('A removed source must not load from stale editable metadata'))
    monkeypatch.setattr(loader, 'entry_points', lambda: {loader.ENTRY_POINT_GROUP: [entry]})
    registry = loader.load_plugin_registry(tmp_path / 'empty', strict=True)
    assert [p.id for p in registry.plugins()] == ['open-agent-world.core']


def test_broken_local_plugin_reports_manifest(tmp_path):
    entry = write_plugin(tmp_path, "autoload_broken")
    (tmp_path / entry.name / f"{entry.name}.py").write_text(
        "raise ImportError('missing dependency')\n", encoding="utf-8"
    )
    registry = loader.load_plugin_registry(tmp_path)
    assert not registry.has_plugin('test.autoload_broken')
    assert registry.load_failures[0]['phase'] == 'import'
    with pytest.raises(RuntimeError, match="autoload_broken.*pyproject.toml.*missing dependency"):
        loader.load_plugin_registry(tmp_path, strict=True)


def test_configured_directories_and_optional_runtime_manifest(tmp_path):
    directory = tmp_path / 'configured'
    write_plugin(directory, 'autoload_runtime')
    manifest = directory / 'autoload_runtime' / 'pyproject.toml'
    with manifest.open('a') as stream:
        stream.write('\n[tool.open-agent-world.runtime]\npython = ["numpy>=2"]\n')
    registry = loader.load_plugin_registry(tmp_path / 'empty', plugin_directories=(directory,))
    assert registry.runtime_requirements['test.autoload_runtime'] == ('numpy>=2',)


def test_default_and_configured_directories_are_independent_of_working_directory(tmp_path, monkeypatch):
    codex = Path(__file__).resolve().parents[2] / "packs/codex"
    monkeypatch.chdir(tmp_path)
    registry = loader.load_plugin_registry(plugin_directories=(codex,))
    agents = {node.id for node in registry.catalog().node_types if node.deck_id == "agents"}
    assert agents >= {"agent", "oaw.barracks", "openai.codex.agent"}


def test_explicit_codex_backend_publishes_codex_card_in_agents_deck(codex_client):
    client = codex_client
    response = client.get("/api/catalog")
    assert response.status_code == 200
    card = next(item for item in response.json()["node_types"] if item["id"] == "openai.codex.agent")
    assert card["deck_id"] == "agents"
    assert card["icon_url"] == "/api/plugins/openai.codex/assets/logo"
    assert card["frontend"] == {"settings": "settings"}
    assert card["user_creatable"] is True
    assert "workspace_path" in card["config_schema"]["properties"]


def test_declared_dependencies_load_before_consumers_across_sources(tmp_path, monkeypatch):
    write_plugin(tmp_path, 'autoload_consumer', requires_plugins=('test.autoload_dependency',))
    dependency = write_plugin(tmp_path / 'external', 'autoload_dependency')
    monkeypatch.syspath_prepend(str(tmp_path / 'external' / 'autoload_dependency'))
    monkeypatch.setattr(loader, 'entry_points', lambda: EntryPoints([dependency]))
    registry = loader.load_plugin_registry(tmp_path)
    ids = [plugin.id for plugin in registry.plugins()]
    assert ids.index('test.autoload_dependency') < ids.index('test.autoload_consumer')


@pytest.mark.parametrize('cycle', [False, True])
def test_missing_or_cyclic_dependencies_report_owners(tmp_path, cycle):
    name = 'autoload_cycle' if cycle else 'autoload_missing'
    write_plugin(tmp_path, name, requires_plugins=('test.' + name if cycle else 'test.absent',))
    registry = loader.load_plugin_registry(tmp_path)
    assert not registry.has_plugin('test.' + name)
    assert registry.load_failures[0]['phase'] == 'dependency'
    with pytest.raises(RuntimeError, match='test.' + name + '.*Missing or cyclic'):
        loader.load_plugin_registry(tmp_path, strict=True)


def test_direct_registration_checks_dependencies_before_registering():
    from open_agent_world.plugin_api import PluginDefinition, PluginDescriptor
    from backend.errors import PluginCompatibilityError
    registry = loader.create_builtin_registry()
    def register(_):
        pytest.fail('Registration must not run with missing dependencies')
    plugin = PluginDefinition(PluginDescriptor(id='test.dependent', version='1', plugin_api_version='1.20',
        requires_plugins=('test.absent',)), register)
    with pytest.raises(PluginCompatibilityError, match='requires plugin'):
        registry.install(plugin)
    assert not registry.has_plugin('test.dependent')
