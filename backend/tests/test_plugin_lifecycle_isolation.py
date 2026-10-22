"""Real persisted cards cross a successful registration / failed restore boundary."""
import asyncio
import sqlite3
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.errors import PermissionDeniedError, PluginUnavailableError
from backend.main import create_app
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.lifecycle import NodeLifecycleHandler
from backend.plugins.failures import PluginIntegrityError
from backend.plugins.registry import (PluginDefinition, PluginDescriptor, RelationshipDefinition,
    CapabilityDefinition, CapabilityGrantDefinition)
from backend.services import create_services
from backend.tests.test_node_lifecycle import _plugin_node
from backend.world.models import CardCreate, EdgeCreate


class Restore(NodeLifecycleHandler):
    def __init__(self, error=None, cleanup_error=None):
        self.error, self.cleanup_error = error, cleanup_error
        self.live, self.started, self.closed = set(), [], []

    async def on_startup(self, context, node):
        self.live.add(node.id)
        self.started.append(node.id)
        if self.error and len(self.started) == 2:
            raise self.error

    async def on_shutdown(self, context, node):
        self.closed.append(node.id)
        if self.cleanup_error:
            raise self.cleanup_error
        self.live.discard(node.id)


def registry(handler, independent=None, dependent=None):
    result = create_builtin_registry()
    def register(r):
        r.register_node_type(_plugin_node('test.restored', handler))
        r.register_capability(CapabilityDefinition(kind='test.read', tool_name='read_restored',
            description='Read', read_only=True), lambda *args: None)
        r.register_relationship(RelationshipDefinition(id='test.read', label='Read', short_label='Read', description='',
            source_types=frozenset({'agent'}), target_types=frozenset({'test.restored'}),
            capabilities=(CapabilityGrantDefinition(kind='test.read'),)))
    result.install(PluginDefinition(PluginDescriptor(id='test.restore', version='1.0', plugin_api_version='1.37'), register))
    for name, lifecycle, requires in [('independent', independent, ()), ('dependent', dependent, ('test.restore',))]:
        result.install(PluginDefinition(PluginDescriptor(id='test.' + name, version='1.0', plugin_api_version='1.37',
            requires_plugins=requires), lambda r, name=name, lifecycle=lifecycle: r.register_node_type(_plugin_node('test.' + name, lifecycle))))
    return result


def seed(settings):
    services = create_services(settings, plugins=registry(Restore()))
    agent = services.world.create_card(CardCreate(type='agent'))
    cards = [services.world.create_card(CardCreate(type='test.restored', config={'value': value})) for value in (1, 2)]
    edge = services.world.create_edge(EdgeCreate(source=agent.id, target=cards[0].id, relationship='test.read'))
    independent = services.world.create_card(CardCreate(type='test.independent'))
    services.world.create_card(CardCreate(type='test.dependent'))
    resource = services.resources.node_storage_path(cards[0].id)
    resource.mkdir(parents=True, exist_ok=True)
    (resource / 'user.txt').write_text('retain exact user data')
    with services.database.locked() as db:
        saved = [tuple(row) for row in db.execute("SELECT * FROM cards WHERE type='test.restored' ORDER BY id")]
    services.close()
    return agent, cards, edge, independent, resource, saved


def test_failed_second_card_restore_releases_runtime_revokes_grants_and_recovers(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    agent, cards, edge, independent, resource, saved = seed(settings)
    broken, good, downstream = Restore(ValueError('restore fixture failed')), Restore(), Restore()
    services = create_services(settings, plugins=registry(broken, good, downstream))
    with TestClient(create_app(settings, services=services)) as client:
        assert client.get('/api/catalog').status_code == 200
        assert not broken.live and set(broken.closed) == {card.id for card in cards}
        assert good.live == {independent.id} and not downstream.started
        assert services.world.get_card(cards[0].id).missing_plugin.reason == 'plugin_unavailable'
        assert services.world.get_edge(edge.id).missing_plugin
        assert not services.capabilities.derive(agent.id).capabilities
        assert client.get('/api/packs').json()['failures'][0]['phase'] in {'startup', 'dependency'}
        # Library reconciliation preserves enabled intent but cannot clear failure.
        assert services.card_library.read().plugins['test.restore'].enabled
        assert not services.plugins.is_enabled('test.restore')
        with pytest.raises(PluginUnavailableError):
            services.plugins.capability_handler('test.read')
        assert client.post('/api/nodes', json={'type': 'test.independent'}).status_code == 201
        with services.database.locked() as db:
            assert [tuple(row) for row in db.execute("SELECT * FROM cards WHERE type='test.restored' ORDER BY id")] == saved
        assert (resource / 'user.txt').read_text() == 'retain exact user data'
    services.close()
    repaired = Restore()
    services = create_services(settings, plugins=registry(repaired))
    with TestClient(create_app(settings, services=services)):
        assert repaired.live == {card.id for card in cards}
        assert services.world.get_card(cards[0].id).missing_plugin is None
        assert services.world.get_edge(edge.id).missing_plugin is None
        assert services.capabilities.derive(agent.id).capabilities
        assert services.world.get_card(cards[1].id).config == cards[1].config
    services.close()


@pytest.mark.parametrize('error', [sqlite3.DatabaseError('corrupt'), PermissionDeniedError('security boundary'),
                                   PluginIntegrityError('uncertain persistence')])
def test_unsafe_restore_errors_still_block_startup(tmp_path, error):
    settings = Settings.for_data_root(tmp_path)
    seed(settings)
    services = create_services(settings, plugins=registry(Restore(error)))
    with pytest.raises(type(error)):
        asyncio.run(services.startup())
    asyncio.run(services.shutdown())
    services.close()


def test_failed_compensation_blocks_startup(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    seed(settings)
    services = create_services(settings, plugins=registry(Restore(ValueError('restore'), RuntimeError('cannot close'))))
    with pytest.raises(PluginIntegrityError, match='cleanup failed'):
        asyncio.run(services.startup())
    asyncio.run(services.shutdown())
    services.close()


def test_shutdown_attempts_other_plugins_and_runtime_cleanup_before_reporting_error(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    seed(settings)
    broken, good = Restore(cleanup_error=ValueError('close failed')), Restore()
    services = create_services(settings, plugins=registry(broken, good))
    async def run():
        await services.startup()
        with pytest.raises(ExceptionGroup, match='shutdown'):
            await services.shutdown()
    asyncio.run(run())
    assert not good.live and good.closed
    assert services.run_manager.shutting_down
    services.close()


def test_strict_startup_reports_soft_lifecycle_failure(tmp_path, monkeypatch):
    settings = Settings.for_data_root(tmp_path)
    seed(settings)
    monkeypatch.setenv('OPEN_AGENT_WORLD_STRICT_PLUGINS', '1')
    services = create_services(settings, plugins=registry(Restore(ValueError('restore'))))
    with pytest.raises(RuntimeError, match='Optional plugin checks failed'):
        asyncio.run(services.startup())
    asyncio.run(services.shutdown())
    services.close()


def test_unfinished_transaction_is_not_an_optional_failure(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    seed(settings)
    handler = Restore()
    services = create_services(settings, plugins=registry(handler))
    async def leave_transaction(context, node):
        with services.database.locked() as db:
            db.execute('BEGIN')
        raise ValueError('callback left transaction open')
    handler.on_startup = leave_transaction
    with pytest.raises(PluginIntegrityError, match='unfinished'):
        asyncio.run(services.startup())
    with services.database.locked() as db:
        db.rollback()
    asyncio.run(services.shutdown())
    services.close()


def test_optional_runtime_restore_failure_cleans_agent_records_and_blocks_future_admission(tmp_path):
    from backend.agents import MockAgentRuntime
    class BrokenRuntime(MockAgentRuntime):
        async def create_agent(self, config):
            result = await super().create_agent(config)
            raise ValueError('provider failed after creating local agent')
    settings = Settings.for_data_root(tmp_path)
    base = create_builtin_registry()
    services = create_services(settings, plugins=base)
    agent = services.world.create_card(CardCreate(type='agent', config={'runtime_provider_id': 'test.runtime'}))
    services.close()
    runtime = None
    def factory(capabilities):
        nonlocal runtime
        runtime = BrokenRuntime(capabilities)
        return runtime
    base = create_builtin_registry()
    base.install(PluginDefinition(PluginDescriptor(id='test.provider', version='1.0', plugin_api_version='1.37'),
        lambda r: r.register_runtime_provider('test.runtime', factory)))
    services = create_services(settings, plugins=base)
    with TestClient(create_app(settings, services=services)) as client:
        assert client.get('/api/catalog').status_code == 200
        assert services.plugins.is_enabled('open-agent-world.core')
        assert not services.plugins.is_enabled('test.provider')
        with pytest.raises(PluginUnavailableError):
            services.run_manager.provider_for_agent_configuration('test.runtime')
        assert agent.id not in services.run_manager._agent_provider_ids
        from backend.agents import AgentNotFoundError
        with pytest.raises(AgentNotFoundError):
            asyncio.run(runtime.get_agent(agent.id))
    services.close()


@pytest.mark.parametrize('core', [False, True])
def test_core_runtime_failure_and_optional_cleanup_timeout_are_fatal(tmp_path, core):
    from backend.agents import MockAgentRuntime
    class BrokenRuntime(MockAgentRuntime):
        async def create_agent(self, config):
            await super().create_agent(config)
            raise ValueError('runtime restoration failed')
        async def delete_agent(self, agent_id):
            await asyncio.sleep(60)
    base = create_builtin_registry()
    provider_id = 'core.mock' if core else 'test.runtime'
    if not core:
        base.install(PluginDefinition(PluginDescriptor(id='test.provider', version='1.0', plugin_api_version='1.38'),
            lambda r: r.register_runtime_provider(provider_id, lambda capabilities: BrokenRuntime(capabilities))))
    services = create_services(Settings.for_data_root(tmp_path), plugins=base)
    if core:
        services.install_runtime_provider(provider_id, BrokenRuntime(services.run_manager.capability_provider))
    services.run_manager.cleanup_timeout_seconds = .01
    card = services.world.create_card(CardCreate(type='agent', config={'runtime_provider_id': provider_id}))
    with pytest.raises(ValueError if core else PluginIntegrityError):
        asyncio.run(services.run_manager.restore_agent(card))
    assert services.world.get_card(card.id).id == card.id
    services.close()
