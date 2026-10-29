"""Permission-scoped backend observation of one authoritative Agent Run."""
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


@dataclass(frozen=True, slots=True)
class ObservedRun:
    run_id: str
    agent_id: str
    status: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None  # Safe category summary, never the raw exception.


class RunSubscription(Protocol):
    async def close(self) -> None: ...


class RunObservations(Protocol):
    async def read(self, viewer_id: str, agent_id: str, run_id: str) -> ObservedRun: ...

    async def watch(
        self, viewer_id: str, agent_id: str, run_id: str,
        callback: Callable[[ObservedRun], Awaitable[None]],
    ) -> RunSubscription:
        """Deliver an initial snapshot, then changed snapshots until closed.

        Callbacks are serialized per subscription and must be bounded, async,
        and idempotent. They may use host resource actions for blocking writes.
        Registrations are process-local; restore them from plugin state at startup.
        Initial registration requires live observation permission. Revocation
        pauses delivery; reauthorization reconciles the same Run automatically.
        Deletion, explicit close, or host shutdown ends the watch.
        """
        ...
