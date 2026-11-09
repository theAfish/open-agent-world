"""Let a connected Agent import an experimental pattern through the spectrum node's own import action."""
import base64

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition, ResourceValidationError,
)

KIND = 'xrd.pattern.import'


class PatternImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=200, description='Name to record, for example the converted source file.')
    text: str = Field(min_length=1, max_length=8 * 1024 * 1024,
        description='Two columns, 2theta in degrees (strictly ascending, 0-180) and non-negative intensity, '
                    'one point per line, separated by comma or whitespace; 20-200000 points. '
                    'Convert other formats (xy, xye, xrdml, raw) to this first.')


def register_pattern_import(registration):
    async def import_pattern(context, capability, arguments):
        try:
            request = PatternImport.model_validate(arguments)
        except ValidationError as exc:
            raise ResourceValidationError(str(exc)) from exc
        current = await context.node_document_action(capability, 'inspect_import', {})
        document = await context.node_document_action(
            capability, 'import',
            {'filename': request.filename, 'source_base64': base64.b64encode(request.text.encode('utf-8')).decode()},
            expected_revision=current['revision'])
        value = document['value']
        angles = [point[0] for point in value['points']]
        return {'filename': value['filename'], 'sha256': value['sha256'], 'points': len(angles),
                'two_theta_range': [min(angles), max(angles)],
                'note': '已导入并保留原始文本与哈希。旧检索结果仍基于先前的实验谱；请重新运行 search 阶段。'}

    registration.register_capability(CapabilityDefinition(
        kind=KIND, tool_name='xrd_import_pattern', input_schema=PatternImport.model_json_schema(),
        description='Replace the connected XRD spectrum node\'s experimental pattern with two-column 2theta/intensity '
                    'text. The pattern is parsed and validated like a UI import; earlier search runs are not updated, '
                    'so run the search stage again afterwards.'), import_pattern)
    registration.register_relationship(RelationshipDefinition(
        id='xrd.pattern-import', label='导入实验谱', short_label='导入',
        description='允许 Agent 向实验谱节点导入两列文本谱',
        source_traits=frozenset({'core.agent'}), target_types=frozenset({'xrd.spectrum-canvas', 'xrd.pattern'}),
        capabilities=(CapabilityGrantDefinition(kind=KIND),), templateable=True))
