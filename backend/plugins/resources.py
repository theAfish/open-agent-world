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
    # Trusted backend code only. Values must never enter state, job parameters,
    # action responses or exports. Resolve at execution time, not when queueing.
    resolve_secret: Callable[[str], str | None] | None = None
    # Resolves an OAW model reference ("oaw:default" or "oaw:model:...") to a
    # ``RuntimeModelConnection`` for a plugin that must call a model itself (for
    # example to transcribe an image inside its own conversion job). Same rules as
    # ``resolve_secret``: the connection's key never leaves the handler.
    resolve_model: Callable[[str], Any] | None = None


@dataclass(frozen=True, slots=True)
class NodeResourceAction:
    handler: Callable[[NodeResourceContext, dict[str, Any]], dict[str, Any]]
    capability_kind: str | None = None
