"""Versioned Codex JSON-RPC over a private child process, never a shell."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from contextlib import suppress
from typing import Any, Awaitable, Callable

from open_agent_world.plugin_api import AgentRuntimeError


class AppServer:
    def __init__(self, command: list[str], cwd: str,
                 handler: Callable[[str, dict], Awaitable[dict]]) -> None:
        self.command, self.cwd, self.handler = command, cwd, handler
        self.events: asyncio.Queue[dict | Exception] = asyncio.Queue()
        self.pending: dict[int, asyncio.Future] = {}
        self.requests: set[asyncio.Task] = set()
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr: asyncio.Task | None = None
        self.sequence = 0
        self.closing = False
        self.close_task: asyncio.Task | None = None

    async def start(self) -> None:
        options: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
            else {"start_new_session": True}
        )
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024, **options,
            )
        except OSError as exc:
            raise AgentRuntimeError(
                "Cannot launch Codex. Install Codex CLI and set OAW_CODEX_COMMAND "
                "to its executable if it is not on PATH."
            ) from exc
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())
        await self.request("initialize", {
            "clientInfo": {"name": "open_agent_world", "title": "Open Agent World", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self.send({"method": "initialized", "params": {}})

    async def send(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None or self.process.returncode is not None:
            raise AgentRuntimeError("Codex App Server is not running")
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise AgentRuntimeError(f"Codex did not answer {method} within {timeout:g} seconds") from exc
        finally:
            self.pending.pop(request_id, None)

    async def _answer(self, message: dict) -> None:
        try:
            result = await self.handler(message["method"], message.get("params", {}))
            await self.send({"id": message["id"], "result": result})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with suppress(Exception):
                await self.send({"id": message["id"], "error": {"code": -32603, "message": str(exc)}})
            await self.events.put(exc)

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        failure: Exception = AgentRuntimeError("Codex App Server exited before the turn completed")
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        task = asyncio.create_task(self._answer(message))
                        self.requests.add(task)
                        task.add_done_callback(self.requests.discard)
                    else:
                        await self.events.put(message)
                elif (future := self.pending.get(message.get("id"))) is not None and not future.done():
                    if "error" in message:
                        future.set_exception(AgentRuntimeError(
                            f"Codex: {message['error'].get('message', 'request failed')}"
                        ))
                    else:
                        future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = AgentRuntimeError(f"Invalid Codex protocol stream: {exc}")
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            if not self.closing:
                await self.events.put(failure)

    async def _drain_stderr(self) -> None:
        # Logs may contain user configuration; don't persist or forward them.
        assert self.process and self.process.stderr
        while await self.process.stderr.read(8192):
            pass

    async def close(self) -> None:
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self.close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self.close_task)
            raise

    async def _close(self) -> None:
        self.closing = True
        for task in tuple(self.requests):
            task.cancel()
        await asyncio.gather(*self.requests, return_exceptions=True)
        process = self.process
        if process is not None:
            if os.name == "nt" and process.returncode is None:
                # This PID belongs to our private app-server. Stop its command tree too.
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await killer.wait()
            elif os.name != "nt":
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
        for task in (self.reader, self.stderr):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
