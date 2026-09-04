"""Authoritative SQLite persistence and mutation semantics for runtime state."""

from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.errors import NotFoundError, PermissionDeniedError, RevisionConflictError
from backend.persistence.database import Database
from backend.plugins.registry import PluginRegistry

from .context import StateContext
from .models import (
    ResolvedStateValue,
    StateMutation,
    StateMutationKind,
    StateScope,
    StateScopeRef,
    StateSnapshot,
    StateValueRecord,
)
from .schema import MergePolicy, StateFieldDefinition


def _now() -> str:
    return datetime.now(UTC).isoformat()


_MISSING = object()


class StateStore:
    """Single authority for scope identity, values, revisions, and merging."""

    def __init__(
        self,
        database: Database,
        registry: PluginRegistry,
        *,
        event_sink: Callable[[StateMutation], None] | None = None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.event_sink = event_sink

    def get_scope(
        self, scope_kind: StateScopeRef | str, owner_id: str | None = None
    ) -> StateScope:
        ref = self._scope_ref(scope_kind, owner_id)
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM state_scopes WHERE scope_kind = ? AND owner_id = ?",
                ref.identity,
            ).fetchone()
        if row is None:
            raise NotFoundError(
                f"state scope {ref.scope_kind}:{ref.owner_id} does not exist"
            )
        return self._scope(row)

    def ensure_scope(
        self,
        scope_kind: StateScopeRef | str,
        owner_id: str | None = None,
        *,
        schema_id: str | None = None,
    ) -> StateScope:
        ref = self._scope_ref(scope_kind, owner_id)
        selected_schema = schema_id or f"core.{ref.scope_kind}"
        self.registry.state_schema(selected_schema)
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO state_scopes (
                    scope_id, scope_kind, owner_id, schema_id, revision,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(scope_kind, owner_id) DO NOTHING
                """,
                (str(uuid4()), ref.scope_kind, ref.owner_id, selected_schema, now, now),
            )
            row = connection.execute(
                "SELECT * FROM state_scopes WHERE scope_kind = ? AND owner_id = ?",
                ref.identity,
            ).fetchone()
            if row is None:  # pragma: no cover - guarded by the transaction
                raise RuntimeError("state scope creation did not produce a row")
            if str(row["schema_id"]) != selected_schema:
                raise ValueError(
                    f"state scope {ref.scope_kind}:{ref.owner_id} already uses schema "
                    f"{row['schema_id']!r}, not {selected_schema!r}"
                )
        return self._scope(row)

    def delete_scope(
        self,
        scope_kind: StateScope | StateScopeRef | str,
        owner_id: str | None = None,
    ) -> None:
        ref = self._scope_ref(scope_kind, owner_id)
        with self.database.transaction(immediate=True) as connection:
            scope_row = connection.execute(
                "SELECT * FROM state_scopes WHERE scope_kind = ? AND owner_id = ?",
                ref.identity,
            ).fetchone()
            if scope_row is None:
                return
            value_rows = connection.execute(
                "SELECT key, revision FROM state_values WHERE scope_id = ? AND deleted = 0",
                (str(scope_row["scope_id"]),),
            ).fetchall()
            connection.execute(
                "DELETE FROM state_scopes WHERE scope_kind = ? AND owner_id = ?",
                ref.identity,
            )
        deleted_scope = self._scope(scope_row)
        for row in value_rows:
            self._emit(StateMutation(
                kind=StateMutationKind.DELETED,
                scope=deleted_scope,
                key=str(row["key"]),
                revision=int(row["revision"]) + 1,
            ))

    def get_record(
        self, scope: StateScope | StateScopeRef, key: str
    ) -> StateValueRecord:
        persisted = self._authoritative_scope(scope)
        definition = self._field(persisted, key)
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM state_values WHERE scope_id = ? AND key = ?",
                (persisted.scope_id, key),
            ).fetchone()
        if row is None or bool(row["deleted"]):
            if definition.has_default:
                return StateValueRecord(
                    scope=persisted,
                    key=key,
                    value=definition.validate(deepcopy(definition.default), key=key),
                    revision=0,
                    updated_at=persisted.created_at,
                )
            raise NotFoundError(
                f"state value {persisted.scope_kind}:{persisted.owner_id}.{key} does not exist"
            )
        return self._value_record(persisted, row)

    def get(self, scope: StateScope | StateScopeRef, key: str) -> Any:
        return self.get_record(scope, key).value

    def set(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
        permissions: Iterable[str] | None = None,
        actor_id: str | None = None,
        run_id: str | None = None,
    ) -> StateValueRecord:
        return self._mutate(
            scope,
            key,
            value,
            policy=MergePolicy.REPLACE,
            expected_revision=expected_revision,
            permissions=permissions,
            actor_id=actor_id,
            run_id=run_id,
        )

    def patch(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
        permissions: Iterable[str] | None = None,
        actor_id: str | None = None,
        run_id: str | None = None,
    ) -> StateValueRecord:
        persisted = self._authoritative_scope(scope)
        definition = self._field(persisted, key)
        return self._mutate(
            persisted,
            key,
            value,
            policy=definition.merge_policy,
            expected_revision=expected_revision,
            permissions=permissions,
            actor_id=actor_id,
            run_id=run_id,
        )

    def delete(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        *,
        expected_revision: int | None = None,
        permissions: Iterable[str] | None = None,
        actor_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        persisted = self._authoritative_scope(scope)
        definition = self._field(persisted, key)
        self._check_write_permissions(definition, permissions, key)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT revision, deleted FROM state_values WHERE scope_id = ? AND key = ?",
                (persisted.scope_id, key),
            ).fetchone()
            if row is None or bool(row["deleted"]):
                raise NotFoundError(
                    f"state value {persisted.scope_kind}:{persisted.owner_id}.{key} does not exist"
                )
            revision = int(row["revision"])
            self._check_revision(expected_revision, revision, persisted, key)
            now = _now()
            connection.execute(
                """
                UPDATE state_values
                SET value_json = 'null', revision = revision + 1,
                    deleted = 1, updated_at = ?
                WHERE scope_id = ? AND key = ?
                """,
                (now, persisted.scope_id, key),
            )
            connection.execute(
                "UPDATE state_scopes SET revision = revision + 1, updated_at = ? WHERE scope_id = ?",
                (now, persisted.scope_id),
            )
        current_scope = self.get_scope(persisted)
        self._emit(StateMutation(
            kind=StateMutationKind.DELETED,
            scope=current_scope,
            key=key,
            revision=revision + 1,
            actor_id=actor_id,
            run_id=run_id,
        ))

    def resolve(self, context: StateContext, key: str) -> ResolvedStateValue:
        local_scope_id = context.local_scope.scope_id
        for candidate in reversed(context.scope_stack):
            scope = self._authoritative_scope(candidate)
            schema = self.registry.state_schema(scope.schema_id)
            definition = schema.fields.get(key)
            if definition is None:
                continue
            if definition.read_visibility == "scope_only" and scope.scope_id != local_scope_id:
                continue
            try:
                record = self.get_record(scope, key)
            except NotFoundError:
                continue
            return ResolvedStateValue(
                key=key,
                value=record.value,
                source_scope=record.scope,
                revision=record.revision,
                updated_at=record.updated_at if record.revision else None,
                is_default=record.revision == 0,
            )
        raise NotFoundError(f"state value {key!r} is not defined in this StateContext")

    def snapshot(self, context: StateContext) -> StateSnapshot:
        scopes = tuple(self._authoritative_scope(scope) for scope in context.scope_stack)
        keys: set[str] = set()
        for scope in scopes:
            keys.update(self.registry.state_schema(scope.schema_id).fields)
        values: dict[str, ResolvedStateValue] = {}
        normalized = StateContext(scopes)
        for key in sorted(keys):
            try:
                values[key] = self.resolve(normalized, key)
            except NotFoundError:
                pass
        return StateSnapshot(scope_stack=scopes, values=values)

    def api(
        self,
        context: StateContext,
        *,
        permissions: Iterable[str] = (),
        actor_id: str | None = None,
        run_id: str | None = None,
    ) -> "StateAPI":
        from .context import StateAPI

        return StateAPI(
            store=self,
            context=context,
            permissions=frozenset(permissions),
            actor_id=actor_id,
            run_id=run_id,
        )

    def _mutate(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        policy: MergePolicy,
        expected_revision: int | None,
        permissions: Iterable[str] | None,
        actor_id: str | None,
        run_id: str | None,
    ) -> StateValueRecord:
        persisted = self._authoritative_scope(scope)
        definition = self._field(persisted, key)
        self._check_write_permissions(definition, permissions, key)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT value_json, revision, deleted
                FROM state_values WHERE scope_id = ? AND key = ?
                """,
                (persisted.scope_id, key),
            ).fetchone()
            previous = (
                _MISSING
                if row is None or bool(row["deleted"])
                else json.loads(str(row["value_json"]))
            )
            previous_revision = 0 if row is None else int(row["revision"])
            self._check_revision(expected_revision, previous_revision, persisted, key)
            merged = self._merge(policy, previous, value, key)
            validated = definition.validate(merged, key=key)
            try:
                encoded = json.dumps(validated, separators=(",", ":"), sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"state field {key!r} is not JSON serializable") from exc
            revision = previous_revision + 1
            now = _now()
            connection.execute(
                """
                INSERT INTO state_values (
                    scope_id, key, value_json, revision, deleted, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(scope_id, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    revision = excluded.revision,
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (persisted.scope_id, key, encoded, revision, now),
            )
            connection.execute(
                "UPDATE state_scopes SET revision = revision + 1, updated_at = ? WHERE scope_id = ?",
                (now, persisted.scope_id),
            )
        current_scope = self.get_scope(persisted)
        result = StateValueRecord(
            scope=current_scope,
            key=key,
            value=validated,
            revision=revision,
            updated_at=datetime.fromisoformat(now),
        )
        self._emit(StateMutation(
            kind=StateMutationKind.CREATED if previous is _MISSING else StateMutationKind.UPDATED,
            scope=current_scope,
            key=key,
            revision=revision,
            actor_id=actor_id,
            run_id=run_id,
        ))
        return result

    @staticmethod
    def _merge(policy: MergePolicy, previous: Any, value: Any, key: str) -> Any:
        if policy is MergePolicy.REPLACE:
            return value
        if policy is MergePolicy.MERGE_DICT:
            base = {} if previous is _MISSING else previous
            if not isinstance(base, dict) or not isinstance(value, dict):
                raise ValueError(f"merge_dict state field {key!r} requires dictionaries")
            return {**base, **value}
        if policy in {MergePolicy.APPEND, MergePolicy.APPEND_UNIQUE}:
            base = [] if previous is _MISSING else previous
            if not isinstance(base, list):
                raise ValueError(f"{policy.value} state field {key!r} requires a list")
            additions = value if isinstance(value, list) else [value]
            result = [*base]
            for item in additions:
                if policy is MergePolicy.APPEND or item not in result:
                    result.append(item)
            return result
        raise ValueError(f"unsupported state merge policy {policy!r}")

    def _field(self, scope: StateScope, key: str) -> StateFieldDefinition:
        schema = self.registry.state_schema(scope.schema_id)
        try:
            definition = schema.fields[key]
        except KeyError as exc:
            raise ValueError(
                f"state key {key!r} is not declared by schema {scope.schema_id!r}"
            ) from exc
        if (
            definition.allowed_scope_kinds
            and scope.scope_kind not in definition.allowed_scope_kinds
        ):
            raise ValueError(
                f"state key {key!r} cannot be stored in a {scope.scope_kind!r} scope"
            )
        return definition

    @staticmethod
    def _check_write_permissions(
        definition: StateFieldDefinition,
        permissions: Iterable[str] | None,
        key: str,
    ) -> None:
        if permissions is None or "*" in definition.write_permissions:
            return
        granted = frozenset(permissions)
        if definition.write_permissions.isdisjoint(granted):
            raise PermissionDeniedError(f"write permission denied for state field {key!r}")

    @staticmethod
    def _check_revision(
        expected: int | None,
        actual: int,
        scope: StateScope,
        key: str,
    ) -> None:
        if expected is not None and expected != actual:
            raise RevisionConflictError(
                f"state revision conflict for {scope.scope_kind}:{scope.owner_id}.{key}: "
                f"expected {expected}, found {actual}"
            )

    def _authoritative_scope(self, scope: StateScope | StateScopeRef) -> StateScope:
        persisted = self.get_scope(scope)
        if isinstance(scope, StateScope) and persisted.scope_id != scope.scope_id:
            raise ValueError("StateScope identity does not match its persisted scope")
        return persisted

    @staticmethod
    def _scope_ref(
        scope: StateScopeRef | str, owner_id: str | None
    ) -> StateScopeRef:
        if isinstance(scope, StateScopeRef):
            if owner_id is not None:
                raise TypeError("owner_id must not be passed with a StateScopeRef")
            return scope
        if owner_id is None:
            raise TypeError("owner_id is required when scope_kind is passed as a string")
        return StateScopeRef(scope_kind=scope, owner_id=owner_id)

    @staticmethod
    def _scope(row: object) -> StateScope:
        return StateScope(
            scope_kind=str(row["scope_kind"]),
            owner_id=str(row["owner_id"]),
            scope_id=str(row["scope_id"]),
            schema_id=str(row["schema_id"]),
            revision=int(row["revision"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _value_record(scope: StateScope, row: object) -> StateValueRecord:
        return StateValueRecord(
            scope=scope,
            key=str(row["key"]),
            value=json.loads(str(row["value_json"])),
            revision=int(row["revision"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _emit(self, mutation: StateMutation) -> None:
        if self.event_sink is not None:
            self.event_sink(mutation)
