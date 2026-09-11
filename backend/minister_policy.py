"""Minister's small ALLOW / CONFIRM / DENY policy and desktop review queue.

No transferable approval token is exposed to a tool. Confirmations retain the
exact request, expire on restart, and repeat the facade checks before execution.
"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.events import EventType
from backend.plugins.config_policy import config_write_risk
from backend.runs.models import TERMINAL_RUN_STATUSES
from backend.world.models import AgentConfig


class ReviewRequired(Exception):
    def __init__(self, review):
        self.review = review


def review_change(services, node_id, effect):
    result = summarize(services, node_id, effect)
    if result['risk'] == 'CONFIRM':
        raise ReviewRequired(result)


def _grant(card):
    return dict(created_at=card.created_at.isoformat(), position=card.position.model_dump(), size=card.size.model_dump(),
                radius=card.config['control_radius'], enabled=card.config['allow_canvas_edits'])


def default_agent_model(services):
    from backend.security.model_connections import ModelConnectionStore
    return ModelConnectionStore(services.llm_settings).read().default_model or AgentConfig().model


def summarize(services, actor_id, effect):
    from backend.minister import MINISTER_TYPE
    before = {card.id: card for card in effect['before']}
    operation = effect['operation']
    reasons, changes = [], []
    if operation == 'delete':
        reasons.append('Delete cards and their stored content; attached equipment and connections are also removed.')
        if effect.get('organization'):
            reasons.append('Remove the physical glue bonds to neighbouring cards; the neighbours are preserved.')
    if operation in {'group', 'unglue'} and any(card.type == MINISTER_TYPE for card in effect['before']):
        raise PermissionDeniedError('Minister control nodes must remain independent of canvas groups and glue')
    for card in effect['after']:
        previous = before.get(card.id)
        creating = previous is None
        if card.type == MINISTER_TYPE and (creating or previous and (
            card.position != previous.position or card.size != previous.size or any(
                card.config.get(key) != previous.config.get(key) for key in ('control_radius', 'allow_canvas_edits', 'system_instruction')))):
            raise PermissionDeniedError('Minister authority and control scopes can only be changed by the user')
        spec = services.plugins.node_type(card.type)
        if creating and spec.canvas_create_requires_confirmation:
            reasons.append(f'Create {spec.label}: initialize its plugin resources using the saved host settings.')
        original = spec.config_model().model_dump(mode='json') if creating else previous.config
        config = {key: card.config.get(key) for key in original.keys() | card.config.keys()
                  if original.get(key) != card.config.get(key)}
        if creating:
            config = {**effect.get('config', {}), **config}
        checked = dict(config)
        if creating and card.type == 'agent' and checked.get('model') == default_agent_model(services):
            checked.pop('model')  # Reuse the user's chosen default; no new service or credential grant.
        if config_write_risk(spec.config_model, checked) == 'CONFIRM':
            reasons.append(f'Change sensitive settings on {card.name}: {", ".join(checked)}.')
        if previous and (card.parent_id != previous.parent_id or card.equipment != previous.equipment):
            if card.parent_id:
                parent = services.world.get_card(card.parent_id)
                if parent.type == 'legion' and any(parent.config.get(key) for key in ('model_override', 'paused')):
                    reasons.append(f'{card.name} will inherit the model/pause settings of {parent.name}.')
        change = dict(name=card.name, type=card.type, action='create' if creating else 'update',
                      id=None if creating else card.id)
        if config:
            # Only validated, caller-supplied fields; never a full persisted config.
            change['configuration'] = config
        for field in ('name', 'position', 'size', 'parent_id', 'equipment'):
            value = getattr(card, field)
            if creating or value != getattr(previous, field):
                change[field] = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
        changes.append(change)
    for card in effect['before'] if operation == 'delete' else []:
        changes.append(dict(id=card.id, name=card.name, type=card.type, action='delete'))

    connections = []
    for edge in effect['affected_edges']:
        definition = services.plugins.relationship(edge.relationship)
        if definition.canvas_requires_confirmation:
            reasons.append(f'Affects existing access via {definition.label}: {definition.description}')
    for key, verb in (('added_edges', 'grant'), ('removed_edges', 'remove')):
        for edge in effect[key]:
            definition = services.plugins.relationship(edge.relationship)
            if actor_id in (edge.source, edge.target) and definition.canvas_requires_confirmation:
                raise PermissionDeniedError('A Minister cannot acquire additional capabilities through its own connections')
            if definition.canvas_requires_confirmation:
                reasons.append(f'{verb.capitalize()} {definition.label}: {definition.description}')
            connections.append(dict(source=edge.source, target=edge.target, relationship=edge.relationship,
                direction=edge.direction, action=verb, description=definition.description,
                capabilities=[services.plugins.capability_definition(grant.kind).description for grant in definition.capabilities]))

    cards = {card.id: card for card in [*effect['affected_cards'], *effect['before']]}
    affected = [dict(id=card.id, name=card.name, type=card.type, status=card.status,
                     revision=card.revision, created_at=card.created_at.isoformat()) for card in cards.values()]
    runs = [dict(id=run.run_id, agent_id=run.agent_id, status=str(run.status)) for card in cards.values()
            for run in (services.run_manager.list_runs(agent_id=card.id) if services.run_manager else [])
            if run.status not in TERMINAL_RUN_STATUSES]
    if runs and (operation == 'delete' or any(change.get('configuration') for change in changes)):
        reasons.append('Active runs are affected. Existing lifecycle rules may require stopping them before this change can be applied.')
    resources = []
    for card in sorted(cards.values(), key=lambda item: item.id):
        spec = services.plugins.node_type(card.type)
        item = dict(card_id=card.id, name=card.name, kind=card.type, status=card.status)
        if record := services.resources.maybe_get_record(card.id):
            # Resource revision is independent of the card revision. Do not read
            # file previews or disclose content just to review deletion/access.
            item.update(revision=record.revision, size_bytes=record.size_bytes)
        if spec.document or spec.execution:
            state = services.state.ensure_scope('node_document', card.id, schema_id='core.node_document')
            item['document_revision'] = services.state.get_record(state, 'document').revision
            item['execution_revision'] = services.state.get_record(state, 'execution').revision
        if card.type == 'conversation':
            item['sessions'] = [dict(id=session.id, title=session.title, revision=session.revision,
                last_message=max((message.sequence for message in services.conversations.list_messages(card.id, session.id, limit=1)), default=0))
                for session in services.conversations.list_sessions(card.id)]
        if len(item) > 4 or spec.execution or card.type == 'sandbox':
            resources.append(item)
    if any(card.type == 'sandbox' for card in effect['after']) and operation == 'create':
        from backend.sandbox.settings import SandboxSettingsStore
        defaults = SandboxSettingsStore(services.database, services.settings.data_root).read()
        resources.append(dict(kind='sandbox defaults', runtime=defaults.runtime,
                              workspace_root=str(defaults.workspace_root) if defaults.workspace_root else None))
    return dict(risk='CONFIRM' if reasons else 'ALLOW', reasons=list(dict.fromkeys(reasons)), changes=changes,
                affected_cards=sorted(affected, key=lambda x: x['id']), connections=connections,
                affected_edges=sorted([e.model_dump(mode='json') for e in effect['affected_edges']], key=lambda e: e['id']),
                running_resources=runs, resources=resources, organization=effect.get('organization'))


def pending_proposals(services, node_id):
    now = datetime.now(UTC)
    for key, proposal in list(services._minister_proposals.items()):
        if proposal['expires_at'] < now:
            del services._minister_proposals[key]
    return [dict(id=key, status=p['status'], expires_at=p['expires_at'].isoformat(), **p['review'])
            for key, p in services._minister_proposals.items() if p['node_id'] == node_id]


def administration_options(services, actor_id, nodes):
    from backend.minister import MINISTER_TYPE
    types = []
    for item in services.plugins.catalog().node_types:
        spec = services.plugins.node_type(item.id)
        fields = {}
        for key in spec.config_model.model_fields:
            try:
                risk = config_write_risk(spec.config_model, {key: None})
            except PermissionDeniedError:
                continue
            if item.id == MINISTER_TYPE and key in {'control_radius', 'allow_canvas_edits', 'system_instruction'}:
                continue
            schema = spec.config_model.model_json_schema()['properties'][key]
            fields[key] = dict(risk=risk, description=schema.get('description', schema.get('title', key)),
                               type=schema.get('type'), choices=schema.get('enum'))
        types.append(dict(id=item.id, label=item.label, create=(
            'DENY' if item.id == MINISTER_TYPE else 'unsupported' if not spec.user_creatable or spec.container and spec.container.document_field else
            'CONFIRM' if spec.canvas_create_requires_confirmation else 'ALLOW'), configuration=fields,
            container=item.container.model_dump(mode='json') if hasattr(item.container, 'model_dump') else item.container))
    return dict(card_types=types, risk_policy=dict(ALLOW='Execute normal local organization and configuration directly.',
        CONFIRM='Show the actual effects in the Minister panel and wait for user confirmation.',
        DENY='Secrets, security bypasses and changes to Minister authority are unavailable.'),
        default_agent_model=default_agent_model(services))


async def dispatch(services, node_id, action, request, reviewer):
    from backend.minister import control
    facade = control(services, node_id, editing=True, review=reviewer)
    if action == 'create':
        config = dict(request.config)
        if request.type == 'agent':
            config.setdefault('model', default_agent_model(services))
        return await facade.create_card(dict(type=request.type, name=request.name, position=request.position,
            size=request.size, parent_id=request.parent_id, config=config), request.versions)
    if action == 'move':
        return await facade.move_card(request.node_id, request.position, request.versions)
    if action == 'rename':
        return await facade.update_card(request.node_id, {'name': request.name}, request.versions)
    if action == 'update':
        return await facade.update_cards([item.model_dump(exclude_unset=True) for item in request.updates], request.versions)
    if action == 'delete':
        return await facade.delete_cards(request.node_ids, request.versions)
    if action == 'connect':
        return await facade.connect_cards(request.source, request.target, request.relationship, request.versions, request.direction)
    if action == 'disconnect':
        return await facade.disconnect_cards(request.edge_id, request.versions)
    if action == 'organize':
        if request.operation == 'group':
            return await facade.group_cards(request.name, request.node_ids, request.versions)
        if request.operation == 'ungroup':
            return await facade.update_cards([dict(node_id=key, patch={'parent_id': None}) for key in request.node_ids], request.versions)
        if request.operation in {'glue', 'unglue'}:
            return await facade.glue_cards(request.node_ids, request.target_id, request.side, request.versions,
                                           detach=request.operation == 'unglue')
        if len(request.node_ids) != 1:
            raise ResourceValidationError('Choose one card to attach or detach')
        if request.operation == 'attach':
            return await facade.attach_card(request.node_ids[0], request.target_id, request.relationship, request.versions)
        return await facade.detach_card(request.node_ids[0], request.versions)
    raise ResourceValidationError('This canvas operation has not been implemented')


async def execute_or_propose(services, node_id, action, request):
    from backend.minister import minister_card
    try:
        return await dispatch(services, node_id, action, request, lambda effect: review_change(services, node_id, effect))
    except ReviewRequired as pending:
        pending_proposals(services, node_id)
        if sum(p['node_id'] == node_id for p in services._minister_proposals.values()) >= 20:
            raise ResourceValidationError('Review or dismiss the pending proposals before requesting more') from None
        key = str(uuid4())
        services._minister_proposals[key] = dict(node_id=node_id, action=action, request=request.model_dump(mode='json'),
            grant=_grant(minister_card(services, node_id)), review=deepcopy(pending.review), status='pending',
            expires_at=datetime.now(UTC) + timedelta(minutes=15))
        await services.events.publish(EventType.MINISTER_REVIEW, node_id=node_id, payload={'proposal_id': key, 'status': 'pending'})
        return dict(status='confirmation_required', proposal_id=key, **pending.review,
                    message='Nothing has changed. The user can review and confirm these effects in the Minister panel.')


async def decide(services, node_id, proposal_id, approve):
    from backend.minister import TOOLS, minister_card
    async with services._node_mutation():
        pending_proposals(services, node_id)
        proposal = services._minister_proposals.get(proposal_id)
        if not proposal or proposal['node_id'] != node_id or proposal['status'] != 'pending':
            raise RevisionConflictError('This proposal expired or was already reviewed; inspect and propose again')
        # Consume before execution: an ambiguous failure must never be retried by
        # replaying the same approval against partly completed lifecycle effects.
        proposal['status'] = 'rejected' if not approve else 'applying'
        try:
            if approve:
                if _grant(minister_card(services, node_id)) != proposal['grant']:
                    raise RevisionConflictError('Minister scope or authority changed; inspect and propose again')
                def review(effect):
                    if summarize(services, node_id, effect) != proposal['review']:
                        raise RevisionConflictError('Consequential effects changed; inspect and propose again')
                request = TOOLS[proposal['action']][0].model_validate(proposal['request'])
                await dispatch(services, node_id, proposal['action'], request, review)
                proposal['status'] = 'applied'
        except BaseException:
            proposal['status'] = 'failed'
            raise
        finally:
            await services.events.publish(EventType.MINISTER_REVIEW, node_id=node_id,
                payload={'proposal_id': proposal_id, 'status': proposal['status']})
        return {'id': proposal_id, 'status': proposal['status']}
