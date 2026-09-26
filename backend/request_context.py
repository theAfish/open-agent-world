"""Trusted caller metadata for the current single-profile application.

This boundary records authentication; it does not grant resource access or
provide shared-database tenant isolation. Client headers cannot select an
Actor or TenantScope. Existing deployment and Agent capability checks remain
authoritative until a resource authorization policy is introduced.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Literal
from uuid import uuid4

from starlette.types import Scope

from backend.errors import PermissionDeniedError


@dataclass(frozen=True, slots=True)
class ActorRef:
    kind: Literal["local_host", "host_credential", "deployment_session", "anonymous", "system", "agent"]
    id: str


@dataclass(frozen=True, slots=True)
class TenantScope:
    organization_id: str
    workspace_id: str
    world_id: str


# A compatibility scope for one application profile, not a multi-tenant claim.
LOCAL_TENANT_SCOPE = TenantScope("local", "default", "default")


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    actor: ActorRef
    tenant: TenantScope
    auth_method: str


_request_context: ContextVar[RequestContext | None] = ContextVar("oaw_request_context", default=None)


def current_request_context() -> RequestContext | None:
    return _request_context.get()


def require_request_context() -> RequestContext:
    context = current_request_context()
    if context is None:
        raise PermissionDeniedError("A trusted request context is required")
    return context


@contextmanager
def request_context_scope(context: RequestContext) -> Iterator[RequestContext]:
    token = _request_context.set(context)
    try:
        yield context
    finally:
        _request_context.reset(token)


def establish_request_context(scope: Scope, actor: ActorRef, *, auth_method: str) -> RequestContext:
    """Called only by server authentication boundaries, after their checks.

    Request IDs are provided by the outer observability middleware. Generate
    one for standalone router/middleware use without interpreting any headers.
    """
    state = scope.setdefault("state", {})
    request_id = state.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        request_id = uuid4().hex
        state["request_id"] = request_id
    context = RequestContext(request_id, actor, LOCAL_TENANT_SCOPE, auth_method)
    state["request_context"] = context
    return context


def context_from_scope(scope: Scope) -> RequestContext:
    context = scope.get("state", {}).get("request_context")
    if not isinstance(context, RequestContext):
        raise PermissionDeniedError("A trusted request context is required")
    return context
