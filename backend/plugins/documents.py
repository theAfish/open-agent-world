"""Validated, durable node documents and pure plugin-owned actions."""
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class NodeDocumentDownload:
    filename: str
    content: bytes
    media_type: str = "application/octet-stream"


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
    # Used in both directions: live IDs -> template keys -> new live IDs.
    # References outside the captured group must be cleared by the plugin.
    remap_references: Callable[[dict[str, Any], Mapping[str, str]], dict[str, Any]] = lambda value, ids: value
    # Explicit seeds are copied into storage on creation, independently of future
    # plugin defaults. Existing document types can keep lazy model defaults.
    initial_value: Mapping[str, Any] | None = None
    downloads: Mapping[str, Callable[[dict[str, Any]], NodeDocumentDownload]] = field(default_factory=dict)
    max_size_bytes: int = 256 * 1024
