from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_data_root() -> Path:
    configured = os.environ.get("OPEN_AGENT_WORLD_DATA_ROOT")
    if configured:
        return Path(configured).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "OpenAgentWorld"

    # LOCALAPPDATA is always present on supported Windows installations. This
    # branch keeps development and test imports predictable on other platforms.
    return Path.cwd() / ".open-agent-world-data"


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    database_path: Path
    chunk_size: int = 2048
    event_queue_size: int = 256
    agent_runtime: str | None = None
    sandbox_runtime: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        root = _default_data_root().resolve()
        runtime = os.environ.get("OPEN_AGENT_WORLD_AGENT_RUNTIME", "google-adk")
        if runtime not in {"google-adk", "mock"}:
            raise ValueError(
                "OPEN_AGENT_WORLD_AGENT_RUNTIME must be 'google-adk' or 'mock' when set"
            )
        sandbox_runtime = os.environ.get("OPEN_AGENT_WORLD_SANDBOX_RUNTIME")
        if sandbox_runtime is None and os.name == "nt":
            sandbox_runtime = "windows"
        if sandbox_runtime not in {None, "windows"}:
            raise ValueError("OPEN_AGENT_WORLD_SANDBOX_RUNTIME must be 'windows' when set")
        return cls(
            data_root=root,
            database_path=root / "database" / "world.sqlite3",
            agent_runtime=runtime,
            sandbox_runtime=sandbox_runtime,
        )

    @classmethod
    def for_data_root(cls, data_root: str | Path) -> "Settings":
        root = Path(data_root).resolve()
        return cls(data_root=root, database_path=root / "database" / "world.sqlite3")
