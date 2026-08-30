from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from backend.events.models import EventType, RuntimeEvent
from backend.services import ApplicationServices


async def event_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    services: ApplicationServices = websocket.app.state.services
    ready = RuntimeEvent(
        type=EventType.CONNECTION_READY,
        payload={"message": "Open Agent World event stream connected"},
    )
    await websocket.send_json(ready.model_dump(mode="json"))
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
                message = receive_task.result()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") == "ping":
                    pong = RuntimeEvent(
                        type=EventType.CONNECTION_READY, payload={"message": "pong"}
                    )
                    await websocket.send_json(pong.model_dump(mode="json"))
            if event_task in done:
                event = event_task.result()
                await websocket.send_json(event.model_dump(mode="json"))


async def websocket_route(websocket: WebSocket) -> None:
    try:
        await event_websocket(websocket)
    except WebSocketDisconnect:
        return
