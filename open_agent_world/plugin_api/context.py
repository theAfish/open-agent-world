"""Profile-scoped context supplied to opt-in Pack factories."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PluginContext:
    data_root: Path
    source_root: Path | None = None
