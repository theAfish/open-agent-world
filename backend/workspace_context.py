"""Host resolution of session/group workspaces over existing Conversation IDs."""
import json

from backend.card_state import DEFAULT_SESSION, active_state_session
from backend.errors import ResourceValidationError


def conversation_sources(services, card_id):
    """Conversation sources reachable through the native Agent grants."""
    card = services.world.get_card(card_id)
    if services.plugins.has_trait(card.type, 'core.agent'):
        agents = [card]
    else:
        sandbox = card_id if services.plugins.has_trait(card.type, 'core.sandbox') else sandbox_for_card(services, card_id)
        agents = []
        for actor in services.world.list_cards():
            if not services.plugins.has_trait(actor.type, 'core.agent') or actor.missing_plugin:
                continue
            capabilities = services.capabilities.derive(actor.id).capabilities
            if any(cap.target_id == (sandbox or card_id) and (sandbox is None or cap.kind == 'sandbox.execute')
                   for cap in capabilities):
                agents.append(actor)
    return {cap.target_id for actor in agents for cap in services.capabilities.derive(actor.id).capabilities
        if cap.kind == 'conversation.send_message'}


def _owner_session(services, card_id):
    for owner in services.world.ancestors(services.world.get_card(card_id)):
        if owner.config.get('session_id'):
            return owner.config['session_id']
    return None


def session_context(services, card_id, session_id=None, *, allow_unbound=False):
    invocation = services.run_manager.current_context if services.run_manager else None
    # A standalone Run must not inherit a browser's selected Conversation.
    if session_id is not None:
        selected = session_id
    elif invocation is not None:
        selected = invocation.context_id
    else:
        selected = active_state_session.get()
    sources = None
    if selected in (None, DEFAULT_SESSION) and invocation is None and session_id is None:
        selected = _owner_session(services, card_id)
        sources = conversation_sources(services, card_id)
        if selected is None and len(sources) == 1:
            conversation = next(iter(sources))
            selected = next((item.id for item in services.conversations.list_sessions(conversation) if item.is_default), None)
        elif selected is None and len(sources) > 1:
            if allow_unbound:
                return None
            raise ResourceValidationError('Select a connected Conversation session before using this workspace')
    if selected and selected != DEFAULT_SESSION:
        with services.database.locked() as db:
            row = db.execute('''SELECT s.id, s.conversation_id, s.group_id, s.title, g.title AS group_title
                FROM conversation_sessions s JOIN conversation_groups g ON g.id=s.group_id WHERE s.id=?''', (selected,)).fetchone()
        if row:
            if invocation is None and session_id is None and selected != _owner_session(services, card_id):
                sources = sources if sources is not None else conversation_sources(services, card_id)
                if row['conversation_id'] not in sources:
                    raise ResourceValidationError('This Conversation is not connected to the workspace through an Agent')
            return dict(row)
        # A model context is not necessarily a Conversation session. Resolve
        # its native Run lineage; explicit selections and deleted Conversation
        # sessions still fail instead of falling back to another workspace.
        run = services.run_manager.get_run(invocation.run_id) if invocation is not None and session_id is None else None
        conversation_session = services.run_manager._conversation_scope(run)[1] if run else selected
        if run is None or conversation_session == selected:
            raise ResourceValidationError('The selected conversation session no longer exists')
    return {'id': DEFAULT_SESSION, 'conversation_id': None, 'group_id': DEFAULT_SESSION,
        'title': 'Default session', 'group_title': 'Default group'}


def sandbox_for_card(services, card_id, *, use_invocation=True):
    card = services.world.get_card(card_id)
    if services.plugins.has_trait(card.type, 'core.sandbox'):
        return card_id
    direct = [edge.target for edge in services.world.connections_from(card_id)
        if edge.relationship == 'core.file-preview'
        and services.plugins.has_trait(services.world.get_card(edge.target).type, 'core.sandbox')]
    if direct:
        candidates = set(direct)
    else:
        invocation = services.run_manager.current_context if services.run_manager and use_invocation else None
        agents = {invocation.agent_id} if invocation else {edge.source for edge in services.world.connections_to(card_id)
            if services.plugins.has_trait(services.world.get_card(edge.source).type, 'core.agent')}
        candidates = {cap.target_id for agent in agents for cap in services.capabilities.derive(agent).capabilities
            if cap.kind == 'sandbox.execute'}
        if invocation:
            run = services.run_manager.get_run(invocation.run_id)
            bound = candidates & set(run.lifecycle.get('sandbox_workspaces', {}))
            if len(bound) == 1:
                return next(iter(bound))
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ResourceValidationError('Multiple Sandboxes are connected. Connect this viewer directly to one file source.')
    return next(iter(candidates))


def _records(services, sandbox_id):
    scope = services.state.ensure_scope('node_document', sandbox_id)
    value = services.state.get(scope, 'workspace_bindings')
    if value.get('initialized'):
        return scope, value
    value = {'initialized': True, 'sessions': {}, 'groups': {}}
    # Preserve previously used directories, including runs from the ID-directory
    # implementation. No existing files or document namespaces are moved.
    for run in services.run_manager.list_runs():
        saved = run.lifecycle.get('sandbox_workspaces', {}).get(sandbox_id)
        session_id = (saved or {}).get('session_id') or run.context_id
        if not saved or not session_id:
            continue
        try:
            session = session_context(services, sandbox_id, session_id)
        except ResourceValidationError:
            continue
        if session['id'] != session_id:
            continue
        kind = saved.get('scope_type') or ('shared' if not saved.get('directory') else 'session')
        identity = saved.get('scope_id') or ('*' if kind == 'shared' else session['group_id'] if kind == 'group' else session_id)
        record = {'directory': saved['directory'], 'label': saved.get('label', session['title']), 'scope_type': kind, 'scope_id': identity, 'claimed': saved.get('claimed', True),
            'session_id': session_id, 'group_id': saved.get('group_id', session['group_id']), 'conversation_id': session['conversation_id']}
        record['key'] = saved.get('key') or json.dumps([kind, None if kind == 'shared' else session['conversation_id'], identity], separators=(',', ':'))
        value['sessions'][session_id] = record
        if kind == 'group':
            value['groups'][identity] = record
    services.state.set(scope, 'workspace_bindings', value)
    return scope, value


def _folder_name(title, identity, records):
    import re
    import unicodedata
    from backend.resources.manager import validate_filename
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', unicodedata.normalize('NFC', title)).strip(' .')
    name = name.encode('utf-8', errors='ignore')[:180].decode('utf-8', errors='ignore').strip(' .') or 'Workspace'
    try:
        validate_filename(name)
    except ResourceValidationError:
        name = '_' + name
    used = {record['directory'].casefold() for record in records['sessions'].values() if record.get('directory')}
    if name.casefold() in used or name.casefold() in {'shared', 'sessions', 'groups', 'tasks', 'resources', '_oaw_profile'}:
        name += ' (' + identity[:8] + ')'
    return name


def _assign(session, mode, records):
    saved = records['sessions'].get(session['id'])
    if saved is not None:
        # The scope survives organization changes; its unused pathname is only
        # a proposal. Freeze that path at native directory claim, not at GET.
        mode = saved['scope_type']
    identity = '*' if mode == 'shared' else session['group_id'] if mode == 'group' else session['id']
    if saved:
        identity = saved['scope_id']
    group = records['groups'].get(identity) if mode == 'group' else None
    title = session['group_title'] if mode == 'group' else session['title']
    if mode == 'group' and identity != session['group_id']:
        title = (saved or group)['label']  # Retain a historical group's identity.
    value = dict(saved or group) if saved or group else {
        'scope_type': mode, 'scope_id': identity, 'directory': '',
        'claimed': mode == 'shared',
        'key': json.dumps([mode, None if mode == 'shared' else session['conversation_id'], identity], separators=(',', ':')),
    }
    if not value.get('claimed', True):
        other = {'sessions': {key: item for key, item in records['sessions'].items() if item['key'] != value['key']}}
        value['directory'] = _folder_name(title, identity, other)
        for item in [*records['sessions'].values(), *records['groups'].values()]:
            if item.get('key') == value['key'] and not item.get('claimed', True):
                item.update(directory=value['directory'], label=title)
    value.update(session_id=session['id'], conversation_id=session['conversation_id'], group_id=session['group_id'], label=title)
    records['sessions'][session['id']] = value
    if mode == 'group':
        records['groups'][identity] = value
    return dict(value)


def preserve_sessions_on_change(services, current, updated):
    if not services.plugins.has_trait(current.type, 'core.sandbox'):
        return
    old = current.config.get('workspace_scope', 'session')
    if old == updated.config.get('workspace_scope', 'session'):
        return
    scope, records = _records(services, current.id)
    conversations = conversation_sources(services, current.id)
    # Only actual Run/command evidence can extend the current graph to former
    # sources. Old speculative metadata is not evidence of a relationship.
    historical_sessions = {run.context_id for run in services.run_manager.list_runs()
        if current.id in run.lifecycle.get('sandbox_workspaces', {}) and run.context_id}
    from backend.sandbox.history import read
    historical_sessions.update(item['session_id'] for item in read(services, current.id) if item.get('session_id'))
    with services.database.locked() as db:
        rows = db.execute('SELECT id,conversation_id FROM conversation_sessions ORDER BY created_at,id').fetchall()
        conversations.update(row['conversation_id'] for row in rows if row['id'] in historical_sessions)
        identities = [row['id'] for row in rows if row['conversation_id'] in conversations]
    for identity in identities:
        _assign(session_context(services, current.id, identity), old, records)
    services.state.set(scope, 'workspace_bindings', records)


def resolve_workspace(services, card_id, *, sandbox_id=None, session_id=None, allow_unbound=False):
    sandbox_id = sandbox_id or sandbox_for_card(services, card_id, use_invocation=session_id is None)
    session = session_context(services, card_id, session_id, allow_unbound=allow_unbound)
    mode = services.world.get_card(sandbox_id).config.get('workspace_scope', 'session') if sandbox_id else 'session'
    if session is None:
        return {'bound': False, 'directory': '', 'writable_directory': '', 'label': 'No session selected',
            'key': json.dumps(['unbound', sandbox_id]), 'scope_type': mode, 'scope_id': DEFAULT_SESSION,
            'session_id': DEFAULT_SESSION, 'group_id': DEFAULT_SESSION, 'conversation_id': None, 'sandbox_id': sandbox_id}
    with services.database.transaction(immediate=True):
        if sandbox_id:
            scope, records = _records(services, sandbox_id)
            value = _assign(session, mode, records)
            if records != services.state.get(scope, 'workspace_bindings'):
                services.state.set(scope, 'workspace_bindings', records)
        else:
            value = _assign(session, mode, {'sessions': {}, 'groups': {}})
    read_only = bool(sandbox_id and services.world.get_card(sandbox_id).config.get('workspace_access') == 'read_only')
    if read_only:
        value['directory'] = ''
    if session['id'] == DEFAULT_SESSION:
        value['label'] = 'Standalone workspace'
    value['writable_directory'] = '' if read_only else None if value['scope_type'] == 'shared' else value['directory']
    value['sandbox_id'] = sandbox_id
    return value


async def claim_directory(services, sandbox_id, value):
    directory = value.get('directory', '')
    if not directory:
        return value
    backend = services._require_sandbox_backend()
    scope, records = _records(services, sandbox_id)
    stored = records['sessions'].get(value.get('session_id'))
    if stored and stored.get('key') == value.get('key'):
        session = session_context(services, sandbox_id, value['session_id'])
        # A Run may have inspected this workspace before the session was named.
        # Reconcile its unclaimed proposal with the current title/peer claim.
        value = {**value, **_assign(session, stored['scope_type'], records)}
        directory = value['directory']
        if value.get('writable_directory'):
            value['writable_directory'] = directory
        stored = records['sessions'][session['id']]
    if not stored or stored.get('directory') != directory or stored.get('claimed', True):
        await backend.file_operation(sandbox_id, 'mkdir', path=directory)
    else:
        original = directory
        suffix = value.get('scope_id', 'workspace')[:8]
        index = 0
        while True:
            try:
                await backend.file_operation(sandbox_id, 'transfer_mkdir', path=directory)
                break
            except FileExistsError:
                index += 1
                directory = original + ' (' + suffix + (f'-{index}' if index > 1 else '') + ')'
    # Allocation and native mkdir occur under the existing node mutation gate.
    # A collided pre-existing folder is never adopted or made writable.
    for entry in [*records['sessions'].values(), *records['groups'].values()]:
        if entry.get('key') == value.get('key') and not entry.get('claimed', True):
            entry.update(directory=directory, claimed=True)
    services.state.set(scope, 'workspace_bindings', records)
    value = {**value, 'directory': directory, 'claimed': True,
        'writable_directory': directory if value.get('writable_directory') else value.get('writable_directory')}
    for run in services.run_manager.list_runs():
        bindings = run.lifecycle.get('sandbox_workspaces', {})
        saved = bindings.get(sandbox_id)
        if saved and saved.get('key') == value.get('key') and not saved.get('claimed', True):
            updated = {**saved, 'directory': directory, 'claimed': True, 'label': value.get('label', saved.get('label'))}
            if updated.get('writable_directory'):
                updated['writable_directory'] = directory
            services.run_manager.store.update_lifecycle(run.run_id, sandbox_workspaces={**bindings, sandbox_id: updated})
    return value


def assert_workspace_write(services, sandbox_id, *paths):
    # Context-free desktop API callers retain their explicit host authority.
    # Browser and Agent requests carry a native session or Run context.
    invocation = services.run_manager.current_context
    if invocation is None and active_state_session.get() is None:
        return
    from backend.sandbox.task_workspace import binding
    from backend.sandbox.files import parts
    from backend.errors import PermissionDeniedError
    value = binding(services, sandbox_id, invocation.agent_id if invocation else None)
    allowed = value.get('writable_directory', value.get('directory') or None)
    for path in paths:
        canonical = '/'.join(parts(path))
        if allowed is not None and (not allowed or not (canonical == allowed or canonical.startswith(allowed + '/'))):
            raise PermissionDeniedError('Other workspace files are read only for this session')


def document_workspace(services, card_id):
    sandbox_id = sandbox_for_card(services, card_id)
    invocation = services.run_manager.current_context if services.run_manager else None
    if invocation and sandbox_id:
        # Freeze the same binding used by execution before either files or a
        # result document is touched. Switching the browser never redirects it.
        from backend.sandbox.task_workspace import binding
        saved = binding(services, sandbox_id, invocation.agent_id)
        if saved.get('scope_type') in {'shared', 'session', 'group'}:
            return saved
    return resolve_workspace(services, card_id, sandbox_id=sandbox_id)
