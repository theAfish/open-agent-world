"""Pageable activity directory; reading it never materializes invocation bodies."""
from __future__ import annotations

import json

from backend.errors import NotFoundError


def stored_record(database, run_id, field, identity):
    """Select one legacy JSON member in SQLite, without decoding the whole Run."""
    with database.locked() as connection:
        row = connection.execute('''SELECT j.value FROM state_values v
            JOIN state_scopes s ON s.scope_id = v.scope_id, json_each(v.value_json) j
            WHERE s.scope_kind = 'run' AND s.owner_id = ? AND v.key = ? AND v.deleted = 0 AND j.key = ?''',
            (run_id, field, identity)).fetchone()
    return json.loads(row[0]) if row else None


def public_tool_identity(payload, event_id):
    return 'public-tool:' + (json.dumps([payload.get('role'), payload.get('run_attempt'), payload['call_id']])
        if payload.get('call_id') else event_id)


def tool_failed(payload):
    def failed(value):
        if not isinstance(value, dict):
            return False
        code = value.get('exit_code', value.get('exitCode'))
        return bool(value.get('error') or value.get('success') is False or value.get('ok') is False
            or value.get('status') in ('failed', 'error') or type(code) is int and code != 0)
    response = payload.get('response')
    return failed(payload) or failed(response) or isinstance(response, dict) and failed(response.get('result'))


class RunActivityHistory:
    def __init__(self, database, run_id):
        self.database = database
        self.run_id = run_id

    def save(self, identity, metadata, detail=None, *, legacy=False):
        with self.database.transaction(immediate=True) as connection:
            previous = connection.execute(
                "SELECT metadata_json, detail_json FROM run_activity WHERE run_id = ? AND identity = ?",
                (self.run_id, identity)).fetchone()
            if previous:
                old = json.loads(previous['metadata_json'])
                if legacy and old.get('updated_at', '') >= metadata.get('updated_at', ''):
                    return
                # Results fill the original row, preserving its place and start.
                metadata = {**old, **metadata, 'timestamp': old['timestamp']}
                if old.get('started_at'):
                    metadata['started_at'] = old['started_at']
                if old.get('type') == 'tool_completed':
                    metadata['type'] = 'tool_completed'
                if detail is not None and previous['detail_json']:
                    detail = {**json.loads(previous['detail_json']), **detail}
            metadata['id'] = identity
            connection.execute("""INSERT INTO run_activity(run_id, identity, timestamp, metadata_json, detail_json)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(run_id, identity) DO UPDATE SET
                metadata_json = excluded.metadata_json, detail_json = COALESCE(excluded.detail_json, detail_json)""",
                (self.run_id, identity, metadata['timestamp'], json.dumps(metadata),
                 json.dumps(detail) if detail is not None else None))

    def tool(self, row):
        fields = {key: row[key] for key in ('call_id', 'name', 'role', 'run_attempt', 'started_at', 'updated_at') if key in row}
        from .trace import public_tool_payload
        public = public_tool_payload({'name': row.get('name'), 'arguments': row.get('effective_arguments', row.get('arguments'))})
        fields.update({key: public[key] for key in ('tool_name', 'capability_id', 'author', 'subagent') if key in public})
        fields.update(type='tool_started' if row['state'] == 'started' else 'tool_completed',
                      archive_id=row['id'], timestamp=row['started_at'],
                      finished_at=row.get('returned_at') or row.get('finished_at') or row.get('interrupted_at'),
                      interrupted=row['state'] == 'interrupted', success=row['state'] not in {'failed', 'interrupted'})
        fields['success'] = fields['success'] and not tool_failed(row)
        self.save('tool:' + row['id'], fields)

    def thinking(self, row):
        fields = {key: row[key] for key in ('provider_message_id', 'role', 'model_request', 'model_attempt',
            'run_attempt', 'streaming', 'interrupted', 'updated_at') if key in row}
        self.save('thinking:' + row['provider_message_id'], {**fields, 'type': 'agent_progress',
            'kind': 'model_reasoning', 'timestamp': row['created_at'], 'preview_truncated': True})

    def public(self, kind, payload, event_id, timestamp, *, legacy=False):
        if kind.startswith('tool_'):
            if payload.get('archive_id'):
                # Native records have full bodies and already own this identity.
                return
            identity = payload.get('activity_id') or public_tool_identity(payload, event_id)
        elif kind == 'agent_progress' and payload.get('kind', 'status') in {
                'status', 'plan', 'reasoning_summary', 'model_stream_retry', 'model_stream_interrupted'}:
            if not isinstance(payload.get('text'), str) or not payload['text'].strip():
                return
            identity = 'progress:' + str(payload.get('provider_message_id') or event_id)
        else:
            return
        fields = {key: payload[key] for key in ('kind', 'role', 'run_attempt', 'provider_message_id',
            'conversation_message_id', 'call_id', 'name', 'tool_name', 'capability_id', 'success', 'interrupted', 'truncated') if key in payload}
        fields.update(type=kind, timestamp=timestamp, updated_at=timestamp, activity_id=identity, source_event_id=event_id)
        if kind.startswith('tool_'):
            fields['started_at' if kind == 'tool_started' else 'finished_at'] = timestamp
            if kind == 'tool_completed':
                fields['success'] = not tool_failed(payload)
        else:
            text = str(payload.get('text') or '')
            fields.update(text=text[:2000], preview_truncated=len(text) > 2000)
        self.save(identity, fields, payload, legacy=legacy)

    def _import_legacy(self):
        """One-time directory backfill. SQLite projects metadata, not full bodies.

        Existing stored records stay authoritative and retain their identities.
        Runs predating archival can only recover their retained public preview.
        """
        with self.database.transaction(immediate=True) as connection:
            if connection.execute('SELECT 1 FROM run_activity_imports WHERE run_id = ?', (self.run_id,)).fetchone():
                return
            for key, prefix, timestamp, projection in (
                ('tool_invocation_logs', 'tool:', 'started_at', """
                    'type', CASE json_extract(j.value, '$.state') WHEN 'started' THEN 'tool_started' ELSE 'tool_completed' END,
                    'archive_id', j.key, 'name', json_extract(j.value, '$.name'),
                    'capability_id', coalesce(json_extract(j.value, '$.effective_arguments.capability_id'), json_extract(j.value, '$.arguments.capability_id')),
                    'call_id', json_extract(j.value, '$.call_id'), 'role', json_extract(j.value, '$.role'),
                    'run_attempt', json_extract(j.value, '$.run_attempt'),
                    'started_at', json_extract(j.value, '$.started_at'),
                    'finished_at', coalesce(json_extract(j.value, '$.returned_at'), json_extract(j.value, '$.finished_at'), json_extract(j.value, '$.interrupted_at')),
                    'success', json(CASE WHEN json_extract(j.value, '$.state') IN ('failed', 'interrupted')
                        OR json_extract(j.value, '$.response.success') = 0 OR json_extract(j.value, '$.response.ok') = 0
                        OR json_extract(j.value, '$.response.exit_code') != 0 OR json_extract(j.value, '$.response.exitCode') != 0
                        OR coalesce(json_extract(j.value, '$.response.error'), '') NOT IN ('', 0)
                        OR json_extract(j.value, '$.response.result.success') = 0 OR json_extract(j.value, '$.response.result.ok') = 0
                        THEN 'false' ELSE 'true' END)"""),
                ('model_reasoning', 'thinking:', 'created_at', """
                    'type', 'agent_progress', 'kind', 'model_reasoning', 'provider_message_id', j.key,
                    'role', json_extract(j.value, '$.role'), 'model_request', json_extract(j.value, '$.model_request'),
                    'model_attempt', json_extract(j.value, '$.model_attempt'),
                    'streaming', json(CASE WHEN json_extract(j.value, '$.streaming') THEN 'true' ELSE 'false' END),
                    'interrupted', json(CASE WHEN json_extract(j.value, '$.interrupted') THEN 'true' ELSE 'false' END),
                    'preview_truncated', json('true')"""),
            ):
                connection.execute(f"""INSERT OR IGNORE INTO run_activity(run_id, identity, timestamp, metadata_json)
                    SELECT ?, ? || j.key, json_extract(j.value, '$.{timestamp}'),
                        json_object('id', ? || j.key, 'timestamp', json_extract(j.value, '$.{timestamp}'),
                            'updated_at', json_extract(j.value, '$.updated_at'), {projection})
                    FROM state_values v JOIN state_scopes s ON s.scope_id = v.scope_id, json_each(v.value_json) j
                    WHERE s.scope_kind = 'run' AND s.owner_id = ? AND v.key = ? AND v.deleted = 0""",
                    (self.run_id, prefix, prefix, self.run_id, key))
            # The old rolling trace is bounded. Import only public rows without
            # native identities; never infer matches by tool name.
            row = connection.execute('SELECT lifecycle_json FROM runs WHERE run_id = ?', (self.run_id,)).fetchone()
            lifecycle = json.loads(row[0]) if row else {}
            # Import outside this transaction: public() owns its transaction.
        for i, entry in enumerate(lifecycle.get('tool_trace') or []):
            self.public(entry.get('type', ''), entry, entry.get('id', f'legacy:{i}'), entry.get('timestamp', ''), legacy=True)
        with self.database.transaction(immediate=True) as connection:
            connection.execute('INSERT OR IGNORE INTO run_activity_imports VALUES (?)', (self.run_id,))

    def page(self, offset=0, limit=50, *, latest=False):
        self._import_legacy()
        # Older directory writers also indexed status ticks with no visible
        # text. Keep the stored events, but exclude them before pagination.
        visible = """run_id = ? AND (json_extract(metadata_json, '$.type') != 'agent_progress'
            OR json_extract(metadata_json, '$.kind') = 'model_reasoning'
            OR length(trim(coalesce(json_extract(metadata_json, '$.text'), ''), char(32, 9, 10, 13))) > 0)"""
        with self.database.locked() as connection:
            total = connection.execute(f'SELECT count(*) FROM run_activity WHERE {visible}', (self.run_id,)).fetchone()[0]
            coverage = connection.execute("""SELECT json_extract(lifecycle_json, '$.tool_count'),
                json_extract(lifecycle_json, '$.tool_trace_truncated'),
                (SELECT count(*) FROM run_activity WHERE run_id = ? AND json_extract(metadata_json, '$.type') LIKE 'tool_%')
                FROM runs WHERE run_id = ?""", (self.run_id, self.run_id)).fetchone()
            if latest:
                offset = max(0, total - limit)
            rows = connection.execute(f'''SELECT metadata_json FROM run_activity WHERE {visible}
                ORDER BY timestamp, sequence LIMIT ? OFFSET ?''', (self.run_id, limit, offset)).fetchall()
        return {'run_id': self.run_id, 'items': [json.loads(row[0]) for row in rows], 'total': total,
                'incomplete': bool(coverage and coverage[1] and (coverage[0] or 0) > coverage[2]),
                'next_offset': offset + limit if offset + limit < total else None}

    def get(self, identity):
        self._import_legacy()
        with self.database.locked() as connection:
            row = connection.execute('SELECT detail_json FROM run_activity WHERE run_id = ? AND identity = ?',
                                     (self.run_id, identity)).fetchone()
        if row is None or row[0] is None:
            raise NotFoundError('No saved public activity detail for this Run')
        return json.loads(row[0])
