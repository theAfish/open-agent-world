"""Ordered state inheritance contexts and narrow runtime-facing APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Protocol

from .models import ResolvedStateValue, StateScope, StateScopeRef, StateSnapshot

if TYPE_CHECKING:
    from .store import StateStore


@dataclass(frozen=True, slots=True)
class StateContext:
    """Scopes ordered from broadest to most local for one invocation."""

    scope_stack: tuple[StateScope, ...]

    def __post_init__(self) -> None:
        scopes = tuple(self.scope_stack)
        if not scopes:
            raise ValueError("StateContext requires at least one scope")
        if not all(isinstance(scope, StateScope) for scope in scopes):
            raise TypeError("StateContext scope_stack must contain persisted StateScope objects")
        identities = [scope.scope_id for scope in scopes]
        if len(identities) != len(set(identities)):
            raise ValueError("StateContext cannot contain the same scope more than once")
        object.__setattr__(self, "scope_stack", scopes)

    @property
    def local_scope(self) -> StateScope:
        return self.scope_stack[-1]

    def derive(
        self,
        *,
        additional_scopes: Iterable[StateScope] = (),
        inherited_scopes: Iterable[StateScope | StateScopeRef] | None = None,
    ) -> StateContext:
        """Project selected parent scopes and append child-local scopes.

        Propagation policy belongs to the caller; this primitive only makes the
        projection explicit and preserves the caller-provided order.
        """

        if inherited_scopes is None:
            inherited = list(self.scope_stack)
        else:
            selected = list(inherited_scopes)
            inherited = []
            for requested in selected:
                match = next((
                    scope
                    for scope in self.scope_stack
                    if (
                        scope.scope_id == requested.scope_id
                        if isinstance(requested, StateScope)
                        else scope.identity == requested.identity
                    )
                ), None)
                if match is None:
                    raise ValueError(
                        f"scope {requested.scope_kind}:{requested.owner_id} is not in the parent context"
                    )
                inherited.append(match)
        return StateContext(tuple((*inherited, *tuple(additional_scopes))))


class StateReader(Protocol):
    def get(self, scope: StateScope | StateScopeRef, key: str) -> Any: ...
    def resolve(self, context: StateContext, key: str) -> ResolvedStateValue: ...
    def snapshot(self, context: StateContext) -> StateSnapshot: ...


class StateWriter(Protocol):
    def set(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
    ) -> Any: ...

    def patch(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
    ) -> Any: ...

    def delete(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        *,
        expected_revision: int | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StateAPI:
    """Scoped facade suitable for runtime and capability boundaries."""

    store: StateStore
    context: StateContext
    permissions: frozenset[str] = frozenset()
    actor_id: str | None = None
    run_id: str | None = None

    def get_scope(self, scope_kind: str, owner_id: str) -> StateScope:
        return self.store.get_scope(scope_kind, owner_id)

    def ensure_scope(
        self, scope_kind: str, owner_id: str, *, schema_id: str | None = None
    ) -> StateScope:
        return self.store.ensure_scope(
            scope_kind, owner_id, schema_id=schema_id
        )

    def get(self, scope: StateScope | StateScopeRef, key: str) -> Any:
        return self.store.get(scope, key)

    def resolve(self, key: str) -> ResolvedStateValue:
        return self.store.resolve(self.context, key)

    def snapshot(self) -> StateSnapshot:
        return self.store.snapshot(self.context)

    def set(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
    ) -> Any:
        return self.store.set(
            scope,
            key,
            value,
            expected_revision=expected_revision,
            permissions=self.permissions,
            actor_id=self.actor_id,
            run_id=self.run_id,
        )

    def patch(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
    ) -> Any:
        return self.store.patch(
            scope,
            key,
            value,
            expected_revision=expected_revision,
            permissions=self.permissions,
            actor_id=self.actor_id,
            run_id=self.run_id,
        )

    def delete(
        self,
        scope: StateScope | StateScopeRef,
        key: str,
        *,
        expected_revision: int | None = None,
    ) -> None:
        self.store.delete(
            scope,
            key,
            expected_revision=expected_revision,
            permissions=self.permissions,
            actor_id=self.actor_id,
            run_id=self.run_id,
        )
