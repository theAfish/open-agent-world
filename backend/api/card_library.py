from fastapi import APIRouter, Depends

from backend.api.dependencies import get_services
from backend.card_library import LibraryEdit
from backend.errors import ConflictError
from backend.events.models import EventType, RuntimeEvent
from backend.services import ApplicationServices
from backend.world.models import CardCreate

router = APIRouter(prefix="/card-library", tags=["card library"])


@router.get("")
async def read_library(services: ApplicationServices = Depends(get_services)):
    return services.card_library.snapshot()


def plugin_usage(services: ApplicationServices, plugin_id: str) -> list[str]:
    """Keep the existing lifecycle intact: no hot unload of objects or providers."""
    usages = []
    for card in services.world.list_cards():
        if services.plugins.node_type_owner_id(card.type) == plugin_id:
            usages.append(f"card {card.name}")
        elif services.plugins.has_trait(card.type, "core.agent"):
            provider = services.run_manager.provider_id_for_card(card)
            if provider:
                try:
                    if services.plugins.runtime_provider_owner_id(provider) == plugin_id:
                        usages.append(f"Agent runtime for {card.name}")
                except ValueError:
                    pass
    with services.database.locked() as db:
        if db.execute("SELECT 1 FROM edges WHERE plugin_id=? LIMIT 1", (plugin_id,)).fetchone():
            usages.append("world relationships")
        if db.execute("SELECT 1 FROM pending_node_deletions WHERE plugin_id=? LIMIT 1", (plugin_id,)).fetchone():
            usages.append("pending lifecycle cleanup")
    return usages


@router.post("/actions")
async def edit_library(request: LibraryEdit, services: ApplicationServices = Depends(get_services)):
    # Collection edits and re-enabling a provider must remain possible during
    # unrelated world cleanup. Only disabling changes runtime availability.
    disabling = request.action == "set_plugin_enabled" and request.enabled is False
    async with services._node_mutation(read_only=not disabling):
        if request.action == "set_plugin_enabled" and request.enabled is False:
            usages = plugin_usage(services, request.id or "")
            if usages:
                raise ConflictError("Plugin is in use by " + ", ".join(usages[:6]) + ". Remove those world instances and links before disabling it.")
        services.card_library.edit(request)
        if request.action == "set_plugin_enabled" and request.enabled and services.plugin_bootstrap:
            services.plugin_bootstrap.enqueue()
        snapshot = services.card_library.snapshot()
    await services.events.publish_event(RuntimeEvent(type=EventType.CARD_LIBRARY_UPDATED, payload={"revision": snapshot["revision"]}))
    return snapshot


@router.post("/nodes", status_code=201)
async def place_collected_card(request: CardCreate, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation():
        services.card_library.assert_collected(request.type)
        return await services.create_card(request)
