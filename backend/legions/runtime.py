"""Team context built on world membership and the authoritative StateStore."""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import ResourceValidationError
from backend.state import StateContext

if TYPE_CHECKING:
    from backend.state import StateStore
    from backend.world.models import Card
    from backend.world.store import WorldStore


class LegionStateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: dict[str, Any]
    expected_revision: int = Field(ge=0)


def read_shared_state(world: WorldStore, state: StateStore, legion_id: str) -> dict[str, Any]:
    if world.get_card(legion_id).type != "legion":
        raise ResourceValidationError("Shared state belongs to a Legion card")
    scope = state.ensure_scope("legion", legion_id, schema_id="core.legion")
    resolved = state.resolve(StateContext((scope,)), "shared_working_memory")
    return {"value": resolved.value, "revision": resolved.revision}


def write_shared_state(
    world: WorldStore, state: StateStore, legion_id: str, request: LegionStateWrite,
    *, actor_id: str | None = None, run_id: str | None = None, merge: bool = False,
) -> dict[str, Any]:
    read_shared_state(world, state, legion_id)
    if len(json.dumps(request.value).encode("utf-8")) > 64 * 1024:
        raise ResourceValidationError("Legion shared state is limited to 64 KiB")
    scope = state.get_scope("legion", legion_id)
    # Validate the resulting merged value, not just the incoming patch.
    if merge:
        current = read_shared_state(world, state, legion_id)["value"]
        combined = {**current, **request.value}
        if len(json.dumps(combined).encode("utf-8")) > 64 * 1024:
            raise ResourceValidationError("Legion shared state is limited to 64 KiB")
    mutation = state.patch if merge else state.set
    mutation(scope, "shared_working_memory", request.value,
             expected_revision=request.expected_revision, actor_id=actor_id, run_id=run_id)
    return read_shared_state(world, state, legion_id)


def group_context(world: WorldStore, state: StateStore, card: Card) -> dict[str, Any] | None:
    if not card.parent_id:
        return None
    group = world.get_card(card.parent_id)
    return {
        "legion_id": group.id, "name": group.name,
        "role": card.config.get("legion_role", ""),
        "settings": dict(group.config),
        "shared_state": read_shared_state(world, state, group.id),
    }
