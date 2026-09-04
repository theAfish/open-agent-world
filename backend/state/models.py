"""Provider-neutral state identities and observable value records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class StateScopeRef:
    """Logical identity of one state owner.

    Scope kinds deliberately remain open strings so plugins and future runtime
    primitives can introduce scopes without changing the core model.
    """

    scope_kind: str
    owner_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope_kind, str) or not self.scope_kind.strip():
            raise ValueError("scope_kind must be a non-empty string")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")

    @property
    def identity(self) -> tuple[str, str]:
        return self.scope_kind, self.owner_id


@dataclass(frozen=True, slots=True)
class StateScope(StateScopeRef):
    """Persisted state scope with a stable database identity."""

    scope_id: str
    schema_id: str
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StateValueRecord:
    scope: StateScope
    key: str
    value: Any
    revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedStateValue:
    key: str
    value: Any
    source_scope: StateScope
    revision: int
    updated_at: datetime | None
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Resolved, source-attributed view of an ordered invocation context."""

    scope_stack: tuple[StateScope, ...]
    values: Mapping[str, ResolvedStateValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_stack", tuple(self.scope_stack))
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


class StateMutationKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class StateMutation:
    kind: StateMutationKind
    scope: StateScope
    key: str
    revision: int
    actor_id: str | None = None
    run_id: str | None = None
