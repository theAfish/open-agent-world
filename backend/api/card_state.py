"""Desktop state access. Agent access stays inside granted plugin handlers."""
from typing import Any, Literal
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services
from backend.errors import PermissionDeniedError, ResourceValidationError, ConflictError

router = APIRouter(prefix="/nodes", tags=["card-state"])


class StateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)


def bound(services, node_id):
    state = services.card_state.bind(node_id)
    if state is None:
        raise PermissionDeniedError("This plugin has no persistent state capability")
    return state


@router.get("/{node_id}/state")
async def get_state(node_id: str, services=Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return bound(services, node_id).get()


@router.put("/{node_id}/state")
async def set_state(node_id: str, request: StateWrite, services=Depends(get_services)):
    async with services._node_mutation():
        return bound(services, node_id).set(request.value, request.expected_revision)


@router.patch("/{node_id}/state")
async def update_state(node_id: str, request: StateWrite, services=Depends(get_services)):
    async with services._node_mutation():
        return bound(services, node_id).update(request.value, request.expected_revision)


@router.delete("/{node_id}/state")
async def delete_state(node_id: str, request: StateWrite, services=Depends(get_services)):
    async with services._node_mutation():
        return bound(services, node_id).delete(request.expected_revision)


class NamespaceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: Literal["shared", "session"]
    scope_id: str = Field(min_length=1, max_length=100)
    values: dict[Literal["document", "data"], dict[str, Any]]


class CardStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespaces: list[NamespaceSnapshot] = Field(default_factory=list, max_length=1000)


@router.get("/{node_id}/state-snapshot")
async def snapshot_state(node_id: str, services=Depends(get_services)):
    async with services._node_mutation(read_only=True):
        card = services.world.get_card(node_id)
        spec = services.plugins.node_type(card.type)
        if spec.state is None or spec.state.mode == "none":
            raise PermissionDeniedError("This card does not provide scoped state snapshots")
        with services.database.locked() as db:
            legacy = db.execute("SELECT 1 FROM state_scopes WHERE scope_kind='node_document' AND owner_id=?", (node_id,)).fetchone()
        if legacy:
            services.card_state.scope(node_id)
        namespaces = []
        for kind, namespace in services.card_state.existing(node_id):
            scope = services.card_state.scope(node_id, (kind, namespace))
            with services.database.locked() as db:
                values = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
                    "SELECT key,value_json FROM state_values WHERE scope_id=? AND deleted=0 AND key IN ('document','data')", (scope.scope_id,))}
            if spec.container and spec.container.document_field and "document" in values:
                values["document"][spec.container.document_field] = []
            namespaces.append({"scope_type": kind, "scope_id": namespace, "values": values})
        return {"namespaces": namespaces}


@router.post("/{node_id}/state-snapshot")
async def restore_state(node_id: str, request: CardStateSnapshot, services=Depends(get_services)):
    async with services._node_mutation():
        card = services.world.get_card(node_id)
        spec = services.plugins.node_type(card.type)
        if spec.state is None or spec.state.mode == "none":
            raise PermissionDeniedError("This card does not provide scoped state snapshots")
        with services.database.transaction(immediate=True) as db:
            if db.execute("SELECT 1 FROM card_state_instances c JOIN state_values v ON v.scope_id=c.state_scope_id WHERE c.card_id=? AND v.revision>0", (node_id,)).fetchone():
                raise ConflictError("State snapshots can only restore an empty card")
            for entry in request.namespaces:
                if entry.scope_type not in spec.state.supported_scopes or (entry.scope_type == "shared" and entry.scope_id != "*"):
                    raise ResourceValidationError("Snapshot has an unsupported namespace")
                if entry.scope_type == "session" and entry.scope_id != "default" and not db.execute("SELECT 1 FROM conversation_sessions WHERE id=?", (entry.scope_id,)).fetchone():
                    raise ResourceValidationError("Snapshot's conversation session no longer exists")
                scope = services.card_state.scope(node_id, (entry.scope_type, entry.scope_id))
                for key, value in entry.values.items():
                    limit = 256 * 1024
                    if key == "document":
                        if spec.document is None:
                            raise ResourceValidationError("This card no longer provides a document")
                        value = spec.document.model.model_validate(value).model_dump(mode="json")
                        limit = spec.document.max_size_bytes
                    if len(json.dumps(value, allow_nan=False).encode()) > limit:
                        raise ResourceValidationError("State snapshot exceeds the card's size limit")
                    services.state.set(scope, key, value, expected_revision=0)
        return {"restored": True}
