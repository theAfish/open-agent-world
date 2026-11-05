"""Execution-scoped access to an existing OAW model, without exporting its key."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
import hmac
import json
import secrets
from urllib.parse import urlsplit

import h11
import httpx

from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.security.model_connections import DEFAULT_MODEL_BASE_URLS

REQUEST_LIMIT = 32 * 1024 * 1024
RESPONSE_LIMIT = 8 * 1024 * 1024
_ROUTES = {
    "openai": {"/v1/chat/completions": "chat/completions", "/v1/responses": "responses"},
    "anthropic": {"/v1/messages": "v1/messages", "/v1/messages/count_tokens": "v1/messages/count_tokens"},
}


class RuntimeModelProxy:
    def __init__(self, resolve, reference, active):
        if not isinstance(reference, str) or not (reference == "oaw:default" or reference.startswith("oaw:model:")):
            raise ResourceValidationError("Choose an OAW model connection or oaw:default")
        self.resolve, self.reference, self.active = resolve, reference, active
        self.connection = resolve(reference)
        if self.connection.adapter not in _ROUTES:
            raise ResourceValidationError("This Harness model connection requires an OpenAI or Anthropic compatible endpoint")
        try:
            parsed = urlsplit(self.connection.base_url or DEFAULT_MODEL_BASE_URLS[self.connection.adapter])
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError()
        except ValueError as error:
            raise ResourceValidationError('Model connection has an invalid base URL') from error
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(c)<32 or c=='\\' for c in self.connection.base_url)):
            raise ResourceValidationError("Model connection needs an HTTP(S) base URL without credentials or query")
        self.token = secrets.token_urlsafe(32)
        self.server = self.client = None
        self.tasks = set()
        self.revoked = self.closed = False
        self.close_lock = asyncio.Lock()

    async def open(self, execution):
        if not self.active() or self.revoked:
            raise RuntimeUnavailableError("Model execution has ended")
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(600, connect=10), limits=httpx.Limits(max_connections=4))
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=16384)
        url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/"
        forwarded = await execution.forward_http(url, path_prefix=True, stream=True)
        if not self.active() or self.revoked:
            raise RuntimeUnavailableError("Model execution has ended")
        base = forwarded.rstrip("/") + ("/v1" if self.connection.adapter == "openai" else "")
        return replace(self.connection, base_url=base, api_key=self.token)

    @staticmethod
    async def _send(connection, writer, event):
        writer.write(connection.send(event))
        await writer.drain()

    async def _error(self, connection, writer, status, message):
        body = json.dumps({"error": {"message": message}}).encode()
        await self._send(connection, writer, h11.Response(status_code=status, headers=[
            ("content-type", "application/json"), ("content-length", str(len(body))), ("connection", "close")]))
        await self._send(connection, writer, h11.Data(data=body))
        await self._send(connection, writer, h11.EndOfMessage())

    async def _serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        wire = h11.Connection(h11.SERVER, max_incomplete_event_size=16384)
        try:
            if self.revoked:
                return
            body = bytearray()
            request = None
            async with asyncio.timeout(30):
                while True:
                    event = wire.next_event()
                    if event is h11.NEED_DATA:
                        data = await reader.read(16384)
                        if not data:
                            return
                        wire.receive_data(data)
                    elif isinstance(event, h11.Request):
                        request = event
                        pairs = [(key.decode("ascii").lower(), value.decode("latin-1")) for key, value in event.headers]
                        incoming = dict(pairs)
                        if len(incoming) != len(pairs):
                            return await self._error(wire, writer, 400, "Duplicate request headers")
                        supplied = incoming.get("authorization", "")
                        if self.connection.adapter == "anthropic":
                            supplied = "Bearer " + incoming.get("x-api-key", "")
                        if self.revoked or not self.active() or not hmac.compare_digest(supplied.encode('latin-1'), ("Bearer " + self.token).encode('ascii')):
                            return await self._error(wire, writer, 401, "Model execution is not authorized")
                        target = urlsplit(event.target.decode("ascii"))
                        if (event.method != b"POST" or target.scheme or target.netloc or target.fragment
                                or target.path not in _ROUTES[self.connection.adapter]
                                or target.query not in ({'', 'beta=true'} if self.connection.adapter == 'anthropic' else {''})):
                            return await self._error(wire, writer, 404, "Model endpoint is not available")
                    elif isinstance(event, h11.Data):
                        body.extend(event.data)
                        if len(body) > REQUEST_LIMIT:
                            return await self._error(wire, writer, 413, "Model request exceeds its limit")
                    elif isinstance(event, h11.EndOfMessage):
                        break
                    else:
                        return
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or payload.get("model", self.connection.model_id) != self.connection.model_id:
                    raise ValueError()
                payload["model"] = self.connection.model_id
            except (ValueError, TypeError):
                return await self._error(wire, writer, 400, "Use the selected model and a JSON object")
            try:
                current = self.resolve(self.reference)
            except Exception:
                return await self._error(wire, writer, 403, "Configured model is unavailable")
            identity = lambda model: (model.adapter, model.model_id, model.base_url)
            if self.revoked or not self.active() or identity(current) != identity(self.connection):
                return await self._error(wire, writer, 403, "Model connection changed; start a new execution")
            suffix = _ROUTES[current.adapter][target.path]
            base = (current.base_url or DEFAULT_MODEL_BASE_URLS[current.adapter]).rstrip("/")
            if base.endswith("/v1") and suffix.startswith("v1/"):
                suffix = suffix[3:]
            headers = {"content-type": "application/json", "accept": "text/event-stream, application/json"}
            if current.adapter == "anthropic":
                headers['anthropic-version'] = '2023-06-01'
                if current.api_key:
                    headers['x-api-key'] = current.api_key
                if beta := incoming.get('anthropic-beta'):
                    headers['anthropic-beta'] = beta
            elif current.api_key:
                headers["authorization"] = "Bearer " + current.api_key
            try:
                url = base + "/" + suffix + ('?' + target.query if target.query else '')
                async with self.client.stream("POST", url, json=payload, headers=headers) as response:
                    if not 200 <= response.status_code < 300:
                        # Provider error text and headers can echo its credentials.
                        status = response.status_code if response.status_code >= 400 else 502
                        return await self._error(wire, writer, status, "Configured model rejected the request")
                    media = response.headers.get("content-type", "application/json")
                    await self._send(wire, writer, h11.Response(status_code=response.status_code, headers=[
                        ("content-type", media), ("cache-control", "no-store"), ("connection", "close")]))
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > RESPONSE_LIMIT:
                            return
                        await self._send(wire, writer, h11.Data(data=chunk))
                    await self._send(wire, writer, h11.EndOfMessage())
            except httpx.HTTPError:
                if wire.our_state is h11.SEND_RESPONSE:
                    await self._error(wire, writer, 502, "Configured model is unreachable")
        except (ValueError, UnicodeError, OSError, TimeoutError, h11.ProtocolError):
            pass  # Neither request bodies nor upstream credentials enter logs.
        finally:
            self.tasks.discard(task)
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def close(self):
        async with self.close_lock:
            if self.closed:
                return
            self.revoked = True
            if self.server is not None:
                self.server.close()
            for task in tuple(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.server is not None:
                await self.server.wait_closed()
            if self.client is not None:
                await self.client.aclose()
            self.closed = True
