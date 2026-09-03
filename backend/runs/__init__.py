"""Provider-neutral Run execution and persistence."""

from .models import (
    InvocationCaller,
    InvocationContext,
    RunRecord,
    RunStatus,
    RuntimeInput,
    TERMINAL_RUN_STATUSES,
)
from .store import RunStore

__all__ = [
    "InvocationCaller",
    "InvocationContext",
    "RunManager",
    "RunRecord",
    "RunStatus",
    "RunStore",
    "RuntimeInput",
    "TERMINAL_RUN_STATUSES",
]


def __getattr__(name: str) -> object:
    if name == "RunManager":
        from .manager import RunManager

        return RunManager
    raise AttributeError(name)
