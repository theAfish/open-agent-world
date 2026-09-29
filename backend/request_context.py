"""Trusted caller metadata within one profile, not database tenant isolation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
import re
from uuid import uuid4

from starlette.types import Scope

from backend.errors import PermissionDeniedError

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ActorKind(str, Enum):
    """Kinds of principals that may initiate work in OAW."""

    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    AGENT = "agent"
    SYSTEM = "system"
    HOST_CREDENTIAL = "host_credential"
    DEPLOYMENT_SESSION = "deployment_session"
    ANONYMOUS = "anonymous"


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
        raise PermissionDeniedError("A trusted request context is required")
    return context


def establish_request_context(scope: Scope, actor: ActorRef, *, auth_method: str) -> RequestContext:
    """Called only by server authentication boundaries, after their checks.

    Request IDs are provided by the outer observability middleware. Generate
    one for standalone router/middleware use without interpreting any headers.
    """
    state = scope.setdefault("state", {})
    request_id = state.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        request_id = resolve_request_id(None)
        state["request_id"] = request_id
    context = RequestContext(request_id, actor, LOCAL_TENANT, auth_method)
    state["request_context"] = context
    return context


def context_from_scope(scope: Scope) -> RequestContext:
    context = scope.get("state", {}).get("request_context")
    if not isinstance(context, RequestContext):
        raise PermissionDeniedError("A trusted request context is required")
    return context
