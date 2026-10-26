"""Persisted card identity is explicit and independent of release naming."""
from dataclasses import replace

import pytest

from backend.packs.manifest import PackManifest
from backend.packs.installation import PackInstallationManager
from backend.packs.spec import BackendComponent, PackSpec
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.registry import PackDefinition, PluginDefinition, PluginDescriptor
from backend.tests.test_pack_installation import MANIFEST


def manifest(**changes):
    return PackManifest.model_validate({**MANIFEST, 'schema_version': 3,
        'owner': 'example.library', 'packs': ['example.library.cards'],
        'node_types': ['historic.paper'], **changes})


def plugin(registry, *, cards=('historic.paper',), own_card=True):
    node = replace(registry._nodes['text'], id='historic.paper')
    def register(staged):
        if own_card:
            staged.register_node_type(node)
        staged.register_pack(PackDefinition(id='example.library.cards', name='Library', cards=cards))
    return PluginDefinition(PluginDescriptor(id='example.library', version='0.1.0', plugin_api_version='1.35'), register)


@pytest.mark.parametrize('source', [False, True])
def test_explicit_card_ids_survive_owner_namespace_change(source):
    registry = create_builtin_registry()
    declaration = PackSpec(distribution_id='test.greeter', name='Library', version='0.1.0',
        owner='example.library', packs=('example.library.cards',), node_types=('historic.paper',),
        backend=BackendComponent()) if source else manifest()
    registry.install(plugin(registry), **({'source_spec': declaration} if source else {'distribution': declaration}))
    assert registry.node_type_owner_id('historic.paper') == 'example.library'


def test_mismatch_fails_before_any_registration_is_committed():
    registry = create_builtin_registry()
    with pytest.raises(ValueError, match='exactly its declared owned node types'):
        registry.install(plugin(registry), distribution=manifest(node_types=['historic.other']))
    assert not registry.has_plugin('example.library')
    assert 'historic.paper' not in registry._nodes


def test_host_card_collection_does_not_take_ownership():
    registry = create_builtin_registry()
    registry.install(plugin(registry, cards=('text',), own_card=False), distribution=manifest(node_types=[]))
    assert registry.node_type_owner_id('text') == 'open-agent-world.core'


@pytest.mark.parametrize('node_types', [['core.text'], ['historic.paper', 'historic.paper'], ['../paper']])
def test_invalid_ownership_declarations_are_rejected(node_types):
    with pytest.raises(ValueError, match='Owned node types'):
        manifest(node_types=node_types)


def test_selected_archives_cannot_claim_the_same_card_before_preparation(tmp_path):
    first = manifest()
    second = manifest(id='other.release', owner='other.owner', packs=['other.owner.cards'])
    with pytest.raises(ValueError, match='Conflicting node type ownership'):
        PackInstallationManager(tmp_path)._check_selection({first.id: first, second.id: second})


def test_loaded_source_card_blocks_foreign_archive_before_preparation(tmp_path):
    registry = create_builtin_registry()
    registry.install(plugin(registry))
    foreign = manifest(id='other.release', owner='other.owner', packs=['other.owner.cards'])
    with pytest.raises(ValueError, match='Node type historic.paper conflicts'):
        PackInstallationManager(tmp_path, registry)._check_selection({foreign.id: foreign})


@pytest.mark.parametrize('collision', ['card', 'module'])
def test_source_restore_checks_selected_archives_before_preparation(tmp_path, monkeypatch, collision):
    from backend.tests.test_pack_source_ownership import source_registry
    import backend.packs.adapters as adapters
    registry = source_registry(tmp_path, module='oaw_source_owned')
    registry.pack_specs['source.owner'] = registry.pack_specs['source.owner'].model_copy(
        update={'node_types': ('historic.paper',)})
    manager = PackInstallationManager(tmp_path / 'profile', registry)
    foreign = manifest(node_types=['historic.paper'] if collision == 'card' else [],
                       backend_modules=['oaw_source_owned'] if collision == 'module' else ['oaw_foreign'])
    monkeypatch.setattr(manager, 'selected', lambda *args: {foreign.id: foreign})
    monkeypatch.setattr(adapters, 'prepare_source_adapter', lambda *args, **kwargs: pytest.fail('No preparation before ownership checks'))
    with manager.connect(write=True) as db:
        db.execute("INSERT INTO source_states VALUES ('source.release',0)")
    with pytest.raises(ValueError, match='already owns'):
        manager.restore_source('source.release')
    assert not manager.source_enrolled('source.release')


def test_source_restore_rechecks_selection_changed_during_preparation(tmp_path, monkeypatch):
    from backend.tests.test_pack_source_ownership import source_registry
    import backend.packs.adapters as adapters
    registry = source_registry(tmp_path)
    registry.pack_specs['source.owner'] = registry.pack_specs['source.owner'].model_copy(
        update={'node_types': ('historic.paper',)})
    manager = PackInstallationManager(tmp_path / 'profile', registry)
    selected = {}
    monkeypatch.setattr(manager, 'selected', lambda *args: dict(selected))
    foreign = manifest()
    monkeypatch.setattr(adapters, 'prepare_source_adapter', lambda *args, **kwargs: selected.update({foreign.id: foreign}))
    with manager.connect(write=True) as db:
        db.execute("INSERT INTO source_states VALUES ('source.release',0)")
    with pytest.raises(ValueError, match='already owns a declared source card'):
        manager.restore_source('source.release')
    assert not manager.source_enrolled('source.release')
