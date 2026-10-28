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
        owner = card.missing_plugin.plugin_id if card.missing_plugin else services.plugins.node_type_owner_id(card.type)
        if owner == plugin_id:
            usages.append(f"card {card.name}")
        elif not card.missing_plugin and services.plugins.has_trait(card.type, "core.agent"):
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
    for job in services.background_jobs.records():
        if job.get("tracking_released") or (job["status"] not in {"submitting", "queued", "running", "unknown"} and not job["wake_requested"]):
            continue
        try:
            owner = services.plugins.owner_id("background_job_provider", job["provider"])
        except ValueError:
            continue
        if owner == plugin_id:
            usages.append("tracked background work")
            break
    return usages


@router.post("/actions")
async def edit_library(request: LibraryEdit, services: ApplicationServices = Depends(get_services)):
    # Collection edits and re-enabling a provider must remain possible during
    # unrelated world cleanup. Only disabling changes runtime availability.
    disabling = request.action == "set_plugin_enabled" and request.enabled is False
    async with services._node_mutation(read_only=not disabling):
        if request.action == "dismiss_pack":
            pack = services.card_library.read().packs.get(request.id or "")
            if pack and (any(manifest.owner_id == pack.definition.plugin_id
                             for manifest in services.pack_installations.selected().values())
                         or any(spec.owner == pack.definition.plugin_id and services.pack_installations.source_enrolled(key)
                                for key, spec in services.pack_installations.source_specs().items())):
                raise ConflictError("Uninstall this Pack before removing its inventory entry")
        if (request.action == "set_plugin_enabled" and request.enabled
                and (any(manifest.owner_id == request.id and manifest.id not in services.pack_installations.selected()
                         for manifest in services.plugins.installed_packs.values())
                     or any(spec.owner == request.id and not services.pack_installations.source_enrolled(key)
                            for key, spec in services.pack_installations.source_specs().items()))):
            raise ConflictError("This Pack is scheduled for uninstall. Select an installed version and restart OAW before enabling it.")
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
        collected = services.card_library.assert_collected(request.type)
        return await services.create_card(request.model_copy(update={"finish": collected.finish}))
