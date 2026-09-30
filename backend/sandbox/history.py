"""Bounded host-only command receipts. Portable node state never carries these."""
import json

from .models import SandboxStateError


def key(services, sandbox_id):
    node = services.world.get_card(sandbox_id)
    return f"sandbox_history:{sandbox_id}:{node.created_at.isoformat()}"


def read(services, sandbox_id):
    return read_key(services, key(services, sandbox_id), sandbox_id)


def _read_receipts(raw, history_key):
    if not isinstance(raw, list):
        return []
    receipts = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        # A failed dispatch in an older build could persist a receipt before
        # all fields were populated. Normalize only the returned copy; never
        # erase its error or require users to reset their Sandbox history.
        receipt = dict(item)
        if not isinstance(receipt.get("id"), str) or not receipt["id"]:
            receipt["id"] = f"legacy:{history_key}:{index}"
        if not isinstance(receipt.get("state"), str):
            receipt["state"] = "unknown"
        if not isinstance(receipt.get("argv"), list):
            receipt["argv"] = []
        receipts.append(receipt)
    return receipts


def read_key(services, history_key, sandbox_id):
    with services.database.locked() as connection:
        row = connection.execute("SELECT value_json FROM application_settings WHERE key = ?", (history_key,)).fetchone()
    items = _read_receipts(json.loads(row[0]) if row else [], history_key)
    active = {key: value for key, value in services._sandbox_commands.items() if value["sandbox_id"] == sandbox_id}
    for item in items:
        if item["state"] == "running" and item["id"] not in active:
            item["state"] = "interrupted"
            item["error"] = "Execution could not be recovered after backend restart; it was not resubmitted."
    for command in active.values():
        if command.get("history_key") == history_key:
            current = dict(command)
            if not isinstance(current.get("argv"), list):
                current["argv"] = []
            items = [entry for entry in items if entry["id"] != command["id"]] + [current]
    return items


def recent_summaries(services, sandbox_id):
    """The Agent view uses the same receipts as the UI, with smaller output tails."""
    fields = ("id", "operation_kind", "phase", "caller", "run_id", "argv", "started_at", "state", "exit_code", "timed_out", "cancelled", "termination_reason",
              "cancellation_reason", "error", "duration_seconds")
    return [
        {key: entry[key] for key in fields if key in entry}
        | {label: entry.get(label, "")[-8192:] for label in ("stdout", "stderr")}
        for entry in read(services, sandbox_id)[-3:]
    ]


def save(services, sandbox_id, item):
    history_key = item.get('history_key') or key(services, sandbox_id)
    items = [entry for entry in read_key(services, history_key, sandbox_id) if entry["id"] != item["id"]]
    items.append(item)
    # Live work, uncollected operation results and unresolved cleanup must not
    # age out while an Agent still holds a handle. Collected results are bounded.
    kept = [entry for entry in items[:-20] if entry['state'] == 'running'
            or (entry.get('operation_kind') and not entry.get('result_observed'))
            or entry.get('cleanup') in {'pending', 'failed', 'uncertain'}] + items[-20:]
    with services.database.transaction(immediate=True) as connection:
        connection.execute("INSERT INTO application_settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (history_key, json.dumps(kept)))


def lifecycle_records(services):
    with services.database.locked() as connection:
        rows = connection.execute("SELECT key, value_json FROM application_settings WHERE key LIKE 'sandbox_history:%'").fetchall()
    return [entry for history_key, value_json in rows
            for entry in _read_receipts(json.loads(value_json), history_key)]


async def stop(services, sandbox_id, *, terminate=False, agent_id=None, command_id=None):
    """Persist cancellation intent and join the existing native backend operation."""
    import asyncio
    from datetime import UTC, datetime
    from uuid import uuid4
    async with services._node_mutation():
        services._require_card_type(sandbox_id, 'sandbox')
        if agent_id is not None:
            kind = "sandbox.stop" if terminate else "sandbox.execute"
            services.capabilities.capability_for_id(agent_id, f"{kind}:{sandbox_id}")
        active_commands = [r for r in services._sandbox_commands.values() if r['sandbox_id'] == sandbox_id]
        if not terminate:
            from backend.sandbox.models import SandboxStateError
            if command_id is None:
                if len(active_commands) != 1:
                    raise SandboxStateError('Select a command_id to cancel; inspect active_commands')
                command_id = active_commands[0]['id']
            active = services._sandbox_commands.get(command_id)
            if active is None or active['sandbox_id'] != sandbox_id:
                raise SandboxStateError('Command is no longer active; inspect the Sandbox again')
            if agent_id is not None and active['caller'] != agent_id:
                services.capabilities.capability_for_id(agent_id, f"sandbox.stop:{sandbox_id}")
        else:
            active = active_commands[0] if active_commands else None
        services.resources.artifacts.assert_source_idle(sandbox_id)
        pending = next((r for r in reversed(read(services, sandbox_id)) if r.get('cleanup') in {'pending', 'failed'}), None)
        receipt = active or pending or {'id': uuid4().hex, 'sandbox_id': sandbox_id, 'caller': 'user', 'state': 'stopping',
            'argv': [], 'started_at': datetime.now(UTC).isoformat(), 'history_key': key(services, sandbox_id)}
        receipts = active_commands if terminate and active_commands else [receipt]
        for item in receipts:
            item.update(cancellation_requested=True, cancellation_reason="sandbox_stop" if terminate else "command_cancel", cleanup='pending', cleanup_error=None, stop_runtime=terminate)
            save(services, sandbox_id, item)
        if terminate:
            services._sandbox_stopping.add(sandbox_id)
        tasks = [services._sandbox_tasks[r["id"]] for r in (active_commands if terminate else [active])
            if r["id"] in services._sandbox_tasks]
    try:
        backend = services._require_sandbox_backend()
        async def cleanup():
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            outcomes = await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
            for outcome in outcomes:
                if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                    raise outcome
            if terminate:
                await backend.terminate(sandbox_id)
        await services._run_bounded_lifecycle_cleanup(
            cleanup(),
            timeout_seconds=services._lifecycle_cleanup_timeout_seconds)
        for item in receipts:
            item.update(cleanup='complete', termination_confirmed=True)
        if receipt['state'] == 'stopping':
            receipt['state'] = 'stopped'
    except TimeoutError as error:
        # The bounded cleanup window closed while the native worker was still
        # draining: a shielded package installation can legitimately outlive
        # it. Cancellation intent is already journalled, so surface a
        # retryable conflict instead of letting the TimeoutError escape the
        # tool boundary and end the caller's whole Run.
        for item in receipts:
            item.update(cleanup='pending', termination_confirmed=False,
                cleanup_error=f'{type(error).__name__}: {error}')
            save(services, sandbox_id, item)
        raise SandboxStateError(
            'Cancellation was requested and journalled; the operation is still '
            'draining. Inspect active_commands and wait for it to finish.') from error
    except BaseException as error:
        for item in receipts:
            item.update(cleanup='failed', termination_confirmed=False, cleanup_error=f'{type(error).__name__}: {error}')
            save(services, sandbox_id, item)
        services._sandbox_stopping.add(sandbox_id)
        raise
    else:
        for item in receipts:
            save(services, sandbox_id, item)
        if terminate:
            services._sandbox_stopping.discard(sandbox_id)
    return receipt


async def recover(services):
    # Native backends reconcile their own process-tree ownership; command side
    # effects are never resubmitted. Only idempotent termination is retried.
    for receipt in lifecycle_records(services):
        sandbox_id = receipt.get('sandbox_id')
        if not sandbox_id or not services.world.maybe_get_card(sandbox_id):
            continue
        if receipt.get('cleanup') in {'pending', 'failed'}:
            try:
                await stop(services, sandbox_id, terminate=True)
            except Exception:
                # stop persisted the actionable native failure and closed admission.
                continue
            receipt.update(cleanup='complete', termination_confirmed=True)
        if receipt['state'] == 'running' and receipt['id'] not in services._sandbox_commands:
            receipt.update(state='interrupted', error='Backend restarted; command was not resubmitted')
        save(services, sandbox_id, receipt)
