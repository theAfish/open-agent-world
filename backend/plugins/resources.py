"""Operations on plugin-owned files in host-managed node storage.

Unlike node documents, these handlers operate on native resources (for example
SQLite), not JSON snapshots. The host serializes them with graph mutations and
runs blocking work off the event loop. Plugins own their format and lifecycle.
"""
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from threading import Event
from typing import Any, Callable
from backend.plugins.state import CardStateStore


@dataclass(frozen=True, slots=True)
class NodeMember:
    """A direct member of a container node, as its resource actions see it.

    Read-only by contract: a container may read a member's files to serve its
    own action (for example a search index) but never writes them.
    """
    node_id: str
    type: str
    name: str
    storage_path: Path
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NodeResourceContext:
    node_id: str
    storage_path: Path
    cancelled: Event
    actor_id: str | None = None
    confirmed: bool = False  # Desktop only; never accepted from Agent arguments.
    state: CardStateStore | None = None
    # background(work, commit, abandon=None): run ``work(cancelled)`` off every
    # host lock, then ``commit(context, outcome)`` as a resource write on this node
    # if it still exists. ``outcome`` is work's return value or the exception it
    # raised. work must not touch the graph or storage_path; commit gets a fresh
    # context. ``abandon()`` runs, without host locks, when the commit is skipped
    # or fails while the host keeps running.
    background: Callable[..., None] | None = None
    # Direct members of a container node at the time of the action; empty otherwise.
    members: tuple[NodeMember, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeResourceAction:
    handler: Callable[[NodeResourceContext, dict[str, Any]], dict[str, Any]]
    capability_kind: str | None = None


_FILE_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
_PATTERN_SEGMENT = re.compile(r"[A-Za-z0-9_*][A-Za-z0-9._*-]{0,127}")


def served_file_pattern_valid(pattern: str) -> bool:
    parts = pattern.split("/")
    return len(parts) <= 8 and all(_PATTERN_SEGMENT.fullmatch(part) for part in parts)


def served_file(storage_path: Path, key: str, patterns: tuple[str, ...]) -> Path | None:
    """The file ``key`` names under ``storage_path`` if a pattern serves it, else None."""
    parts = key.split("/")
    if len(parts) > 8 or not all(_FILE_SEGMENT.fullmatch(part) for part in parts):
        return None
    if not any(len(rule := pattern.split("/")) == len(parts) and all(map(fnmatchcase, parts, rule))
               for pattern in patterns):
        return None
    path = storage_path.joinpath(*parts)
    return path if path.is_file() else None
