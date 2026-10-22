"""Keep locally printed designs reusable through the ordinary content installer."""
from __future__ import annotations

import hashlib

from backend import __version__
from backend.card_library import LibraryEdit
from backend.events.models import EventType, RuntimeEvent
from backend.packs.content import _json, export_archive
from backend.packs.factory_models import CardRecipe, PrintedCard
from backend.packs.manifest import PackManifest
from backend.sandbox.python_runtime import finish_thread


async def collect_printed_type(services, design: PrintedCard) -> str:
    """Called under the graph mutation barrier; identical designs reuse a type.

    Each immutable design is a normal local content Pack. Updates never replace a
    loaded owner or change the definition of an existing canvas card. The same
    installer, activation event, Library and restart loader serve local and shared
    recipes; there is no second registry or ad-hoc persistence format.
    """
    from backend.packs.factory_cards import recipe_dependencies

    dependencies = recipe_dependencies([CardRecipe(id="local.printed.pending", design=design)], services.plugins)
    compatibility = {"oaw": f">={__version__},<1", "plugin_api": "1.39", "frontend_api": 1}
    identity = _json({"design": design.model_dump(mode="json"), "compatibility": compatibility,
                      "dependencies": [item.model_dump(mode="json") for item in dependencies]})
    owner = "local.printed.c" + hashlib.sha256(identity).hexdigest()[:32]
    recipe = CardRecipe(id=owner + ".card", design=design)
    if not services.plugins.has_plugin(owner):
        path = "content/card.json"
        manifest = PackManifest(schema_version=2, kind="content", id=owner,
            name=design.face.title, version="0.1.0", compatibility=compatibility,
            content={"cards": [path]}, creator={}, dependencies={"packs": dependencies})
        files = {path: _json(recipe.model_dump(mode="json")),
                 "manifest.json": _json(manifest.model_dump(mode="json", exclude_none=True))}
        selected = services.pack_installations.selected().get(owner)
        if selected is None:
            data = await finish_thread(export_archive, files)
            await finish_thread(services.pack_installations.install, data)
        else:
            # Installation can succeed before live activation is interrupted.
            # Retry that activation without reinstalling an immutable version.
            root = services.pack_installations.version_path(selected.id, selected.version)
            if selected != manifest or (root / path).read_bytes() != files[path]:
                raise ValueError("The local printed Pack identity belongs to a different design")
        await services.activate_passive_packs()
    # This also rejects an existing, explicitly disabled local Pack rather than
    # silently overriding the user's Library preference.
    services.plugins.node_type(recipe.id)
    library = services.card_library.read()
    if recipe.id not in library.collection or not library.collection[recipe.id].unlocked:
        library = services.card_library.edit(LibraryEdit(action="open_pack", id=owner,
                                                        expected_revision=library.revision))
        await services.events.publish_event(RuntimeEvent(type=EventType.CARD_LIBRARY_UPDATED,
                                                         payload={"revision": library.revision}))
    return recipe.id
