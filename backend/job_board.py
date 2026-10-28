"""Connected, session-scoped views of the host's durable background jobs."""
from backend.errors import ConflictError, NotFoundError, PermissionDeniedError, PluginUnavailableError, RuntimeUnavailableError
from backend.background_jobs import ACTIVE
from backend.plugins.background_jobs import JobControl


def sources(services, board_id):
    board = services.world.get_card(board_id)
    services.world.require_available_card(board)
    if not services.plugins.has_trait(board.type, 'core.jobs-viewer'):
        raise PermissionDeniedError('This card is not a Jobs board')
    result = {}
    for edge in services.world.list_edges_from(board_id):
        if edge.missing_plugin or edge.direction not in {'forward', 'bidirectional'}:
            continue
        if edge.relationship not in {'core.jobs-source', 'core.jobs-manage'}:
            continue
        source = services.world.get_card(edge.target)
        if source.missing_plugin or not services.plugins.has_trait(source.type, 'core.agent'):
            continue
        old = result.get(source.id, {})
        result[source.id] = dict(id=source.id, name=source.name, type=source.type,
            can_manage=old.get('can_manage', False) or edge.relationship == 'core.jobs-manage')
    return result


def in_session(services, job, session):
    run = services.run_manager.get_run(job['source_run_id'])
    return (services.run_manager._conversation_scope(run)[1] or 'default') == session


def actions(services, job, can_manage):
    if not can_manage or job.get('tracking_released') or job['id'] in services.background_jobs.submissions:
        return []
    provider = services.plugins.background_job_provider(job['provider'])
    result = ['refresh'] if job['external_id'] else []
    if job['status'] in ACTIVE | {'unknown'} or job['wake_requested']:
        result.append('resume' if job['paused'] else 'pause')
    if job['external_id'] and job['status'] in ACTIVE | {'unknown'} and provider.cancel:
        result.append('cancel')
    return result


def project(services, job, source):
    sandbox = services.world.get_card(job['sandbox_id'])
    return {**services.background_jobs.public(job),
        **{key: job.get(key) for key in ('revision', 'created_at', 'updated_at', 'source_run_id', 'tracking_released')},
        'agent_id': job['agent_id'], 'agent_name': source['name'], 'sandbox_name': sandbox.name,
        'actions': actions(services, job, source['can_manage'])}


def listing(services, board_id):
    connected = sources(services, board_id)
    session = services.card_state.session_id(board_id)
    jobs = []
    for job in services.background_jobs.records():
        source = connected.get(job['agent_id'])
        if source is None:
            continue
        try:
            if not in_session(services, job, session):
                continue
            services.background_jobs.authorize(job)
            jobs.append(project(services, job, source))
        except (PermissionDeniedError, NotFoundError, RuntimeUnavailableError, PluginUnavailableError):
            continue
    jobs.sort(key=lambda job: (job['created_at'], job['id']), reverse=True)
    return {'sources': list(connected.values()), 'jobs': jobs}


def require_job(services, board_id, job_id):
    connected = sources(services, board_id)
    job = services.background_jobs.get(job_id)
    source = connected.get(job['agent_id'])
    if source is None or not source['can_manage'] or not in_session(services, job, services.card_state.session_id(board_id)):
        raise PermissionDeniedError('Connect this Jobs board to the owning Agent with Manage jobs in this session')
    services.background_jobs.authorize(job)
    return job, source


async def control(services, board_id, job_id, action, revision):
    job, source = require_job(services, board_id, job_id)
    if job['revision'] != revision:
        raise ConflictError('This job changed; refresh before applying the action')
    if action not in actions(services, job, source['can_manage']):
        raise PermissionDeniedError('This action is unavailable for the current job')
    if action == 'refresh':
        await services.background_jobs.probe(job)
    else:
        await services.background_jobs.control_record(job, JobControl(job_id=job_id, action=action),
            reauthorize=lambda: require_job(services, board_id, job_id))
    # Connections and session ownership can change during a provider request.
    job, source = require_job(services, board_id, job_id)
    return project(services, job, source)


def register(registration):
    from pydantic import BaseModel, ConfigDict
    from backend.plugins.registry import NodeTypeDefinition, NodePresentation, WorkspaceLayoutMetadata, RelationshipDefinition
    from backend.plugins.state import ScopedStateSpec

    class Config(BaseModel):
        model_config = ConfigDict(extra='forbid')

    registration.register_node_type(NodeTypeDefinition(
        id='core.jobs', label='Jobs', default_name='Jobs',
        description='Track background jobs from connected Agents in the selected session.',
        icon='server', color='#879a9d', deck_id='tools', deck_label='Tools', deck_icon='boxes',
        default_size=(680, 460), default_status='available', statuses=frozenset({'available'}), config_model=Config,
        traits=frozenset({'core.jobs-viewer'}), templateable=True,
        state=ScopedStateSpec(supported_scopes=('session',), default_scope='session'),
        presentation=NodePresentation(states=('node', 'preview', 'inspector', 'workspace'), initial='preview', open='workspace',
            sizes={'preview': {'width': 320, 'height': 336}},
            workspace_layout=WorkspaceLayoutMetadata(min_width=210, min_height=250, ideal_width=340, ideal_height=480)),
    ))
    for name, label, description in (
        ('source', 'Observe jobs', 'Read background jobs in the selected session.'),
        ('manage', 'Manage jobs', 'Read jobs, pause or resume monitoring, refresh status and explicitly cancel external work.'),
    ):
        registration.register_relationship(RelationshipDefinition(
            id=f'core.jobs-{name}', label=label, short_label=label,
            description=description, source_traits=frozenset({'core.jobs-viewer'}),
            target_traits=frozenset({'core.agent'}), templateable=True,
        ))
