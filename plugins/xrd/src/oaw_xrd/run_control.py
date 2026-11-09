"""Bounded run control for the XRD match stages, available to any connected Agent.

The tools set only the documented stage options and start the node's normal run. Run
provenance (owned runs, candidate lineage, preoptimization) is still validated by the
pipeline when the run starts, so an Agent cannot bypass the Run button's guarantees.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition, ResourceValidationError,
)

CLAIM_SCOPE = '运行完成不代表收敛，也不能单独确认物相；请用 xrd_read_results 读取真实证据。'


class StageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: Literal['search', 'preopt', 'fit']
    match_run_id: str = Field(default='', max_length=128, description='Completed search run; required for preopt and fit.')
    preopt_run_id: str = Field(default='', max_length=128, description='Completed preopt run; required for fit.')
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=10,
        description='Candidates from that search run; required for preopt and fit.')
    wavelength: float | None = Field(default=None, gt=.1, le=5)
    tolerance_deg: float | None = Field(default=None, gt=0, le=2)
    library_elements: str | None = Field(default=None, max_length=300)
    library_top_n: int | None = Field(default=None, ge=1, le=100)
    library_engine: Literal['qualx', 'native'] | None = None


_OPTIONS = ('wavelength', 'tolerance_deg', 'library_elements', 'library_top_n', 'library_engine')
_NO_ARGUMENTS = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def stage_config(arguments):
    try:
        request = StageRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ResourceValidationError(str(exc)) from exc
    if request.stage != 'search' and not (request.match_run_id and request.selected_candidate_ids):
        raise ResourceValidationError('preopt 与 fit 需要 match_run_id 和 selected_candidate_ids。')
    if request.stage == 'fit' and not request.preopt_run_id:
        raise ResourceValidationError('fit 需要 preopt_run_id。')
    config = {'workflow_stage': request.stage}
    if request.stage != 'search':
        config.update(workflow_match_run_id=request.match_run_id, selected_candidate_ids=request.selected_candidate_ids)
    if request.stage == 'fit':
        config['workflow_preopt_run_id'] = request.preopt_run_id
    config.update({name: getattr(request, name) for name in _OPTIONS if getattr(request, name) is not None})
    return config


def register_run_control(registration):
    async def start(context, capability, arguments):
        result = await context.node_run_action(capability, 'start', {'config': stage_config(arguments)})
        return {**result, 'note': CLAIM_SCOPE}

    async def stop(context, capability, arguments):
        if arguments:
            raise ResourceValidationError('停止工具不接受参数。')
        return await context.node_run_action(capability, 'stop', {})

    async def status(context, capability, arguments):
        if arguments:
            raise ResourceValidationError('状态工具不接受参数。')
        return await context.node_run_action(capability, 'read', {})

    definitions = (
        ('xrd.run.start', 'xrd_run_stage', start, StageRequest.model_json_schema(),
         'Start one XRD stage on the connected match node: search (reference/library matching), preopt (candidate '
         'structure preoptimization) or fit (whole-pattern fit). Returns a run ID immediately; poll xrd_run_status. '
         'Later stages need run IDs and candidates from earlier completed stages.'),
        ('xrd.run.stop', 'xrd_run_stop', stop, _NO_ARGUMENTS, 'Stop the connected match node and its active runs.'),
        ('xrd.run.read', 'xrd_run_status', status, _NO_ARGUMENTS,
         'Read the connected match node status and its latest run ID and state. Use xrd_read_results for evidence.'),
    )
    for kind, name, handler, schema, description in definitions:
        registration.register_capability(
            CapabilityDefinition(kind=kind, tool_name=name, description=description, input_schema=schema), handler)
    registration.register_relationship(RelationshipDefinition(
        id='xrd.run-control', label='运行控制', short_label='运行',
        description='允许 Agent 启动、停止并查看检索、预优化与拟合阶段，并只读结果（一对节点只能有一条连接）',
        source_traits=frozenset({'core.agent'}), target_types=frozenset({'xrd.match'}),
        capabilities=(*(CapabilityGrantDefinition(kind=kind) for kind, *_ in definitions),
                      CapabilityGrantDefinition(kind='xrd.results.read')), templateable=True))
