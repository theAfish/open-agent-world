"""Private persisted input for a Run awaiting dispatch by its profile owner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from backend.request_context import ActorKind, ActorRef, RequestContext, TenantScope


@dataclass(frozen=True)
class RunSubmission:
    prompt: str
    context: RequestContext
    state_session_id: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> RunSubmission:
        value = json.loads(raw)
        context = value["context"]
        actor = context["actor"]
        return cls(
            value["prompt"],
            RequestContext(
                context["request_id"],
                ActorRef(ActorKind(actor["kind"]), actor["id"], actor["display_name"]),
                TenantScope(**context["tenant"]),
                context["auth_method"],
            ),
            value["state_session_id"],
        )
