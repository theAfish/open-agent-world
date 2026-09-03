from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.events.models import EventType, RuntimeEvent


class EventHub:
    def __init__(self, *, queue_size: int = 256) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self.queue_size = queue_size
        self._subscribers: set[asyncio.Queue[RuntimeEvent]] = set()
        self._lock = asyncio.Lock()

    async def publish(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        agent_id: str | None = None,
        sandbox_id: str | None = None,
        resource_id: str | None = None,
        conversation_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            type=event_type,
            node_id=node_id,
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            resource_id=resource_id,
            conversation_id=conversation_id,
            session_id=session_id,
            run_id=run_id,
            payload=payload or {},
        )
        async with self._lock:
            queues = tuple(self._subscribers)
        for queue in queues:
            if queue.full():
                # Runtime output is a live view rather than an audit log. Keep
                # the newest operational state for slow clients.
                queue.get_nowait()
            queue.put_nowait(event)
        return event

    async def publish_event(self, event: RuntimeEvent) -> None:
        async with self._lock:
            queues = tuple(self._subscribers)
        for queue in queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[RuntimeEvent]]:
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
