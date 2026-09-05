from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_data_root() -> Path:
    configured = os.environ.get("OPEN_AGENT_WORLD_DATA_ROOT")
    if configured:
        return Path(configured).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local_app_data:
        return Path(local_app_data) / "OpenAgentWorld"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenAgentWorld"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "open-agent-world"


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    database_path: Path
    chunk_size: int = 2048
    event_queue_size: int = 256
    agent_runtime: str | None = None
    sandbox_runtime: str | None = None
    run_inactivity_timeout_seconds: float | None = 300.0

    @classmethod
    def from_environment(cls) -> "Settings":
        root = _default_data_root().resolve()
        runtime = os.environ.get("OPEN_AGENT_WORLD_AGENT_RUNTIME", "google.adk")
        runtime = {"google-adk": "google.adk", "mock": "core.mock"}.get(
            runtime, runtime
        )
        if runtime is not None and not runtime.strip():
            raise ValueError("OPEN_AGENT_WORLD_AGENT_RUNTIME must not be empty")
        sandbox_runtime = os.environ.get("OPEN_AGENT_WORLD_SANDBOX_RUNTIME", "auto")
        if not sandbox_runtime.strip() or "\x00" in sandbox_runtime:
            raise ValueError("OPEN_AGENT_WORLD_SANDBOX_RUNTIME must be a non-empty runtime ID")
        configured_timeout = os.environ.get("OPEN_AGENT_WORLD_RUN_INACTIVITY_TIMEOUT")
        inactivity_timeout: float | None = 300.0
        if configured_timeout is not None:
            try:
                parsed = float(configured_timeout)
            except ValueError as exc:
                raise ValueError(
                    "OPEN_AGENT_WORLD_RUN_INACTIVITY_TIMEOUT must be a number of seconds"
                ) from exc
            inactivity_timeout = parsed if parsed > 0 else None
        return cls(
            data_root=root,
            database_path=root / "database" / "world.sqlite3",
            agent_runtime=runtime,
            sandbox_runtime=sandbox_runtime,
            run_inactivity_timeout_seconds=inactivity_timeout,
        )

    @classmethod
    def for_data_root(cls, data_root: str | Path) -> "Settings":
        root = Path(data_root).resolve()
        return cls(data_root=root, database_path=root / "database" / "world.sqlite3")
