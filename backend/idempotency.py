"""Durable replay for short, synchronous mutations of this SQLite database.

The mutation and its response commit together. Callbacks must use this same
Database and must not perform I/O, await, or emit events. This is deliberately
not a general mechanism for provider calls, runs, or reliable event delivery.
Records have no automatic expiry: deleting one would allow its key to execute
again and must be an explicit retention policy decision.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.errors import ConflictError, DomainError
from backend.persistence import Database
from backend.request_context import RequestContext


class IdempotencyConflictError(ConflictError):
    code = "idempotency_conflict"


class InvalidIdempotencyKeyError(DomainError):
    code = "invalid_idempotency_key"


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    value: dict[str, Any]
    replayed: bool


class IdempotencyStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        with database.transaction(immediate=True) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_results (
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    world_id TEXT NOT NULL,
                    actor_kind TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY (organization_id, workspace_id, world_id,
                                 actor_kind, actor_id, operation, key)
                )
            """)

    def execute(
        self,
        context: RequestContext,
        *,
        operation: str,
        key: str,
        payload: dict[str, Any],
        authorize: Callable[[], None],
        mutate: Callable[[], dict[str, Any]],
    ) -> IdempotencyResult:
        """Authorize every attempt, then atomically mutate or replay its result.

        BEGIN IMMEDIATE serializes competing writers across SQLite connections;
        the in-process RLock alone cannot provide that guarantee. No transaction
        remains open when this synchronous method returns to an async caller.
        """
        if not isinstance(key, str) or re.fullmatch(r"[\x21-\x7e]{1,128}", key) is None:
            raise InvalidIdempotencyKeyError(
                "Idempotency-Key must contain 1 to 128 visible ASCII characters"
            )
        fingerprint = hashlib.sha256(self._json(payload).encode("utf-8")).hexdigest()
        identity = (
            context.tenant.organization_id, context.tenant.workspace_id,
            context.tenant.world_id, context.actor.kind, context.actor.id,
            operation, key,
        )
        with self.database.locked() as connection:
            if connection.in_transaction:
                raise RuntimeError("Idempotency execution must own its outer transaction")
            with self.database.transaction(immediate=True):
                self._require_sync(authorize())
                row = connection.execute("""
                    SELECT payload_hash, response_json FROM idempotency_results
                    WHERE organization_id=? AND workspace_id=? AND world_id=?
                        AND actor_kind=? AND actor_id=? AND operation=? AND key=?
                """, identity).fetchone()
                if row is not None:
                    if row["payload_hash"] != fingerprint:
                        raise IdempotencyConflictError(
                            "Idempotency-Key was already used with a different request"
                        )
                    result = IdempotencyResult(json.loads(row["response_json"]), replayed=True)
                else:
                    value = mutate()
                    self._require_sync(value)
                    if not isinstance(value, dict):
                        raise TypeError("Idempotent mutations must return a JSON object")
                    response_json = self._json(value)
                    connection.execute("""
                        INSERT INTO idempotency_results
                        (organization_id, workspace_id, world_id, actor_kind, actor_id,
                         operation, key, payload_hash, response_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (*identity, fingerprint, response_json))
                    result = IdempotencyResult(json.loads(response_json), replayed=False)
        return result

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _require_sync(value: object) -> None:
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise TypeError("Idempotency callbacks must be synchronous database operations")
