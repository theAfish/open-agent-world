"""Session/group directories over the existing Sandbox root and Run lifecycle.

Historical task/root bindings remain readable for explicit continuations.
The host binds scope identity here; native runtimes enforce its write boundary.
"""
from backend.errors import NotFoundError
from .files import parts
from .models import SandboxValidationError


def initial_policy(store, *, agent_id, run_id, caller_kind, task_id=None, lifecycle=None, context_id=None):
    lifecycle = lifecycle or {}
    source_id = lifecycle.get("continuation_run_id")
    if source_id:
        source = store.get(source_id)
        if source.agent_id != agent_id:
            raise SandboxValidationError("Task continuation must belong to the same Agent")
        if source.context_id != context_id:
            return {"mode": "workspace", "origin_run_id": run_id}
        return {**(source.lifecycle.get("task_workspace") or {"mode": "legacy", "origin_run_id": source.run_id}),
                "source_run_id": source.run_id}
    # File grouping follows session/group, never a task-board item ID. Only an
    # explicit continuation inherits a historical binding across scope changes.
    return {"mode": "workspace", "origin_run_id": run_id}


def binding(services, sandbox_id, agent_id=None):
    context = services.run_manager.current_context if services.run_manager else None
    if context is None:
        from backend.card_state import active_state_session
        if active_state_session.get() is None:
            # Historical desktop/API callers without a viewing context keep
            # their explicit root semantics. The UI supplies its session.
            return {'mode': 'legacy', 'directory': ''}
    if context is None or agent_id is None or context.agent_id != agent_id:
        from backend.workspace_context import resolve_workspace
        return {"mode": "workspace", **resolve_workspace(services, sandbox_id, sandbox_id=sandbox_id)}
    with services.database.transaction(immediate=True):
        run = services.run_manager.get_run(context.run_id)
        saved = run.lifecycle.get("sandbox_workspaces", {})
        if sandbox_id in saved:
            return dict(saved[sandbox_id])
        policy = run.lifecycle.get("task_workspace") or {"mode": "legacy", "origin_run_id": run.run_id}
        directory = ""
        mode = policy["mode"]
        scoped = {}
        if mode == 'workspace':
            from backend.workspace_context import resolve_workspace
            scoped = resolve_workspace(services, sandbox_id, sandbox_id=sandbox_id)
            directory = scoped['directory']
        if mode == "task":
            origin = policy["origin_run_id"]
            if len(parts(origin)) != 1:
                raise SandboxValidationError("Invalid task directory identity")
            if services.world.get_card(sandbox_id).config.get("workspace_access") == "read_only":
                mode = "read_only"
            else:
                directory = "tasks/" + origin
        value = {**scoped, "mode": mode, "directory": directory, "origin_run_id": policy.get("origin_run_id"),
                 "task_id": run.task_id, "root_run_id": run.root_run_id}
        inherited = saved_binding(services, run, sandbox_id)
        if inherited is not None:
            value = {**inherited, "task_id": run.task_id, "root_run_id": run.root_run_id}
        services.run_manager.store.update_lifecycle(run.run_id, sandbox_workspaces={**saved, sandbox_id: value})
        return value


def saved_binding(services, run, sandbox_id):
    """Resolve an explicit continuation even if its new Run has not used a Sandbox."""
    seen = set()
    while run.run_id not in seen:
        seen.add(run.run_id)
        value = run.lifecycle.get("sandbox_workspaces", {}).get(sandbox_id)
        if value is not None:
            return dict(value)
        source = run.lifecycle.get("task_workspace", {}).get("source_run_id") or run.lifecycle.get("continuation_run_id")
        if not source:
            break
        try:
            parent = services.run_manager.get_run(source)
        except NotFoundError:
            break
        if parent.agent_id != run.agent_id or parent.context_id != run.context_id:
            break
        run = parent
    return None


def path_for(directory, path):
    relative = "/".join(parts(path))
    return "/".join(p for p in (directory, relative) if p)


def task_path(services, sandbox_id, path, agent_id=None):
    return path_for(binding(services, sandbox_id, agent_id)["directory"], path)


def record_shared_input(services, sandbox_id, path, agent_id, *, root="shared"):
    context = services.run_manager.current_context if services.run_manager else None
    if context is None or context.agent_id != agent_id:
        return
    with services.database.transaction(immediate=True):
        value = binding(services, sandbox_id, agent_id)
        inputs = list(value.get("shared_inputs", []))
        canonical = path_for(root, path)
        if canonical not in inputs:
            inputs.append(canonical)
            run = services.run_manager.get_run(context.run_id)
            saved = run.lifecycle.get("sandbox_workspaces", {})
            services.run_manager.store.update_lifecycle(run.run_id,
                sandbox_workspaces={**saved, sandbox_id: {**value, "shared_inputs": inputs}})


async def ensure_directory(services, sandbox_id, agent_id=None):
    value = binding(services, sandbox_id, agent_id)
    from backend.workspace_context import claim_directory
    return await claim_directory(services, sandbox_id, value)


def list_workspaces(services, sandbox_id):
    groups = {}
    for run in services._require_run_manager().list_runs():
        value = run.lifecycle.get("sandbox_workspaces", {}).get(sandbox_id)
        if not value or not value.get("directory") or not value.get('claimed', True):
            continue
        key = value["directory"]
        if key not in groups:
            title = value.get('label')
            try:
                if value.get('session_id') and value.get('conversation_id'):
                    session = services.conversations.get_session(value['conversation_id'], value['session_id'])
                    title = session.group_title if value.get('scope_type') == 'group' else session.title
                elif run.caller_kind == "conversation" and run.caller_id and run.context_id:
                    title = services.conversations.get_session(run.caller_id, run.context_id).title
            except NotFoundError:
                pass
            card = services.world.maybe_get_card(run.agent_id)
            groups[key] = {"directory": key, "origin_run_id": value["origin_run_id"],
                "label": (title if isinstance(title, str) and title.strip()
                          else (card.name if card else "Task")),
                "run_ids": [], "task_id": run.task_id, 'statuses': [],
                'scope_type': value.get('scope_type'), 'scope_id': value.get('scope_id')}
        groups[key]["run_ids"].append(run.run_id)
        groups[key]['statuses'].append(run.status.value)
        active = [status for status in groups[key]['statuses'] if status in {'created', 'running', 'waiting'}]
        groups[key]['status'] = 'running' if active else run.status.value
        groups[key]['active_runs'] = len(active)
    from backend.workspace_context import _records
    _, records = _records(services, sandbox_id)
    for value in records['sessions'].values():
        directory = value.get('directory')
        if directory and value.get('claimed'):
            groups.setdefault(directory, {**value, 'run_ids': [], 'status': 'ready'})
    # Manual commands use the same native receipt journal without creating a
    # Run. Their session/group directories must also remain selectable.
    from .history import read
    for receipt in reversed(read(services, sandbox_id)):
        directory = receipt.get('working_directory', '')
        if not isinstance(directory, str):
            continue
        if directory in groups:
            if receipt.get('state') == 'running':
                groups[directory]['status'] = 'running'
            continue
        pieces = directory.split('/')
        if len(pieces) != 2 or pieces[0] not in {'sessions', 'groups'}:
            continue
        kind = 'session' if pieces[0] == 'sessions' else 'group'
        with services.database.locked() as db:
            table = 'conversation_sessions' if kind == 'session' else 'conversation_groups'
            row = db.execute(f'SELECT title FROM {table} WHERE id=?', (pieces[1],)).fetchone()
        label = row['title'] if row else ('Default ' + kind if pieces[1] == 'default' else directory)
        groups[directory] = {'directory': directory, 'label': label, 'scope_type': kind,
            'scope_id': pieces[1], 'run_ids': [], 'status': receipt.get('state', 'unknown')}
    return list(reversed(list(groups.values())))
