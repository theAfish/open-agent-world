"""One selector-based operation per capability, independent of version count."""
from backend.plugins.registry import CapabilityDefinition, CapabilityGrantDefinition, CapabilitySelector, NodeTypeDefinition, RelationshipDefinition
from .artifact_models import ArtifactCollectionConfig, ArtifactMaterialize, ArtifactPublish


async def invoke(context, capability, arguments):
    return await context.artifact_action(capability, arguments)


def register(registry):
    registry.register_node_type(NodeTypeDefinition(
        id='core.artifact-collection', label='Published artifacts', description='References to retained immutable file versions',
        icon='boxes', color='#697c78', deck_id='resources', deck_label='Resources', deck_icon='file-text',
        default_name='Published artifacts', default_size=(320, 200), default_status='available',
        statuses=frozenset({'available'}), config_model=ArtifactCollectionConfig,
        traits=frozenset({'core.artifact-collection'}),
        surfaces={'preview': True, 'inspector': True, 'workspace': True}))
    sandbox = CapabilitySelector(parameter='sandbox', argument='sandbox_id', capability_kinds=frozenset({'sandbox.execute'}))
    operations = [
        ('publish', 'publish_artifact', 'Publish explicitly finalized Sandbox files as a retained immutable version.', ArtifactPublish.model_json_schema(), (sandbox,)),
        ('read', 'inspect_artifacts', 'List metadata or preview one file; version IDs alone do not grant access.',
            {'type': 'object', 'properties': {'version_id': {'type': 'string'}, 'path': {'type': 'string'}}, 'additionalProperties': False}, ()),
        ('materialize', 'materialize_artifact', 'Copy an immutable version into a new mutable workspace directory.',
            {**ArtifactMaterialize.model_json_schema(), 'properties': {**ArtifactMaterialize.model_json_schema()['properties'], 'version_id': {'type': 'string'}},
             'required': ['sandbox_id', 'destination', 'version_id']}, (sandbox,)),
        ('manage', 'release_artifact', 'Explicitly release retention and delete the stored bytes; removing a collection reference is separate.',
            {'type': 'object', 'properties': {'version_id': {'type': 'string'}}, 'required': ['version_id'], 'additionalProperties': False}, ()),
    ]
    for kind, name, description, schema, selectors in operations:
        if selectors:
            schema['properties'].pop('sandbox_id', None)
            schema['required'] = [key for key in schema.get('required', []) if key != 'sandbox_id']
        registry.register_capability(CapabilityDefinition(kind=f'artifact.{kind}', tool_name=name, target_parameter='collection',
            description=description, input_schema=schema, selectors=selectors,
            target_capabilities=frozenset({'artifact.read'}) if kind == 'materialize' else frozenset()), invoke)
    for relationship, kinds in [('read', ('read', 'materialize')), ('publish', ('read', 'materialize', 'publish')), ('manage', ('read', 'materialize', 'publish', 'manage'))]:
        registry.register_relationship(RelationshipDefinition(id=f'artifact.{relationship}', label=f'Artifact {relationship}',
            short_label=relationship, description=f'Grant artifact {relationship} operations in this collection',
            source_traits=frozenset({'core.agent'}), target_traits=frozenset({'core.artifact-collection'}),
            templateable=True,
            capabilities=tuple(CapabilityGrantDefinition(kind=f'artifact.{kind}') for kind in kinds)))
