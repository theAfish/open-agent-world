"""Bounded host-only command receipts. Portable node state never carries these."""
import json


def key(services, sandbox_id):
    node = services.world.get_card(sandbox_id)
    return f"sandbox_history:{sandbox_id}:{node.created_at.isoformat()}"


def read(services, sandbox_id):
    return read_key(services, key(services, sandbox_id), sandbox_id)


def read_key(services, history_key, sandbox_id):
    with services.database.locked() as connection:
        row = connection.execute("SELECT value_json FROM application_settings WHERE key = ?", (history_key,)).fetchone()
    items = json.loads(row[0]) if row else []
    active = services._sandbox_commands.get(sandbox_id)
    for item in items:
        if item["state"] == "running" and (not active or active["id"] != item["id"]):
            item["state"] = "interrupted"
            item["error"] = "Execution could not be recovered after backend restart; it was not resubmitted."
    if active and active.get("history_key") == history_key:
        items = [entry for entry in items if entry["id"] != active["id"]] + [dict(active)]
    return items


def recent_summaries(services, sandbox_id):
    """The Agent view uses the same receipts as the UI, with smaller output tails."""
    fields = ("id", "state", "exit_code", "timed_out", "cancelled", "termination_reason",
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
    # Unresolved cleanup must not age out of the bounded command history.
    kept = [entry for entry in items[:-20] if entry.get('cleanup') in {'pending', 'failed', 'uncertain'}] + items[-20:]
    with services.database.transaction(immediate=True) as connection:
        connection.execute("INSERT INTO application_settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (history_key, json.dumps(kept)))


def lifecycle_records(services):
    with services.database.locked() as connection:
        rows = connection.execute("SELECT value_json FROM application_settings WHERE key LIKE 'sandbox_history:%'").fetchall()
    return [entry for row in rows for entry in json.loads(row[0])]


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
        if command_id is not None:
            from backend.sandbox.models import SandboxStateError
            current = services._sandbox_commands.get(sandbox_id)
            if current is None or current['id'] != command_id:
                raise SandboxStateError('Command is no longer active; inspect the Sandbox again')
        services.resources.artifacts.assert_source_idle(sandbox_id)
        active = services._sandbox_commands.get(sandbox_id)
        pending = next((r for r in reversed(read(services, sandbox_id)) if r.get('cleanup') in {'pending', 'failed'}), None)
        receipt = active or pending or {'id': uuid4().hex, 'sandbox_id': sandbox_id, 'caller': 'user', 'state': 'stopping',
            'argv': [], 'started_at': datetime.now(UTC).isoformat(), 'history_key': key(services, sandbox_id)}
        receipt.update(cancellation_requested=True, cancellation_reason="sandbox_stop" if terminate else "command_cancel", cleanup='pending', cleanup_error=None, stop_runtime=terminate)
        save(services, sandbox_id, receipt)
        services._sandbox_stopping.add(sandbox_id)
    try:
        backend = services._require_sandbox_backend()
        await services._run_bounded_lifecycle_cleanup(
            backend.terminate(sandbox_id) if terminate else backend.cancel(sandbox_id),
            timeout_seconds=services._lifecycle_cleanup_timeout_seconds)
        receipt.update(cleanup='complete', termination_confirmed=True)
        if receipt['state'] == 'stopping':
            receipt['state'] = 'stopped'
    except BaseException as error:
        receipt.update(cleanup='failed', termination_confirmed=False, cleanup_error=f'{type(error).__name__}: {error}')
        save(services, sandbox_id, receipt)
        raise
    else:
        save(services, sandbox_id, receipt)
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
                await stop(services, sandbox_id, terminate=receipt.get('stop_runtime', True))
            except Exception:
                # stop persisted the actionable native failure and closed admission.
                continue
            receipt.update(cleanup='complete', termination_confirmed=True)
        if receipt['state'] == 'running' and sandbox_id not in services._sandbox_commands:
            receipt.update(state='interrupted', error='Backend restarted; command was not resubmitted')
        save(services, sandbox_id, receipt)
