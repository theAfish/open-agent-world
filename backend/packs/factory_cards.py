"""Host-owned renderer contract for declarative card recipes in content Packs."""
import hashlib

from packaging.version import Version
from pydantic import Field, create_model

from backend.packs.associations import PassiveAssociation
from backend.packs.factory_models import CardRecipe, PrintedCard
from backend.packs.manifest import PackDependency
from backend.plugins.registry import NodePresentation, NodeTypeDefinition

FACTORY_RENDERER = "oaw.factory"


def exported_recipe_location(owner_id: str, type_id: str):
    key = hashlib.sha256(f"recipe:{type_id}".encode()).hexdigest()[:16]
    return f"{owner_id}.card{key}", f"content/item-{key}.json"


def local_recipes(type_ids, owner_id: str, registry) -> dict[str, CardRecipe]:
    """Embed local type contracts, including their local connection targets."""
    recipes = {}
    pending = list(type_ids)
    while pending:
        type_id = pending.pop()
        if type_id in recipes or not type_id.startswith("local.printed."):
            continue
        if len(owner_id) > 100:
            raise ValueError("Use a Pack ID of at most 100 characters when exporting printed card types")
        definition = registry.node_type(type_id)
        source_owner = registry.owner_id("node_type", type_id)
        source_manifest = registry.installed_packs.get(source_owner)
        if "ui.factory-card.v1" not in definition.traits or source_manifest is None or source_manifest.kind != "content":
            raise ValueError(f"Local printed type is not a passive recipe: {type_id}")
        if len(recipes) >= 50:
            raise ValueError("A content Pack supports at most 50 card recipes, including local connection targets")
        recipe_id, _ = exported_recipe_location(owner_id, type_id)
        recipe = CardRecipe(id=recipe_id, design=PrintedCard.model_validate(definition.config_model().model_dump(mode="json")))
        recipes[type_id] = recipe
        pending.extend(binding.target_type for binding in recipe.design.function.associations or [] if binding.target_type)
    for recipe in recipes.values():
        remap_recipe_targets(recipe.design, {type_id: item.id for type_id, item in recipes.items()})
    return recipes


def remap_recipe_targets(design: PrintedCard, type_ids: dict[str, str]):
    for binding in design.function.associations or []:
        if binding.target_type in type_ids:
            binding.target_type = type_ids[binding.target_type]


def embed_local_recipes(blueprint, owner_id: str, registry) -> dict[str, CardRecipe]:
    """Preserve a formation while replacing local type and relationship ownership."""
    recipes = local_recipes((node.type for node in blueprint.nodes), owner_id, registry)
    types = {type_id: recipe.id for type_id, recipe in recipes.items()}
    relationships = {}
    for type_id, recipe in recipes.items():
        source_owner = registry.owner_id("node_type", type_id)
        old_recipe = CardRecipe(id=type_id, design=PrintedCard.model_validate(registry.node_type(type_id).config_model().model_dump(mode="json")))
        relationships.update({old.id: new.id for old, new in zip(recipe_associations(old_recipe, source_owner), recipe_associations(recipe, owner_id))})
    for node in blueprint.nodes:
        if node.type in types:
            node.type, node.plugin_id = types[node.type], owner_id
            design = PrintedCard.model_validate(node.config)
            remap_recipe_targets(design, types)
            node.config = design.model_dump(mode="json")
        for dependency in node.dependencies:
            mapping = types if dependency.kind == "node_type" else relationships if dependency.kind == "relationship" else {}
            if dependency.id in mapping:
                dependency.id, dependency.plugin_id = mapping[dependency.id], owner_id
    for edge in blueprint.edges:
        if edge.relationship in relationships:
            edge.relationship, edge.plugin_id = relationships[edge.relationship], owner_id
    return {exported_recipe_location(owner_id, type_id)[1]: recipe for type_id, recipe in recipes.items()}


def recipe_associations(recipe: CardRecipe, owner_id: str):
    """Mint bounded, owner-scoped IDs without broadening a recipe's source selector."""
    for binding in recipe.design.function.associations or []:
        key = hashlib.sha256(f"{recipe.id}:{binding.id}".encode()).hexdigest()[:16]
        yield PassiveAssociation(id=f"{owner_id}.link{key}", label=binding.label,
            source_types=(recipe.id,), target_types=(binding.target_type,) if binding.target_type else (),
            target_traits=(binding.target_trait,) if binding.target_trait else ())


def recipe_dependencies(recipes: list[CardRecipe], registry) -> tuple[PackDependency, ...]:
    """Resolve renderer and connection targets through the ordinary Pack catalog."""
    if not recipes:
        return ()
    catalog = registry.catalog()
    own_types = {recipe.id for recipe in recipes}
    nodes = {node.id: node for node in catalog.node_types}
    renderer_owner = registry.owner_id("pack", FACTORY_RENDERER)
    owners = {renderer_owner}
    for recipe in recipes:
        for binding in recipe.design.function.associations or []:
            if binding.target_type:
                if binding.target_type in own_types:
                    continue
                node = nodes.get(binding.target_type)
                if node is None:
                    raise ValueError(f"Connection target card type is unavailable: {binding.target_type}")
                owners.add(node.plugin_id)
            else:
                providers = sorted({node.plugin_id for node in catalog.node_types if binding.target_trait in node.traits})
                if not providers:
                    raise ValueError(f"Connection target trait is unavailable: {binding.target_trait}")
                # One declared provider establishes a usable target; the trait remains an open contract.
                owners.add(next((provider for provider in providers if provider in owners), providers[0]))
    descriptors = {plugin.id: plugin for plugin in catalog.plugins}
    dependencies = []
    for owner in sorted(owners):
        packs = sorted(pack.id for pack in catalog.packs if pack.plugin_id == owner)
        if not packs or owner not in descriptors:
            raise ValueError(f"Connection dependency has no enabled distributable Pack: {owner}")
        pack_id = FACTORY_RENDERER if owner == renderer_owner else packs[0]
        dependencies.append(PackDependency(id=pack_id, version="==" + descriptors[owner].version))
    return tuple(dependencies)


def validate_recipe_targets(recipes: list[CardRecipe], registry, owners: set[str]):
    """A target must be provided by this content Pack or a declared enabled dependency."""
    own_types = {recipe.id for recipe in recipes}
    nodes = {node.id: node for node in registry.catalog().node_types if node.plugin_id in owners}
    for recipe in recipes:
        for binding in recipe.design.function.associations or []:
            if binding.target_type and binding.target_type not in own_types | nodes.keys():
                raise ValueError(f"Unavailable or undeclared connection target: {binding.target_type}")
            if binding.target_trait and not any(binding.target_trait in node.traits for node in nodes.values()):
                raise ValueError(f"Unavailable or undeclared connection trait: {binding.target_trait}")


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
        presentation=presentation, tutorials=recipe.tutorials)


def read_recipes(manifest, files):
    from backend.packs.archive import json_object
    recipes = []
    for path in manifest.content.cards:
        if path not in files or len(files[path]) > 8 * 1024 * 1024:
            raise ValueError("Missing or oversized card recipe")
        recipe = CardRecipe.model_validate(json_object(files[path]))
        if recipe.design.function.associations and Version(manifest.compatibility.plugin_api) < Version("1.39"):
            raise ValueError("Recipe connections require Plugin API 1.39 or newer")
        if not recipe.id.startswith(manifest.id + "."):
            raise ValueError("Content card IDs must use their Pack namespace")
        recipes.append(recipe)
    if len({recipe.id for recipe in recipes}) != len(recipes):
        raise ValueError("Content card IDs must be unique")
    return recipes
