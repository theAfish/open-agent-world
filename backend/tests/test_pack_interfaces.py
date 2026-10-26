"""Presentation bindings preserve runtime authority and advanced registrations."""
from dataclasses import replace

import pytest

from backend.packs.interfaces import CardInterface
from backend.packs.factory_models import ButtonBinding
from backend.packs.spec import read_source_pack
from backend.plugins.declarative import declarative_plugin
from backend.plugins.registry import PluginDefinition, PluginDescriptor, PluginRegistry
from backend.plugins.resources import NodeResourceAction
from backend.tests.test_declarative_packs import project


def designed_project(tmp_path):
    root = project(tmp_path)
    manifest = root / 'oaw.pack.toml'
    manifest.write_text(manifest.read_text() + '\n[designs]\n"test.notes"="face.json"\n')
    interface = CardInterface()
    (root / 'face.json').write_text(interface.model_dump_json(), encoding='utf-8-sig')
    return root


def test_visual_interface_defaults_and_existing_resource_action_contract(tmp_path):
    spec = read_source_pack(designed_project(tmp_path))
    registry = PluginRegistry()
    registry.install(declarative_plugin(spec), source_spec=spec)
    node = registry.node_type('test.notes')
    assert node.config_model().name == '世界'
    assert node.resource_actions['run'].handler(None, {'name': 'OAW'}) == {'result': '你好，OAW！'}
    assert node.resource_actions['run'].capability_kind is None
    assert node.frontend['body'] == 'oaw.authoring-card'
    with pytest.raises(ValueError, match='未知'):
        node.resource_actions['run'].handler(None, {'unknown': 'never evaluated'})


def test_advanced_overlay_preserves_actions_lifecycle_state_and_other_frontend(tmp_path):
    spec = read_source_pack(designed_project(tmp_path))
    interface = spec.designs['test.notes'].model_copy(update={'surfaces': ['preview'], 'action': 'analyze_structure'})
    spec = spec.model_copy(update={'entrypoints': {'notes': 'oaw_notes:create_plugin'}, 'designs': {'test.notes': interface}})
    handler = NodeResourceAction(lambda context, values: {'result': values['name']}, capability_kind='notes.analyze')
    # An advanced hook augments a contribution using the existing registration API.
    captured = {}
    def register(registration):
        registration.register_capability_handler('notes.analyze', lambda *args: None)
        node = registration.nodes['test.notes']
        node = replace(node, resource_actions={'analyze_structure': handler},
                       frontend={'workspace': 'handwritten-react'}, templateable=True)
        captured['node'] = node
        registration.nodes[node.id] = node
    registry = PluginRegistry()
    registry.install(PluginDefinition(PluginDescriptor(id='test.notes', version='1.0.0', plugin_api_version='1.39'), register), source_spec=spec)
    node = registry.node_type('test.notes')
    assert node.resource_actions['analyze_structure'] is handler
    assert node.templateable and node.config_model is captured['node'].config_model
    assert node.frontend == {'preview': 'oaw.authoring-card', 'workspace': 'handwritten-react'}
    assert node.lifecycle is captured['node'].lifecycle


def test_unknown_action_fails_atomic_registration(tmp_path):
    spec = read_source_pack(designed_project(tmp_path))
    interface = spec.designs['test.notes'].model_copy(deep=True)
    interface.function.button_bindings = [ButtonBinding(mode='preview', element_id='extra', logic_id='missing')]
    # Validate the resource as read from disk, including its button references.
    interface = CardInterface.model_validate(interface.model_dump())
    spec = spec.model_copy(update={'designs': {'test.notes': interface}})
    registry = PluginRegistry()
    with pytest.raises(ValueError, match='unknown bound resource actions: missing'):
        registry.install(declarative_plugin(spec), source_spec=spec)
    assert not registry.has_plugin('test.notes')


def test_visual_resource_api_and_ownership_are_checked_statically(tmp_path):
    root = designed_project(tmp_path)
    manifest = root / 'oaw.pack.toml'
    original = manifest.read_text()
    manifest.write_text(original + '\n[compatibility]\nplugin_api="1.38"\n')
    with pytest.raises(ValueError, match='1.39'):
        read_source_pack(root)
    manifest.write_text(original.replace('"test.notes"="face.json"', '"other.card"="face.json"'))
    with pytest.raises(ValueError, match='outside the declared'):
        read_source_pack(root)


def test_public_registration_normalizes_optional_interface_defaults(tmp_path):
    spec = read_source_pack(designed_project(tmp_path))
    source = PluginRegistry()
    source.install(declarative_plugin(spec), source_spec=spec)
    definition = replace(source.node_type('test.notes'), authoring={'face': {'title': 'Python-authored interface'}})
    registry = PluginRegistry()
    registry.install(PluginDefinition(PluginDescriptor(id='test.notes', version='1.0.0', plugin_api_version='1.39'),
                                     lambda registration: registration.register_node_type(definition)))
    interface = registry.node_type('test.notes').catalog_item('test.notes').authoring
    assert interface['action'] == 'run'
    assert interface['function']['fields'][0]['key'] == 'name'
    invalid = replace(definition, authoring={'face': {'title': 'Invalid'}, 'action': 'missing'})
    with pytest.raises(ValueError, match='registered resource actions'):
        PluginRegistry().install(PluginDefinition(PluginDescriptor(id='test.notes', version='1.0.0', plugin_api_version='1.39'),
                                                 lambda registration: registration.register_node_type(invalid)))
