from __future__ import annotations

import asyncio

from anyio import CancelScope
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
    async with services.events.subscribe() as queue:
        ready = RuntimeEvent(
            type=EventType.CONNECTION_READY,
            stream_id=services.events.stream_id, sequence=services.events.sequence,
            payload={"message": "Open Agent World event stream connected"},
        )
        if not await _send_json(websocket, ready.model_dump(mode="json")):
            return
        last_sent_sequence = ready.sequence
        receive_task = asyncio.create_task(websocket.receive())
        event_task = asyncio.create_task(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {receive_task, event_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if receive_task in done:
                    try:
                        message = receive_task.result()
                    except WebSocketDisconnect:
                        return
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") == "ping":
                        # Never acknowledge events still queued for this client:
                        # its sequence filter would discard them after the pong.
                        pong = RuntimeEvent(
                            type=EventType.CONNECTION_READY, payload={"message": "pong"},
                            stream_id=services.events.stream_id, sequence=last_sent_sequence,
                        )
                        if not await _send_json(websocket, pong.model_dump(mode="json")):
                            return
                    receive_task = asyncio.create_task(websocket.receive())
                if event_task in done:
                    event = event_task.result()
                    if not await _send_json(websocket, event.model_dump(mode="json")):
                        return
                    last_sent_sequence = event.sequence
                    event_task = asyncio.create_task(queue.get())
        finally:
            # Join both waits on disconnect, send failure and route cancellation.
            for task in (receive_task, event_task):
                if not task.done():
                    task.cancel()
            # ASGI servers cancel their scope on disconnect; shield the join so
            # that cancellation cannot strand either child wait during cleanup.
            with CancelScope(shield=True):
                await asyncio.gather(receive_task, event_task, return_exceptions=True)


async def websocket_route(websocket: WebSocket) -> None:
    await event_websocket(websocket)
