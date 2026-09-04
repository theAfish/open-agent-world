from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api.websocket import websocket_route
from backend.events.hub import EventHub


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
