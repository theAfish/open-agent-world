"""Validated, durable node documents and pure plugin-owned actions."""
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class NodeDocumentAction:
    handler: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    capability_kind: str | None = None
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class NodeDocumentDefinition:
    model: type[BaseModel]
    actions: Mapping[str, NodeDocumentAction] = field(default_factory=dict)
    summarize: Callable[[dict[str, Any]], dict[str, Any]] = lambda value: {}
    capture: Callable[[dict[str, Any]], dict[str, Any]] = lambda value: value
