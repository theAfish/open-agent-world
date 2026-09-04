from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from backend.events.models import EventType, RuntimeEvent
from backend.services import ApplicationServices


def _is_send_after_close(exc: RuntimeError) -> bool:
    """Recognize only server messages that mean the WebSocket is already closed."""

    message = str(exc)
    return (
        message == 'Cannot call "send" once a close message has been sent.'
        or message.startswith(
            "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'"
        )
    )


async def _send_json(websocket: WebSocket, payload: dict[str, object]) -> bool:
    """Send one event, returning false only for a known disconnect boundary."""

    try:
        await websocket.send_json(payload)
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if _is_send_after_close(exc):
            return False
        raise
    return True


async def event_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    services: ApplicationServices = websocket.app.state.services
    ready = RuntimeEvent(
        type=EventType.CONNECTION_READY,
        payload={"message": "Open Agent World event stream connected"},
    )
    if not await _send_json(websocket, ready.model_dump(mode="json")):
        return
    async with services.events.subscribe() as queue:
        while True:
            receive_task = asyncio.create_task(websocket.receive())
            event_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {receive_task, event_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if receive_task in done:
                try:
                    message = receive_task.result()
                except WebSocketDisconnect:
                    return
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") == "ping":
                    pong = RuntimeEvent(
                        type=EventType.CONNECTION_READY, payload={"message": "pong"}
                    )
                    if not await _send_json(websocket, pong.model_dump(mode="json")):
                        return
            if event_task in done:
                event = event_task.result()
                if not await _send_json(websocket, event.model_dump(mode="json")):
                    return


async def websocket_route(websocket: WebSocket) -> None:
    await event_websocket(websocket)
