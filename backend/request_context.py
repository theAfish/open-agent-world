from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ActorKind(str, Enum):
    """Kinds of principals that may initiate work in OAW."""

    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass(frozen=True)
class ActorRef:
    """Stable identity of the principal responsible for an operation."""

    kind: ActorKind
    id: str
    display_name: str | None = None


@dataclass(frozen=True)
class TenantScope:
    """Ownership boundary for resources touched by a request."""

    organization_id: str
    workspace_id: str
    world_id: str | None = None


@dataclass(frozen=True)
class RequestContext:
    """Identity, ownership scope, and correlation data for one request."""

    request_id: str
    actor: ActorRef
    tenant: TenantScope
    auth_method: str


LOCAL_ACTOR = ActorRef(
    kind=ActorKind.USER,
    id="local-user",
    display_name="Local user",
)
LOCAL_TENANT = TenantScope(
    organization_id="local-organization",
    workspace_id="local-workspace",
    world_id="local-world",
)

_active_request_context: ContextVar[RequestContext | None] = ContextVar(
    "oaw_request_context",
    default=None,
)


def resolve_request_id(value: str | None) -> str:
    """Return a safe caller correlation ID or generate a new one."""

    if value is not None and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return f"req_{uuid4().hex}"


def create_local_request_context(request_id: str | None = None) -> RequestContext:
    """Create the implicit personal context used by desktop installations."""

    return RequestContext(
        request_id=resolve_request_id(request_id),
        actor=LOCAL_ACTOR,
        tenant=LOCAL_TENANT,
        auth_method="local",
    )


@contextmanager
def bind_request_context(context: RequestContext) -> Iterator[RequestContext]:
    """Bind a context to the current async execution flow and restore it safely."""

    token = _active_request_context.set(context)
    try:
        yield context
    finally:
        _active_request_context.reset(token)


def current_request_context() -> RequestContext | None:
    """Return the current context when called inside a request or delegated task."""

    return _active_request_context.get()


def require_request_context() -> RequestContext:
    """Return the current context, failing fast when propagation was omitted."""

    context = current_request_context()
    if context is None:
        raise RuntimeError("No OAW request context is bound")
    return context


class RequestContextMiddleware:
    """Bind the implicit desktop principal and a safe request ID to ASGI requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        request_ids = Headers(scope=scope).getlist(REQUEST_ID_HEADER)
        supplied_request_id = request_ids[0] if len(request_ids) == 1 else None
        context = create_local_request_context(supplied_request_id)
        scope.setdefault("state", {})["request_context"] = context

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = context.request_id
            await send(message)

        effective_send = send_with_request_id if scope["type"] == "http" else send
        with bind_request_context(context):
            await self.app(scope, receive, effective_send)
