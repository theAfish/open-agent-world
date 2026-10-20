import pytest

def test_plugin_preset_is_available_in_decks_and_disabled_with_its_plugin(client):
    from backend.card_library import LibraryEdit
    services = client.app.state.services
    library = services.card_library
    state = library.read()
    assert 'core.jobs' in state.packs['matcreator.default'].definition.cards
    state = library.edit(LibraryEdit(action='open_pack', id='matcreator.default', expected_revision=state.revision))
    assert state.collection['core.jobs'].plugin_id == 'open-agent-world.core'
    assert library.assert_collected('core.jobs').source_pack_ids == ['matcreator.default']
    state = library.edit(LibraryEdit(action='update_deck', id=state.active_deck_id, expected_revision=state.revision,
        entries=[{'kind': 'legion', 'id': 'matcreator.native-research'}]))
    assert any(e.id == 'matcreator.native-research' for d in state.decks for e in d.entries)
    services.plugins.set_enabled('matcreator', False)
    assert 'matcreator.native-research' not in {item['id'] for item in client.get('/api/legions/presets').json()}
    assert client.post('/api/legions/presets/matcreator.native-research/instances', json={}).status_code == 404


def test_invalid_plugin_presets_do_not_partially_install():
    from open_agent_world.plugin_api import LegionPresetDefinition, PresetNode, PresetEdge, PluginDefinition, PluginDescriptor
    from backend.plugins import create_builtin_registry
    registry = create_builtin_registry()
    def register(registration):
        registration.register_legion_preset(LegionPresetDefinition(id='example.research', name='Bad plan', nodes=(
            PresetNode(key='group', type='legion', name='Group', parent_key=None),
            PresetNode(key='agent', type='agent', name='Agent'),
        ), edges=(PresetEdge(source='agent', target='missing', relationship='participate'),)))
    with pytest.raises(ValueError, match='reference preset nodes'):
        registry.install(PluginDefinition(PluginDescriptor(id='example', version='1', plugin_api_version='1.18'), register))
    assert not registry.has_plugin('example')
    assert registry.legion_presets() == ()


def test_research_preset_requires_enabled_structure_viewer(client):
    services = client.app.state.services
    services.plugins.set_enabled('science.structure-viewer', False)
    assert 'matcreator.native-research' not in {item['id'] for item in client.get('/api/legions/presets').json()}
    services.plugins.set_enabled('science.structure-viewer', True)
    assert 'matcreator.native-research' in {item['id'] for item in client.get('/api/legions/presets').json()}
