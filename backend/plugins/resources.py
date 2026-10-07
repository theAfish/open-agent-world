"""Operations on plugin-owned files in host-managed node storage.

Unlike node documents, these handlers operate on native resources (for example
SQLite), not JSON snapshots. The host serializes them with graph mutations and
runs blocking work off the event loop. Plugins own their format and lifecycle.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable
from backend.plugins.state import CardStateStore


@dataclass(frozen=True, slots=True)
class NodeResourceContext:
    node_id: str
    storage_path: Path
    cancelled: Event
    actor_id: str | None = None
    confirmed: bool = False  # Desktop only; never accepted from Agent arguments.
    state: CardStateStore | None = None

    @property
    def invocation(self) -> dict[str, Any] | None:
        from backend.operation_associations import active_operation
        context = active_operation.get()
        return context.payload() if context else None

    def associate_execution(self, kind: str, reference_id: str, *, object_id: str | None = None,
                            produced: bool = False) -> dict[str, Any] | None:
        from backend.operation_associations import associate_execution
        reference = associate_execution(kind, reference_id, object_id=object_id, produced=produced)
        return reference.model_dump(mode="json") if reference else None


@dataclass(frozen=True, slots=True)
class NodeResourceAction:
    handler: Callable[[NodeResourceContext, dict[str, Any]], dict[str, Any]]
    capability_kind: str | None = None
