"""Host-owned renderer contract for declarative card recipes in content Packs."""
from pydantic import Field, create_model

from backend.packs.factory_models import CardRecipe, PrintedCard
from backend.plugins.registry import NodePresentation, NodeTypeDefinition


def recipe_definition(recipe: CardRecipe):
    design = recipe.design
    studio = design.face.studio
    presentation = NodePresentation(states=tuple(studio.enabled), initial=studio.initial, open=studio.open) if studio else NodePresentation(states=("node", "preview", "inspector", "workspace"), initial="preview", open="workspace")
    size = studio.modes[studio.initial] if studio else None
    model = create_model("RecipeConfig", __base__=PrintedCard,
        face=(type(design.face), Field(default_factory=lambda: design.face.model_copy(deep=True))),
        function=(type(design.function), Field(default_factory=lambda: design.function.model_copy(deep=True))))
    return NodeTypeDefinition(id=recipe.id, label=design.face.title, description=design.face.description,
        icon=design.face.icon, color=design.face.color, deck_id="factory", deck_label="自定义卡牌", deck_icon="layers",
        default_name=design.face.title, default_size=(size.width, size.height) if size else (340, 300), default_status="idle", statuses=frozenset({"idle"}),
        config_model=model, traits=frozenset({"ui.factory-card.v1"}),
        card_face={"variant": design.face.variant, "tone": design.face.tone}, templateable=True,
        frontend={slot: "factory-card" for slot in ("preview", "body", "settings", "workspace")},
        presentation=presentation)


def read_recipes(manifest, files):
    from backend.packs.archive import json_object
    recipes = []
    for path in manifest.content.cards:
        if path not in files or len(files[path]) > 8 * 1024 * 1024:
            raise ValueError("Missing or oversized card recipe")
        recipe = CardRecipe.model_validate(json_object(files[path]))
        if not recipe.id.startswith(manifest.id + "."):
            raise ValueError("Content card IDs must use their Pack namespace")
        recipes.append(recipe)
    if len({recipe.id for recipe in recipes}) != len(recipes):
        raise ValueError("Content card IDs must be unique")
    return recipes
