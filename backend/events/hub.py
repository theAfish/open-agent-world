from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from backend.events.models import EventType, RuntimeEvent


class EventHub:
    def __init__(self, *, queue_size: int = 256) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self.queue_size = queue_size
        self.stream_id = str(uuid4())
        self.sequence = 0
        self._subscribers: set[asyncio.Queue[RuntimeEvent]] = set()
        self._lock = asyncio.Lock()
        self._buffer: ContextVar[list[RuntimeEvent] | None] = ContextVar("event_buffer", default=None)

    @contextmanager
    def committed_batch(self):
        """Buffer synchronous mutation events until their transaction commits."""
        if self._buffer.get() is not None:
            yield
            return
        events: list[RuntimeEvent] = []
        token = self._buffer.set(events)
        try:
            yield
        except BaseException:
            raise
        else:
            self._buffer.reset(token)
            token = None
            for event in events:
                self.publish_event_nowait(event)
        finally:
            if token is not None:
                self._buffer.reset(token)

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
        self.publish_event_nowait(event)
        return event

    async def publish_event(self, event: RuntimeEvent) -> None:
        self.publish_event_nowait(event)

    def publish_event_nowait(self, event: RuntimeEvent) -> None:
        """Publish a synchronously committed state change to live subscribers.

        State persistence is authoritative, so this intentionally does not make
        event delivery part of the database transaction.
        """

        pending = self._buffer.get()
        if pending is not None:
            pending.append(event)
            return
        self.sequence += 1
        event = event.model_copy(update={"stream_id": self.stream_id, "sequence": self.sequence})
        for queue in tuple(self._subscribers):
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
