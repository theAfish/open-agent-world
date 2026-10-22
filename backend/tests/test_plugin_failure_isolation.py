"""Optional activation failures must preserve durable objects and revoke grants."""
from importlib.metadata import EntryPoints
from pathlib import Path
import sqlite3
import sys

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.plugins import loader
from backend.services import create_services
from backend.world.models import CardCreate, EdgeCreate
from backend.tests.test_plugin_loader import write_plugin


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(loader, 'entry_points', lambda: EntryPoints())
    yield
    for name in list(sys.modules):
        if name.startswith('batch1_'):
            del sys.modules[name]


def recoverable_plugin(root):
    entry = write_plugin(root, 'batch1_recoverable')
    path = root / entry.name / (entry.name + '.py')
    path.write_text('''
from pathlib import Path
from pydantic import BaseModel
from open_agent_world.plugin_api import (PluginDefinition, PluginDescriptor,
    NodeTypeDefinition, RelationshipDefinition, CapabilityGrantDefinition, CapabilityDefinition)
class Config(BaseModel):
    message: str = "saved"
async def read(context, **kwargs):
    return "ok"
def register(r):
    r.register_node_type(NodeTypeDefinition(id="test.recoverable.card", label="Saved", description="",
        icon="box", color="#123456", deck_id="test", deck_label="Test", deck_icon="box",
        default_name="Saved", default_size=(300, 200), default_status="idle", statuses=frozenset({"idle"}), config_model=Config))
    r.register_capability(CapabilityDefinition(kind="test.recoverable.read", tool_name="test_read", description="Read", read_only=True), read)
    r.register_relationship(RelationshipDefinition(id="test.recoverable.link", label="Read", short_label="Read", description="",
        source_types=frozenset({"agent"}), target_types=frozenset({"test.recoverable.card"}),
        capabilities=(CapabilityGrantDefinition(kind="test.recoverable.read"),)))
    if Path(__file__).with_suffix('.fail').exists():
        raise ValueError("deliberate registration failure after staging all contributions")
def create_plugin():
    return PluginDefinition(PluginDescriptor(id="test.batch1_recoverable", version="1.0", plugin_api_version="1.20"), register)
''', encoding='utf-8')
    return path.with_suffix('.fail')


def test_failed_registration_preserves_cards_edges_config_and_revokes_capabilities(tmp_path):
    sources = tmp_path / 'plugins'
    failure = recoverable_plugin(sources)
    write_plugin(sources, 'batch1_independent')
    write_plugin(sources, 'batch1_downstream', requires_plugins=('test.batch1_recoverable',))
    settings = Settings.for_data_root(tmp_path / 'profile')
    load = lambda: loader.load_plugin_registry(sources, data_root=settings.data_root)
    registry = load()
    assert not registry.load_failures
    services = create_services(settings, plugins=registry)
    agent = services.world.create_card(CardCreate(type='agent'))
    card = services.world.create_card(CardCreate(type='test.recoverable.card', config={'message': 'unchanged'}))
    edge = services.world.create_edge(EdgeCreate(source=agent.id, target=card.id, relationship='test.recoverable.link'))
    assert services.capabilities.derive(agent.id).capabilities
    with services.database.locked() as db:
        before = tuple(db.execute('SELECT config_json,x,y,width,height FROM cards WHERE id=?', (card.id,)).fetchone())
    services.close()

    failure.touch()
    failed = load()
    assert failed.has_plugin('test.batch1_independent')
    assert not failed.has_plugin('test.batch1_recoverable')
    assert not failed.has_plugin('test.batch1_downstream')
    assert {r['phase'] for r in failed.load_failures} == {'registration', 'dependency'}
    for kind, identifier in [('node_type', 'test.recoverable.card'), ('relationship', 'test.recoverable.link'),
                             ('capability_handler', 'test.recoverable.read')]:
        with pytest.raises(ValueError):
            failed.owner_id(kind, identifier)
    services = create_services(settings, plugins=failed)
    with TestClient(create_app(settings, services=services)) as client:
        assert client.get('/api/catalog').status_code == 200
        assert client.get('/api/packs').json()['failures']
        assert services.world.get_card(card.id).missing_plugin
        assert services.world.get_edge(edge.id).missing_plugin
        from backend.api.card_library import plugin_usage
        assert plugin_usage(services, 'test.batch1_recoverable')
        assert plugin_usage(services, 'test.batch1_independent') == []
        assert not services.capabilities.derive(agent.id).capabilities
        with services.database.locked() as db:
            assert tuple(db.execute('SELECT config_json,x,y,width,height FROM cards WHERE id=?', (card.id,)).fetchone()) == before
    services.close()

    failure.unlink()
    recovered = load()
    assert not recovered.load_failures
    services = create_services(settings, plugins=recovered)
    try:
        assert services.world.get_card(card.id).config == {'message': 'unchanged'}
        assert services.world.get_card(card.id).missing_plugin is None
        assert services.world.get_edge(edge.id).missing_plugin is None
        assert services.capabilities.derive(agent.id).capabilities
    finally:
        services.close()


def test_discovery_and_missing_dependencies_do_not_block_independent_sources(tmp_path):
    write_plugin(tmp_path, 'batch1_good')
    entry = write_plugin(tmp_path, 'batch1_bad')
    (tmp_path / entry.name / (entry.name + '.py')).write_text('import batch1_missing_dependency\n')
    bad = tmp_path / 'malformed'
    bad.mkdir()
    (bad / 'pyproject.toml').write_text('invalid toml [')
    registry = loader.load_plugin_registry(tmp_path)
    assert registry.has_plugin('test.batch1_good')
    assert {r['phase'] for r in registry.load_failures} == {'import', 'discovery'}


def test_source_metadata_is_discovered_before_any_import_or_path_mutation(tmp_path, monkeypatch):
    write_plugin(tmp_path, 'batch1_static')
    import backend.packs.spec as spec
    original = spec.read_source_pack
    before = list(sys.path)
    def inspect(root):
        assert sys.path == before
        assert 'batch1_static' not in sys.modules
        return original(root)
    monkeypatch.setattr(spec, 'read_source_pack', inspect)
    assert loader.load_plugin_registry(tmp_path).has_plugin('test.batch1_static')


def test_broken_editable_source_cannot_bypass_discovery_through_stale_metadata(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    entry = write_plugin(tmp_path, 'batch1_stale')
    source = tmp_path / entry.name
    (source / 'pyproject.toml').write_text('broken toml [')
    metadata = SimpleNamespace(name=entry.name, value=entry.value,
        dist=SimpleNamespace(read_text=lambda name: json.dumps({'url': source.as_uri(), 'dir_info': {'editable': True}})),
        load=lambda: pytest.fail('Stale editable entrypoint must not load'))
    monkeypatch.setattr(loader, 'entry_points', lambda: {loader.ENTRY_POINT_GROUP: [metadata]})
    registry = loader.load_plugin_registry(tmp_path)
    assert not registry.has_plugin('test.batch1_stale')
    assert len(registry.load_failures) == 1
    assert registry.load_failures[0]['phase'] == 'discovery'


@pytest.mark.parametrize('error', [sqlite3.DatabaseError('damaged database'), MemoryError('out of memory')])
def test_storage_and_resource_failures_are_fatal(tmp_path, monkeypatch, error):
    write_plugin(tmp_path, 'batch1_fatal')
    monkeypatch.setattr(loader, 'create_plugin', lambda *a, **k: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        loader.load_plugin_registry(tmp_path)


def test_core_failure_remains_fatal(monkeypatch):
    monkeypatch.setattr(loader, 'create_builtin_registry', lambda: (_ for _ in ()).throw(ValueError('invalid Core')))
    with pytest.raises(ValueError, match='invalid Core'):
        loader.load_plugin_registry(core_only=True)


def test_core_only_never_discovers_optional_code(tmp_path, monkeypatch):
    def forbidden():
        pytest.fail('Core-only startup must not inspect installed optional distributions')
    monkeypatch.setattr(loader, 'entry_points', forbidden)
    settings = Settings.for_data_root(tmp_path / 'profile')
    services = create_services(settings, plugins=loader.load_plugin_registry(core_only=True))
    with TestClient(create_app(settings, services=services)) as client:
        assert client.get('/api/catalog').status_code == 200
        assert {p['id'] for p in client.get('/api/catalog').json()['plugins']} == {'open-agent-world.core'}
        assert client.get('/api/packs').status_code == 200
    services.close()


def test_core_only_reconciliation_retains_existing_source_resources(tmp_path):
    from backend.packs.installation import PackInstallationManager
    from backend.packs.lifecycle import ManagedResourceLedger
    ledger = ManagedResourceLedger(tmp_path)
    resource = 'environment:' + 'a' * 64
    ledger.claim('source:retained.pack', resource)
    registry = loader.load_plugin_registry(core_only=True)
    manager = PackInstallationManager(tmp_path, registry)
    assert manager.reconcile_resources(collect=True) == []
    with ledger.connect() as db:
        assert db.execute('SELECT resource FROM claims WHERE consumer=?', ('source:retained.pack',)).fetchone()['resource'] == resource


def test_host_resource_accounting_failure_is_fatal(tmp_path, monkeypatch):
    from backend.packs.lifecycle import ManagedResourceLedger
    write_plugin(tmp_path / 'plugins', 'batch1_accounting')
    def invalid(*args, **kwargs):
        raise ValueError('Invalid resource accounting')
    monkeypatch.setattr(ManagedResourceLedger, 'pin_process', invalid)
    with pytest.raises(ValueError, match='Invalid resource accounting'):
        loader.load_plugin_registry(tmp_path / 'plugins', data_root=tmp_path / 'profile')
