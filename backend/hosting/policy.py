"""Restrictions for hosted workers; desktop retains its full management API."""
from __future__ import annotations

import json
from pathlib import PurePosixPath, PureWindowsPath
import re

from starlette.responses import JSONResponse


_HOST_FIELDS = frozenset({"workspace_path", "workspace_root", "working_directory", "cwd", "project_path",
                          "host_path", "local_path", "folders", "folder_bindings", "plugin_directories"})
_BLOCKED_PREFIXES = ("/api/desktop", "/api/settings/storage", "/api/diagnostics", "/api/development",
                     "/api/store", "/api/runtime/python/plugins", "/api/settings/sandbox")
_PACK_ASSET = re.compile(r"/api/packs/[^/]+/versions/[^/]+/.+")


def unsafe_host_configuration(value) -> bool:
    """Configuration authority stays with the operator, including nested forms."""
    if isinstance(value, dict):
        if any(key in _HOST_FIELDS and item not in (None, "", [], {}) for key, item in value.items()):
            return True
        # Plugin actions may accept a generic path (for example local XRD
        # library mounts). Remote/workspace-relative paths remain supported.
        for key, item in value.items():
            if key in {"path", "source_path", "destination"} and isinstance(item, str) and item:
                normalized = item.replace("\\", "/")
                if (PurePosixPath(normalized).is_absolute() or PureWindowsPath(item).drive
                        or normalized.startswith("~") or ".." in normalized.split("/")):
                    return True
        if value.get("runtime_provider_id") == "openai.codex" or value.get("type") == "openai.codex.agent":
            return True
        if value.get("action") == "set_plugin_enabled":
            return True
        return any(unsafe_host_configuration(item) for item in value.values())
    if isinstance(value, list):
        return any(unsafe_host_configuration(item) for item in value)
    return False


class HostedPolicyMiddleware:
    """Deny host management even when an authenticated user constructs requests."""

    def __init__(self, app, *, managed_models=False):
        self.app, self.managed_models = app, managed_models

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        original_send = send

        async def secure_send(message):
            if message["type"] == "http.response.start":
                protected = {b"cache-control": b"no-store", b"x-content-type-options": b"nosniff",
                             b"referrer-policy": b"no-referrer", b"x-frame-options": b"DENY"}
                message = {**message, "headers": [(key, value) for key, value in message.get("headers", [])
                                                  if key.lower() not in protected] + list(protected.items())}
            await original_send(message)

        send = secure_send
        path = scope["path"].rstrip("/")
        method = scope.get("method", "GET")
        blocked = any(path == prefix or path.startswith(prefix + "/") for prefix in _BLOCKED_PREFIXES)
        blocked |= path.startswith("/api/packs") and not (method in {"GET", "HEAD"} and _PACK_ASSET.fullmatch(path))
        blocked |= path.endswith(("/environment-folders", "/files/open-folder"))
        blocked |= bool(re.fullmatch(r"/api/sandboxes/[^/]+/diagnostics", path))
        blocked |= path == "/ws/visual"  # Desktop observation is an operator feature.
        blocked |= (self.managed_models and method not in {"GET", "HEAD", "OPTIONS"}
                    and (path == "/api/settings/llm" or path == "/api/settings/models" or path.startswith("/api/settings/models/")))
        if blocked:
            return await self._deny(scope, receive, send)
        # Inspect only configuration-bearing routes. Binary attachments and user
        # conversation text stream to their ordinary bounded upload handlers.
        inspect = (scope["type"] == "http" and method not in {"GET", "HEAD", "OPTIONS"}
                   and path.startswith(("/api/nodes", "/api/card-library", "/api/legions", "/api/legion-groups",
                                        "/api/runtime-app/workspace/nodes", "/api/settings/models")))
        if inspect:
            messages, size = [], 0
            while True:
                message = await receive()
                messages.append(message)
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > 4 * 1024 * 1024:
                    return await self._deny(scope, receive, send, 413)
                if not message.get("more_body"):
                    break
            try:
                payload = json.loads(b"".join(message.get("body", b"") for message in messages))
            except (ValueError, UnicodeError):
                payload = None  # The route retains ordinary schema validation.
            if unsafe_host_configuration(payload):
                return await self._deny(scope, receive, send)
            # A browser must not turn a remote-Sandbox service credential from
            # worker.env into an API key sent to an arbitrary model endpoint.
            if path.startswith("/api/settings/models") and self._environment_auth(payload):
                return await self._deny(scope, receive, send)
            cursor = iter(messages)

            async def replay():
                try:
                    return next(cursor)
                except StopIteration:
                    return await receive()

            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)

    @staticmethod
    def _environment_auth(value):
        if isinstance(value, dict):
            return (value.get("auth_mode") == "environment" or bool(value.get("environment_variable"))
                    or any(HostedPolicyMiddleware._environment_auth(item) for item in value.values()))
        if isinstance(value, list):
            return any(HostedPolicyMiddleware._environment_auth(item) for item in value)
        return False

    @staticmethod
    async def _deny(scope, receive, send, status=403):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Hosted operation unavailable"})
            return
        await JSONResponse({"error": {"code": "hosted_operation_unavailable",
                                     "message": "This operation is managed by the hosting operator."}},
                           status_code=status, headers={"Cache-Control": "no-store"})(scope, receive, send)
