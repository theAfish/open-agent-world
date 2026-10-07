"""Host-owned definition, presentation, and isolated simulation APIs."""
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from backend.api.dependencies import get_services
from backend.capabilities.events import discover_event_sources, CALL_EVENTS, WORK_EVENTS, ACTION_EVENTS, RUN_EVENTS, STATE_EVENTS, AGENT_EVENTS
from backend.services import ApplicationServices

from backend.state_machine_preview import EVENT_CATALOG, PreviewRequest, replay_preview
from backend.state_machine import StateMachineConfig, StateMachineModel, StateMachinePresentation
from backend.errors import NotFoundError
from backend.events.models import EventType


router = APIRouter(prefix="/state-machines", tags=["state-machines"])


@router.get("/events")
async def event_catalog(agent_id: str | None = None, card_id: str | None = None, services: ApplicationServices = Depends(get_services)):
    # Use the same live projection as Agent tools, including prerequisite grants
    # and secondary selectors. A visually adjacent card is not an authority grant.
    operations, sources = [], []
    if (source_id := card_id or agent_id) is not None:
        async with services._node_mutation(read_only=True):
            operations, sources = discover_event_sources(services, source_id)
    events = [event for event in EVENT_CATALOG if event["key"] == "custom"]
    events.extend(event.descriptor() for event in (*CALL_EVENTS.values(), *WORK_EVENTS.values(),
                                                   *ACTION_EVENTS.values(), *RUN_EVENTS.values(), *STATE_EVENTS.values(), *AGENT_EVENTS.values()))
    return {"events": events, "operations": operations, "sources": sources}


@router.post("/preview")
async def preview(request: PreviewRequest, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        # When a replay names real system anchors, use their live declaration.
        # The pure evaluator also accepts portable plugin declarations in tests.
        for group in request.machine.entities:
            if group.ownership != "system" or group.card_id is None:
                continue
            declared = services.state_machines._default_document(services.world.get_card(group.card_id))
            canonical = next((item for item in (declared or {}).get("definition", {}).get("entities", []) if item["id"] == group.id), None)
            if canonical is None:
                from backend.errors import ResourceValidationError
                raise ResourceValidationError("Simulation system anchor is not provided by its owning subsystem")
            from backend.state_machine import MachineEntity
            expected = MachineEntity.model_validate(canonical)
            if group.model_dump(exclude={"states"}) != expected.model_dump(exclude={"states"}) or [(state.id, state.label) for state in group.states] != [(state.id, state.label) for state in expected.states]:
                from backend.errors import ResourceValidationError
                raise ResourceValidationError("Simulation cannot override system-owned states or canonical transitions")
        if request.machine.references:
            resolved = services.state_machines.resolve_preview_definition(request.machine)
            try:
                request = PreviewRequest.model_validate({**request.model_dump(), "machine": resolved})
            except ValueError as exc:
                from backend.errors import ResourceValidationError
                raise ResourceValidationError(str(exc)) from exc
    return replay_preview(request)


class DefinitionSave(StateMachineModel):
    definition: StateMachineConfig
    presentation: StateMachinePresentation = Field(default_factory=StateMachinePresentation)
    expected_revision: int | None = Field(default=None, ge=0)


class InstanceActivation(StateMachineModel):
    definition_version: int = Field(ge=1)
    scope_key: str = Field(default="default", min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)


@router.get("/{card_id}")
async def read_definition(card_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return services.state_machines.get(card_id)


@router.put("/{card_id}")
async def save_definition(card_id: str, request: DefinitionSave, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation():
        services.world._require_structure_edit()
        saved = services.state_machines.save(card_id, request.definition, request.presentation.model_dump(mode="json", exclude_none=True), request.expected_revision)
        card = services.enrich_card(services.world.get_card(card_id))
        await services.events.publish(EventType.CARD_UPDATED, node_id=card_id, payload={"node": card.model_dump(mode="json")})
        return saved


@router.get("/{card_id}/members")
async def list_members(card_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return {"members": services.state_machines.members(card_id)}


@router.post("/{card_id}/instances")
async def activate_instance(card_id: str, request: InstanceActivation, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation():
        services.world._require_structure_edit()
        instance = services.state_machines.activate(card_id, request.definition_version, request.scope_key, request.context)
    runtime = getattr(services, "state_machine_runtime", None)
    if runtime is not None:
        runtime.wake()
    return instance


@router.get("/{card_id}/runtime")
async def runtime_diagnostics(card_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        services.world.get_card(card_id)
        return services.state_machine_runtime.diagnostics(card_id)


class InstanceDisable(StateMachineModel):
    enabled: Literal[False]


@router.patch("/{card_id}/instances/{instance_id}")
async def disable_instance(card_id: str, instance_id: str, request: InstanceDisable,
                           services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation():
        services.world._require_structure_edit()
        instance = services.state_machines.get_instance(instance_id)
        if instance["card_id"] != card_id:
            raise NotFoundError("State-machine instance does not belong to this object")
        return services.state_machines.put_runtime(instance_id, enabled=False)
