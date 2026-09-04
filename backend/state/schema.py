"""Schema contracts for state owned by core components and plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import TypeAdapter, ValidationError


class MergePolicy(StrEnum):
    REPLACE = "replace"
    MERGE_DICT = "merge_dict"
    APPEND = "append"
    APPEND_UNIQUE = "append_unique"


class _NoDefault:
    pass


NO_DEFAULT = _NoDefault()


@dataclass(frozen=True, slots=True)
class StateFieldDefinition:
    """One state field's type, ownership, visibility, and mutation contract."""

    value_type: Any = Any
    allowed_scope_kinds: frozenset[str] = frozenset()
    read_visibility: str = "inherited"
    write_permissions: frozenset[str] = frozenset({"*"})
    merge_policy: MergePolicy = MergePolicy.REPLACE
    default: Any = NO_DEFAULT

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_scope_kinds", frozenset(self.allowed_scope_kinds))
        object.__setattr__(self, "write_permissions", frozenset(self.write_permissions))
        object.__setattr__(self, "merge_policy", MergePolicy(self.merge_policy))
        if self.read_visibility not in {"inherited", "scope_only"}:
            raise ValueError("read_visibility must be 'inherited' or 'scope_only'")

    @property
    def has_default(self) -> bool:
        return self.default is not NO_DEFAULT

    def validate(self, value: Any, *, key: str) -> Any:
        try:
            return TypeAdapter(self.value_type).validate_python(value)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid value for state field {key!r}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class StateSchema:
    id: str
    fields: Mapping[str, StateFieldDefinition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("state schema id must be non-empty")
        normalized: dict[str, StateFieldDefinition] = {}
        for key, definition in self.fields.items():
            if not key or not isinstance(key, str):
                raise ValueError("state field keys must be non-empty strings")
            if not isinstance(definition, StateFieldDefinition):
                raise TypeError(f"state field {key!r} must be a StateFieldDefinition")
            normalized[key] = definition
        object.__setattr__(self, "fields", MappingProxyType(normalized))

