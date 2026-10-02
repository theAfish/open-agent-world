"""The small context every action handler reads, without any host in it.

``actions.py`` only ever touches five attributes — ``node_id``, ``storage_path``,
``state``, ``actor_id`` and ``confirmed``. OAW's ``NodeResourceContext`` happens to
expose exactly those names, so the card passes its own context straight through and
only the service and the CLI need the dataclass below.

``state`` follows OAW's ``CardStateStore`` shape: ``get()`` returns
``{"value": {...}, "revision": n}`` and ``update(patch)`` shallow-merges.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class KnowledgeContext:
    """A single caller's view of one store, outside OAW."""

    node_id: str
    storage_path: Path
    state: Any = None
    actor_id: str | None = None
    confirmed: bool = False


class MemorySettings:
    """The state contract backed by a dict; the default for tests and one-shot calls."""

    def __init__(self, value=None):
        self._value = dict(value or {})
        self._revision = 0
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            return {"value": json.loads(json.dumps(self._value)), "revision": self._revision}

    def set(self, value, expected_revision=None):
        with self._lock:
            self._check(expected_revision)
            self._value = json.loads(json.dumps(dict(value)))
            self._revision += 1
            return {"value": dict(self._value), "revision": self._revision}

    def update(self, patch, expected_revision=None):
        with self._lock:
            self._check(expected_revision)
            self._value = {**self._value, **json.loads(json.dumps(dict(patch)))}
            self._revision += 1
            return {"value": dict(self._value), "revision": self._revision}

    def delete(self, expected_revision=None):
        return self.set({}, expected_revision)

    def _check(self, expected_revision):
        if expected_revision is not None and expected_revision != self._revision:
            raise ConflictingRevision(
                f"Settings changed underneath this call (revision {self._revision})")


class ConflictingRevision(RuntimeError):
    """Raised when an optimistic settings write loses a race."""


class JsonSettings(MemorySettings):
    """The same contract, persisted to one small JSON file next to the store."""

    def __init__(self, path):
        self.path = Path(path)
        loaded, revision = {}, 0
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text("utf-8"))
            except (OSError, ValueError):
                stored = {}
            if isinstance(stored, dict):
                loaded = stored.get("value") if isinstance(stored.get("value"), dict) else stored
                revision = stored.get("revision") if isinstance(stored.get("revision"), int) else 0
        super().__init__(loaded)
        self._revision = revision

    def set(self, value, expected_revision=None):
        result = super().set(value, expected_revision)
        self._write(result)
        return result

    def update(self, patch, expected_revision=None):
        result = super().update(patch, expected_revision)
        self._write(result)
        return result

    def _write(self, result):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash never leaves a half-written settings file.
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(result, indent=2, sort_keys=True), "utf-8")
        temporary.replace(self.path)


@dataclass
class PinnedSettings:
    """Reads through to ``inner`` with a few settings forced; writes go to ``inner``.

    The service pins ``collection_name`` from the request path so a caller cannot
    reach another collection by writing a setting.
    """

    inner: Any
    pinned: dict = field(default_factory=dict)

    def get(self):
        result = self.inner.get()
        value = dict(result.get("value") or {})
        settings = dict(value.get("settings") or {})
        settings.update(self.pinned)
        value["settings"] = settings
        return {"value": value, "revision": result.get("revision", 0)}

    def set(self, value, expected_revision=None):
        return self.inner.set(value, expected_revision)

    def update(self, patch, expected_revision=None):
        patch = dict(patch)
        if isinstance(patch.get("settings"), dict):
            patch["settings"] = {key: item for key, item in patch["settings"].items()
                                 if key not in self.pinned}
        return self.inner.update(patch, expected_revision)

    def delete(self, expected_revision=None):
        return self.inner.delete(expected_revision)
