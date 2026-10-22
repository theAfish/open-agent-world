"""Compile passive manifest contributions into the canonical Plugin API."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, TypeAdapter, create_model


def _schema_type(schema: dict, depth=0):
    if not isinstance(schema, dict) or depth > 12:
        raise ValueError("Config schemas must be objects with at most 12 nested levels")
    allowed = {'type', 'title', 'description', 'default', 'enum', 'properties', 'required',
               'additionalProperties', 'items', 'minimum', 'maximum', 'minLength', 'maxLength',
               'minItems', 'maxItems', 'pattern', '$schema'}
    if unsupported := schema.keys() - allowed:
        raise ValueError("Unsupported declarative config keywords: " + ', '.join(sorted(unsupported)))
    kind = schema.get('type', 'object' if 'properties' in schema else None)
    if kind == 'object':
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        if not isinstance(properties, dict) or not isinstance(required, list) or set(required) - properties.keys():
            raise ValueError('Invalid config properties/required fields')
        if type(schema.get('additionalProperties', False)) is not bool:
            raise ValueError('additionalProperties must be boolean')
        fields = {}
        for key, child in properties.items():
            if not isinstance(key, str) or key.startswith('_') or not key.isidentifier():
                raise ValueError('Declarative config keys must be public Python field identifiers')
            annotation = _schema_type(child, depth + 1)
            # Palette creation needs a complete valid default, just like advanced
            # config_model(). Never invent values for a required field.
            default = child.get('default', {} if child.get('type') == 'object' else ...)
            fields[key] = (annotation, default)
        result = create_model(schema.get('title', 'CardConfig'),
            __config__=ConfigDict(extra='allow' if schema.get('additionalProperties', False) else 'forbid', validate_default=True), **fields)
    elif kind == 'array':
        result = list[_schema_type(schema.get('items'), depth + 1)]
    else:
        result = {'string': str, 'integer': int, 'number': float, 'boolean': bool, 'null': type(None)}.get(kind)
        if result is None:
            raise ValueError(f'Unsupported declarative config type: {kind}')
    if 'enum' in schema:
        values = schema['enum']
        if not isinstance(values, list) or not values or any(isinstance(v, (dict, list)) for v in values):
            raise ValueError('Config enums must contain scalar values')
        for value in values:
            TypeAdapter(result).validate_python(value, strict=True)
        result = Literal[tuple(values)]
    options = {key: schema[key] for key in ('title', 'description') if key in schema}
    constraints = {'minimum': ('ge', {'number', 'integer'}), 'maximum': ('le', {'number', 'integer'}),
                   'minLength': ('min_length', {'string'}), 'maxLength': ('max_length', {'string'}),
                   'minItems': ('min_length', {'array'}), 'maxItems': ('max_length', {'array'}),
                   'pattern': ('pattern', {'string'})}
    for key, (target, types) in constraints.items():
        if key in schema:
            if kind not in types:
                raise ValueError(f'{key} is not valid for {kind}')
            options[target] = schema[key]
    return Annotated[result, Field(**options)] if options else result


def config_model(schema: dict):
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ValueError('Card configuration must be an object schema')
    # Root metadata belongs to the model, not an Annotated wrapper.
    result = _schema_type({key: value for key, value in schema.items() if key not in {'description', 'title'}})
    result.model_config['title'] = schema.get('title', 'CardConfig')
    return result


def contribute_cards(registration, spec):
    """Populate the same staging collector before an advanced register hook."""
    from backend.plugins.registry import NodeTypeDefinition
    from backend.packs.associations import register_associations, validate_card_traits
    for card in spec.contributions.cards:
        name = card.name or spec.name
        registration.register_node_type(NodeTypeDefinition(
            id=card.id, label=name, description=card.description, icon=card.icon, color=card.color,
            deck_id=card.deck_id or spec.owner + '.cards', deck_label=card.deck_name or spec.name, deck_icon=card.icon,
            default_name=card.default_name or name, default_size=card.size,
            default_status=card.default_status or card.statuses[0],
            statuses=frozenset(card.statuses), config_model=config_model(card.config_schema), frontend=card.frontend,
            traits=validate_card_traits(spec.owner, card.traits),
            templateable=True,
            surfaces={'preview': True, 'inspector': True, 'workspace': 'workspace' in card.frontend}))
    register_associations(registration, spec.contributions.associations)


def declarative_plugin(spec):
    """Accept the same normalized Schema 3 declaration in source and wheel form."""
    from backend.packs.spec import PackSpec
    from backend.plugins.registry import PackDefinition, PluginDefinition, PluginDescriptor
    spec = spec if isinstance(spec, PackSpec) else PackSpec.model_validate(spec)
    if not spec.contributions.cards or spec.entrypoints or len(spec.packs) != 1:
        raise ValueError('Expected passive declarative cards in one Pack')
    descriptor = PluginDescriptor(id=spec.owner, version=spec.version, name=spec.name,
                                  plugin_api_version=spec.compatibility.plugin_api,
                                  requires_plugins=tuple(item.id for item in spec.dependencies),
                                  python_requirements=spec.legacy_python_requirements)

    def register(registration):
        contribute_cards(registration, spec)
        registration.register_pack(PackDefinition(id=spec.packs[0], name=spec.name,
                                                 cards=tuple(card.id for card in spec.contributions.cards)))
    return PluginDefinition(descriptor, register)
