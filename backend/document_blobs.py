"""Content-addressed binary history, committed with the owning document revision."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime

from backend.errors import NotFoundError, ResourceValidationError


def _bytes(value):
    try:
        if not isinstance(value, str):
            raise ValueError("Expected base64 text")
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ResourceValidationError("A retained document field must contain valid base64") from exc


def retain_changes(connection, scope_id, fields, previous, updated):
    """Caller owns the same SQLite transaction as the optimistic document write."""
    now = datetime.now(UTC).isoformat()
    for field in fields:
        before, after = previous.get(field, ""), updated.get(field, "")
        if before == after:
            continue
        for index, encoded in enumerate((before, after)):
            if not encoded:
                continue
            raw = _bytes(encoded)
            digest = hashlib.sha256(raw).hexdigest()
            connection.execute("INSERT OR IGNORE INTO node_document_blobs VALUES(?,?,?,?)",
                               (digest, raw, len(raw), now))
            connection.execute("INSERT OR IGNORE INTO node_document_blob_members VALUES(?,?,?,?)",
                               (scope_id, field, digest, now))
            snapshot = {key: item for key, item in (previous if index == 0 else updated).items() if key not in fields}
            # Leaving a version captures the latest notes. Returning to bytes
            # already seen must not erase their previously archived reading state.
            suffix = " ON CONFLICT(scope_id,field,sha256) DO UPDATE SET snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at" if index == 0 else " ON CONFLICT DO NOTHING"
            connection.execute("INSERT INTO node_document_blob_snapshots VALUES(?,?,?,?,?)" + suffix,
                               (scope_id, field, digest, json.dumps(snapshot, ensure_ascii=False), now))


def restore_fields(connection, scope_id, configured_fields, previous, updated):
    """Restore opt-in state for returning bytes, inside the document transaction.

    Same-version saves never restore: explicitly clearing notes remains a normal
    edit and is captured when that version is next replaced. Ownership is local
    to this state scope and binary field, even when bytes are globally deduped.
    """
    result = dict(updated)
    for binary_field, names in configured_fields.items():
        encoded = updated.get(binary_field, "")
        if not encoded or encoded == previous.get(binary_field, ""):
            continue
        digest = hashlib.sha256(_bytes(encoded)).hexdigest()
        row = connection.execute("""SELECT s.snapshot_json
            FROM node_document_blob_snapshots s
            JOIN node_document_blob_members m
                ON m.scope_id=s.scope_id AND m.field=s.field AND m.sha256=s.sha256
            WHERE s.scope_id=? AND s.field=? AND s.sha256=?""",
            (scope_id, binary_field, digest)).fetchone()
        if row is not None:
            snapshot = json.loads(row["snapshot_json"])
            for name in names:
                if name in snapshot:
                    result[name] = deepcopy(snapshot[name])
    return result


def _owner(services, node_id, field):
    from backend.node_documents import definition, read_document
    if field not in definition(services, node_id).binary_history:
        raise ResourceValidationError("This document does not retain that binary field")
    return services.card_state.scope(node_id).scope_id, read_document(services, node_id)["value"]


def list_blobs(services, node_id, field):
    scope_id, value = _owner(services, node_id, field)
    with services.database.locked() as connection:
        rows = connection.execute("""SELECT m.sha256,b.size_bytes,m.created_at
            FROM node_document_blob_members m JOIN node_document_blobs b ON b.sha256=m.sha256
            WHERE m.scope_id=? AND m.field=? ORDER BY m.created_at,m.sha256""", (scope_id, field)).fetchall()
    items = [dict(row) for row in rows]
    current = None
    if value.get(field):
        raw = _bytes(value[field])
        current = hashlib.sha256(raw).hexdigest()
        if not any(item["sha256"] == current for item in items):
            # Legacy documents remain readable without a write during GET.
            items.append({"sha256": current, "size_bytes": len(raw), "created_at": None})
    return {"current": current, "items": [{**item, "current": item["sha256"] == current} for item in items]}


def read_blob(services, node_id, field, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ResourceValidationError("Expected a SHA256 document version")
    scope_id, value = _owner(services, node_id, field)
    with services.database.locked() as connection:
        row = connection.execute("""SELECT b.content FROM node_document_blobs b
            JOIN node_document_blob_members m ON m.sha256=b.sha256
            WHERE m.scope_id=? AND m.field=? AND m.sha256=?""", (scope_id, field, digest)).fetchone()
    if row:
        raw = bytes(row["content"])
    elif value.get(field):
        raw = _bytes(value[field])
    else:
        raise NotFoundError("This document version is unavailable")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise NotFoundError("This document version is unavailable or failed integrity validation")
    return raw


def read_snapshot(services, node_id, field, digest):
    from backend.node_documents import definition
    # The byte read enforces both live ownership and content integrity first.
    read_blob(services, node_id, field, digest)
    scope_id, value = _owner(services, node_id, field)
    fields = definition(services, node_id).binary_history
    if value.get(field) and hashlib.sha256(_bytes(value[field])).hexdigest() == digest:
        return {"value": {key: item for key, item in value.items() if key not in fields}, "current": True, "updated_at": None}
    with services.database.locked() as connection:
        row = connection.execute("SELECT snapshot_json,updated_at FROM node_document_blob_snapshots WHERE scope_id=? AND field=? AND sha256=?",
                                 (scope_id, field, digest)).fetchone()
    if row is None:
        raise NotFoundError("The reading state for this version is unavailable")
    return {"value": json.loads(row["snapshot_json"]), "current": False, "updated_at": row["updated_at"]}
