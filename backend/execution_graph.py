"""Connected, session-scoped execution views. Execution remains owned by Runs."""
import base64
import hashlib
import json

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.plugins.execution_graph import ExecutionSnapshot, MAX_EXECUTION_BYTES, MAX_EXECUTION_NODES


def _projection(run):
    value = ExecutionSnapshot.model_validate(run.lifecycle['execution_graph'])
    value.run_id = run.run_id
    value.previous_run_id = ''
    # A conversational turn succeeding does not prove its planned work ran.
    # Reconcile an abnormally stopped worker only where it was actually active.
    if run.status.value in {'failed', 'cancelled', 'interrupted'}:
        value.phase = run.status.value
        for node in value.nodes:
            if node.status in {'running', 'created'}:
                node.status = run.status.value
    return value


def connected_sources(services, viewer_id):
    viewer = services.world.get_card(viewer_id)
    if not services.plugins.has_trait(viewer.type, 'core.execution-viewer'):
        raise PermissionDeniedError('This card is not an execution viewer')
    sources = {}
    for edge in services.world.list_edges_from(viewer_id):
        if edge.missing_plugin or edge.relationship != 'core.execution-source' or edge.direction not in {'forward', 'bidirectional'}:
            continue
        node = services.world.get_card(edge.target)
        if services.plugins.has_trait(node.type, 'core.execution-source'):
            sources[node.id] = {'id': node.id, 'name': node.name, 'type': node.type}
    return list(sources.values())


def snapshot(services, source_id, context, *, limit=None, cursor=None):
    """Return a full projection or one revision-bound transport page.

    Every invocation is authorized by the resource dispatcher. A changed source
    invalidates its cursors instead of merging pages from different snapshots.
    Typed edges travel with their target; unresolved targets travel on the last
    page so even historical external references are preserved exactly once.
    """
    value = _snapshot(services, source_id, context)
    value['history_id'] = value.get('history_id') or f"{source_id}:{services.card_state.session_id(source_id)}"
    value['mode'] = 'full'
    value['total_nodes'] = len(value['nodes'])
    value['next_cursor'] = None
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(encoded) > MAX_EXECUTION_BYTES:
        raise ResourceValidationError('Execution projection exceeds 8 MiB')
    if limit is None and cursor is None:
        return value
    if limit is None:
        limit = 500
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ResourceValidationError('Execution page limit must be between 1 and 2000')
    signature = hashlib.sha256(encoded).hexdigest()
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            token = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            offset = token['offset']
            if token['snapshot'] != signature:
                raise RevisionConflictError('Execution history changed; restart pagination')
            if type(offset) is not int or offset < 0 or offset >= len(value['nodes']):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as error:
            raise ResourceValidationError('Invalid execution history cursor') from error
    end = min(offset + limit, len(value['nodes']))
    nodes = value['nodes'][offset:end]
    ids = {node['id'] for node in nodes}
    all_ids = {node['id'] for node in value['nodes']}
    edges = [edge for edge in value['edges']
             if edge['target'] in ids or (end == len(value['nodes']) and edge['target'] not in all_ids)]
    value.update(mode='page', nodes=nodes, edges=edges)
    if end < value['total_nodes']:
        value['next_cursor'] = base64.urlsafe_b64encode(
            json.dumps({'snapshot': signature, 'offset': end}, separators=(',', ':')).encode()
        ).decode().rstrip('=')
    return value


def _snapshot(services, source_id, context):
    source = services.world.get_card(source_id)
    definition = services.plugins.node_type(source.type)
    spec = definition.execution_source
    if spec and spec.read:
        value = ExecutionSnapshot.model_validate(spec.read(context))
        if value.mode != 'full' or value.next_cursor is not None:
            raise ResourceValidationError('Execution sources must return complete snapshots')
        return value.model_dump(mode='json')
    manager = services.run_manager
    session = services.card_state.session_id(source_id)
    # Summons can have a private context ID. Their conversation belongs to the
    # durable Run ancestry, not to that private reasoning context.
    def in_session(run):
        return (manager._conversation_scope(run)[1] or 'default') == session
    if 'core.agent' in definition.traits:
        own = [run for run in manager.list_runs(agent_id=source_id) if in_session(run)]
        if not own:
            return ExecutionSnapshot().model_dump()
        # A concurrent send may create a rejected Run while the original worker
        # is still active. That bookkeeping must not replace the live graph.
        active = [run for run in own if run.status.value in {'running', 'waiting'}]
        current = active[-1] if active else own[-1]
        projected = current.lifecycle.get('execution_graph')
        if projected:
            return _projection(current).model_dump(mode='json')
        previous = next((run for run in reversed(own[:own.index(current)])
                         if run.runtime_provider_id == current.runtime_provider_id
                         and run.lifecycle.get('execution_graph', {}).get('nodes')), None)
        if previous:
            value = _projection(previous)
            value.previous_run_id = previous.run_id
            value.run_id = current.run_id
            value.phase = current.status.value
            return value.model_dump(mode='json')
        # Run-backed sources have durable activity identities already. Keep all
        # turns in this source/session instead of replacing history on each send.
        runs = [run for run in own if run.runtime_provider_id == current.runtime_provider_id]
        seen_runs = {run.run_id for run in runs}
        for parent in runs:
            if len(runs) > MAX_EXECUTION_NODES:
                break
            for run in manager.store.list_children(parent.run_id):
                if in_session(run) and run.run_id not in seen_runs:
                    seen_runs.add(run.run_id)
                    runs.append(run)
        runs.sort(key=lambda run: run.created_at)
    else:
        libraries = {source_id} if definition.summoning else set()
        if spec and spec.kind == 'summoning-adapter':
            for edge in services.world.list_edges_from(source_id):
                if edge.missing_plugin or edge.direction not in {'forward', 'bidirectional'}:
                    continue
                target = services.world.get_card(edge.target)
                summon = services.plugins.node_type(target.type).summoning
                relation = services.plugins.relationship(edge.relationship)
                if summon and any(grant.kind == summon.capability_kind for grant in relation.capabilities):
                    libraries.add(target.id)
        records = [record for record in services.summoning.records() if record['library_id'] in libraries
                   and (record.get('session_id') or 'default') == session]
        # Retain separate Run nodes for follow-up turns; retries never overwrite history.
        runs = [manager.get_run(attempt['run_id']) for record in records for attempt in record['attempts']]
        runs = [run for run in runs if in_session(run)]
    nodes = []
    for sequence, run in enumerate(runs[:MAX_EXECUTION_NODES]):
        actor = services.world.maybe_get_card(run.agent_id)
        nodes.append({'id': run.run_id, 'label': actor.name if actor else 'Reclaimed Agent', 'kind': 'agent',
                      'status': run.status.value, 'parent_id': run.parent_run_id,
                      'role': 'run', 'execution_id': run.run_id, 'sequence': sequence,
                      'actor_id': run.agent_id, 'actor_label': actor.name if actor else 'Reclaimed Agent',
                      'workstream_id': run.task_id or run.run_id, 'operation': 'run',
                      'started_at': run.started_at.isoformat() if run.started_at else None,
                      'finished_at': run.finished_at.isoformat() if run.finished_at else None,
                      'detail': run.error or (f'Task: {run.task_id}' if run.task_id else ''),
                      'result': manager.final_text(run.run_id)[:20000]})
    phase = 'running' if any(node['status'] in {'running', 'created', 'waiting'} for node in nodes) else nodes[-1]['status'] if nodes else 'idle'
    return ExecutionSnapshot(run_id=current.run_id if 'core.agent' in definition.traits else '',
        title=source.name, phase=phase, nodes=nodes, truncated=len(runs) > MAX_EXECUTION_NODES).model_dump(mode='json')


def register(registration):
    from pydantic import BaseModel, ConfigDict
    from backend.plugins.registry import NodeTypeDefinition, RelationshipDefinition
    from backend.plugins.state import ScopedStateSpec
    from backend.plugins.registry import NodePresentation, WorkspaceLayoutMetadata

    class Config(BaseModel):
        model_config = ConfigDict(extra='forbid')

    registration.register_node_type(NodeTypeDefinition(
        id='core.execution-graph', label='Agent graph', default_name='Agent graph',
        description='Observe connected Agents, callable libraries and custom execution sources.',
        icon='workflow', color='#879a9d', deck_id='tools', deck_label='Tools', deck_icon='boxes',
        default_size=(680, 460), default_status='available', statuses=frozenset({'available'}), config_model=Config,
        traits=frozenset({'core.execution-viewer'}), templateable=True,
        state=ScopedStateSpec(supported_scopes=('session',), default_scope='session'),
        presentation=NodePresentation(states=('node', 'preview', 'inspector', 'workspace'), initial='preview', open='workspace',
            sizes={'preview': {'width': 384, 'height': 440}},
            workspace_layout=WorkspaceLayoutMetadata(min_width=210, min_height=280, ideal_width=300, ideal_height=640)),
    ))
    registration.register_relationship(RelationshipDefinition(
        id='core.execution-source', label='Observe execution', short_label='execution',
        description='Read execution state from this source in the selected session. Grants no execution or control permissions.',
        source_traits=frozenset({'core.execution-viewer'}), target_traits=frozenset({'core.execution-source'}), templateable=True,
    ))
