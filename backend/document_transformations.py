"""Atomic host-owned transformations of inert document collections."""
from pydantic import BaseModel, ConfigDict, Field
from copy import deepcopy
from backend.errors import ResourceValidationError, RevisionConflictError
from backend.node_documents import definition, read_document, write_document, validation_message
from backend.events.models import RuntimeEvent, EventType


class TransformationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str | None = None
    source_type: str | None = None
    source_revision: int = Field(default=0, ge=0)
    expected_revision: int = Field(ge=0)
    confirm: bool = False


async def transform_document(services, node_id, operation, request):
    async with services._node_mutation():
        if (request.source_id is None) == (request.source_type is None):
            raise ResourceValidationError("Choose one source card or palette type")
        if node_id == request.source_id:
            raise ResourceValidationError("Choose a different source")
        spec = definition(services, node_id)
        conversion = spec.transformations.get(operation)
        if conversion is None:
            raise ResourceValidationError("Unknown resource transformation")
        source = services.world.get_card(request.source_id) if request.source_id else None
        source_type = source.type if source else request.source_type
        source_spec = services.plugins.node_type(source_type)
        if not conversion.source_traits <= source_spec.traits:
            raise ResourceValidationError("This source is not supported")
        if source is None and (not source_spec.user_creatable or source_spec.document is None or source_spec.document.initial_value is None or source_spec.lifecycle is not None or source_spec.execution is not None):
            raise ResourceValidationError("Only creatable inert document packages support palette transformation")
        consumed = [source, *services.world.owned_descendants(source.id)] if source else []
        for card in consumed:
            card_spec = services.plugins.node_type(card.type)
            if card_spec.document is None or card_spec.lifecycle is not None or card_spec.execution is not None or services.world.equipment_for(card.id):
                raise ResourceValidationError("Only inert document collections support this transformation")
            services.node_execution.assert_editable(card.id)
            services.resources.artifacts.assert_source_idle(card.id)
        if node_id in {card.id for card in consumed}:
            raise ResourceValidationError("A source cannot contain the destination")
        services.node_execution.assert_editable(node_id)
        original = read_document(services, source.id) if source else {"value": deepcopy(dict(source_spec.document.initial_value)), "revision": 0}
        target = read_document(services, node_id)
        if original["revision"] != request.source_revision or target["revision"] != request.expected_revision:
            raise RevisionConflictError("Source or destination changed; preview again")
        provenance = {"node_id": source.id if source else None, "node_type": source_type, "plugin_id": services.plugins.node_type_owner_id(source_type),
                      "document_revision": original["revision"]}
        try:
            value = conversion.handler(target["value"], original["value"], provenance)
            value = spec.model.model_validate(value).model_dump(mode="json")
        except ValueError as error:
            raise ResourceValidationError(validation_message(error)) from error
        preview = {"label": conversion.label, "source_name": source.name if source else source_spec.default_name,
                   "source_revision": original["revision"], "revision": target["revision"],
                   "summary": spec.summarize(value), "consumed_ids": [card.id for card in consumed]}
        if not request.confirm:
            return preview
        edges = {edge.id: edge for card in consumed for edge in
                 [*services.world.list_edges_from(card.id), *services.world.list_edges_to(card.id)]}
        affected = {key: services._affected_agents(edge) for key, edge in edges.items()}
        with services.events.committed_batch(), services.world.database.transaction(immediate=True):
            write_document(services, node_id, value, target["revision"])
            if consumed:
                services.world.delete_cards([card.id for card in consumed])
            from backend.node_containers import touch_parent
            if source:
                touch_parent(services, source.parent_id)
        for edge in edges.values():
            services._publish_edge_change_nowait(EventType.EDGE_DELETED, edge, affected_agents=affected[edge.id])
        for card in consumed:
            services.events.publish_event_nowait(RuntimeEvent(type=EventType.CARD_DELETED, node_id=card.id,
                payload={"node": card.model_dump(mode="json")}))
        return {**preview, "committed": True}
