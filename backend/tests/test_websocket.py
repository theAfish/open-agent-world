from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from backend.api.websocket import websocket_route
from backend.events.hub import EventHub
from backend.events.models import EventType, RuntimeEvent


class _FakeWebSocket:
    def __init__(self, *, send_error: Exception | None = None) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(services=SimpleNamespace(events=EventHub()))
        )
        self.send_error = send_error
        self.sent: list[dict[str, object]] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict[str, object]) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(payload)

    async def receive(self) -> dict[str, object]:
        return {"type": "websocket.disconnect", "code": 1000}


@pytest.mark.asyncio
async def test_websocket_disconnect_message_ends_connection_cleanly() -> None:
    websocket = _FakeWebSocket()

    await websocket_route(websocket)  # type: ignore[arg-type]

    assert websocket.sent[0]["type"] == "connection_ready"


@pytest.mark.asyncio
async def test_websocket_send_after_close_is_a_disconnect_boundary() -> None:
    websocket = _FakeWebSocket(
        send_error=RuntimeError('Cannot call "send" once a close message has been sent.')
    )

    await websocket_route(websocket)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_websocket_does_not_hide_unrelated_runtime_errors() -> None:
    websocket = _FakeWebSocket(send_error=RuntimeError("serializer invariant failed"))

    with pytest.raises(RuntimeError, match="serializer invariant failed"):
        await websocket_route(websocket)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_route_cancellation_joins_receive_and_queue_waits() -> None:
    receiving = asyncio.Event()
    finished = asyncio.Event()

    class BlockedSocket(_FakeWebSocket):
        async def receive(self):
            receiving.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

    websocket = BlockedSocket()
    before = asyncio.all_tasks()
    route = asyncio.create_task(websocket_route(websocket))
    await asyncio.wait_for(receiving.wait(), 1)
    route.cancel()
    with suppress(asyncio.CancelledError):
        await route
    assert finished.is_set()
    assert not websocket.app.state.services.events._subscribers
    assert not {task for task in asyncio.all_tasks() - before if not task.done()}


@pytest.mark.asyncio
async def test_heartbeat_never_skips_queued_events_or_restarts_the_pending_receiver() -> None:
    delivered = asyncio.Event()

    class QueuedSocket(_FakeWebSocket):
        receives = 0

        async def receive(self):
            self.receives += 1
            if self.receives == 1:
                for _ in range(3):
                    self.app.state.services.events.publish_event_nowait(RuntimeEvent(type=EventType.CARD_UPDATED))
                return {"type": "websocket.receive", "text": "ping"}
            await delivered.wait()
            return {"type": "websocket.disconnect"}

        async def send_json(self, payload):
            await super().send_json(payload)
            if payload["type"] == "card_updated" and payload["sequence"] == 3:
                delivered.set()

    websocket = QueuedSocket()
    await asyncio.wait_for(websocket_route(websocket), 1)
    pong = next(i for i, event in enumerate(websocket.sent) if event["payload"].get("message") == "pong")
    assert websocket.sent[pong]["sequence"] == max(event["sequence"] for event in websocket.sent[:pong])
    assert [event["sequence"] for event in websocket.sent if event["type"] == "card_updated"] == [1, 2, 3]
    assert websocket.receives == 2
