"""Operations on plugin-owned files in host-managed node storage.

Unlike node documents, these handlers operate on native resources (for example
SQLite), not JSON snapshots. The host serializes them with graph mutations and
runs blocking work off the event loop. Async handlers run on the host event loop
and must offload their own blocking work. Plugins own their format and lifecycle.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Awaitable, Callable
from backend.file_transfer import TransferredFile, file_transfer_policy as _file_transfer_policy
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
    # Bound by the host to this node/action. No provider credential or arbitrary
    # node selection is exposed to a Pack handler.
    open_execution: Callable[..., Any] | None = None
    execution_configuration: Callable[[], dict[str, Any]] | None = None
    # Ephemeral file leases supplied by the host outside JSON/model arguments.
    # Consumers must finish reading before the resource action returns.
    files: Mapping[str, TransferredFile] = field(default_factory=dict)
    # Host-supplied identity of this Agent invocation, never a tool argument.
    # Desktop/standalone calls have no Run. Appended for positional compatibility.
    run_id: str | None = None

    @property
    def file_transfer_policy(self):
        return _file_transfer_policy()


@dataclass(frozen=True, slots=True)
class NodeResourceAction:
    handler: Callable[[NodeResourceContext, dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
    capability_kind: str | None = None
