from dataclasses import replace

import pytest

from backend.application import PreferenceEdit
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.registry import PackDefinition, PluginDefinition, PluginDescriptor, PluginRegistry
from backend.plugins.tutorials import TutorialDefinition, TutorialStep, validate_tutorials


def tutorial(**overrides):
    return TutorialDefinition(**{
        "id": "intro", "title": {"en": "Hello", "zh-CN": "你好"}, "summary": "Learn this card",
        "steps": [{"id": "open", "title": "Open", "body": "Open the card."}], **overrides,
    })


def test_public_contract_and_catalog_round_trip():
    from open_agent_world.plugin_api import TutorialDefinition as PublicTutorial, TutorialStep as PublicStep
    assert (PublicTutorial, PublicStep) == (TutorialDefinition, TutorialStep)
    registry = create_builtin_registry()
    text = next(item for item in registry.catalog().node_types if item.id == "text")
    assert text.tutorials[0].steps
    source = registry.node_type("text")
    custom = replace(source, id="example.card", tutorials=(tutorial(),))

    def register(registration):
        registration.register_node_type(custom)
        registration.register_pack(PackDefinition(id="example.pack", name="Example", cards=(custom.id,),
            tutorials=(tutorial(steps=(), document="## Reference"),)))

    target = PluginRegistry()
    target.install(PluginDefinition(PluginDescriptor(id="example", version="1", plugin_api_version="1.26"), register))
    payload = target.catalog().model_dump(mode="json")
    assert payload["node_types"][0]["tutorials"][0]["title"]["zh-CN"] == "你好"
    assert payload["packs"][0]["tutorials"][0]["document"] == "## Reference"


@pytest.mark.parametrize("overrides", [
    {"revision": 0}, {"revision": True}, {"title": {"zh-CN": "你好"}},
    {"steps": ()}, {"trigger": "execute"},
    {"steps": [{"id": "same", "title": "A", "body": "B"}] * 2},
])
def test_invalid_content_is_rejected(overrides):
    with pytest.raises(ValueError):
        tutorial(**overrides)


@pytest.mark.parametrize("items", [
    [tutorial(), tutorial()],
    [tutorial(after=("missing",))],
    [tutorial(after=("intro",))],
    [tutorial(after=("advanced",)), tutorial(id="advanced", after=("intro",))],
])
def test_invalid_dependencies_are_rejected_on_packs_and_cards(items):
    with pytest.raises(ValueError):
        PackDefinition(id="example.pack", name="Example", tutorials=items)
    registry = create_builtin_registry()
    node = replace(registry.node_type("text"), id="example.card", tutorials=items)

    def register(registration):
        registration.register_node_type(node)
        registration.register_pack(PackDefinition(id="example.pack", name="Example", cards=(node.id,)))

    with pytest.raises(ValueError):
        registry.install(PluginDefinition(PluginDescriptor(id="example", version="1", plugin_api_version="1.26"), register))
    assert not registry.has_plugin("example")


def test_valid_sequence_and_profile_preference():
    assert len(validate_tutorials([tutorial(), tutorial(id="advanced", after=("intro",))])) == 2
    edit = PreferenceEdit(profile_id="profile", generation="generation", changes={"oaw-progressive-tutorials-v1": '{"version":1,"state":{"enabled":false}}'})
    assert "oaw-progressive-tutorials-v1" in edit.changes


def test_content_pack_and_recipe_tutorials_survive_distribution(tmp_path):
    import json
    from backend.legions.presets import preset_record
    from backend.packs.content import CreatorRequest, prepare_export, export_archive
    from backend.packs.factory_models import CardRecipe, PrintedCard
    from backend.packs.installation import PackInstallationManager
    from backend.plugins.loader import load_installed_packs

    registry = create_builtin_registry()
    request = CreatorRequest(legion_id="coding", id="local.guides", name="Guides",
        creator={"tutorials": [tutorial().model_dump(mode="json")]})
    review, files = prepare_export(preset_record("coding", registry), request, registry)
    assert review["can_export"]
    manifest = json.loads(files["manifest.json"])
    manifest["content"]["cards"] = ["content/card.json"]
    files["manifest.json"] = json.dumps(manifest).encode()
    recipe = CardRecipe(id="local.guides.card", design=PrintedCard(), tutorials=(tutorial(),))
    files["content/card.json"] = recipe.model_dump_json().encode()
    PackInstallationManager(tmp_path, registry).install(export_archive(files))
    fresh = create_builtin_registry()
    load_installed_packs(fresh, tmp_path)
    catalog = fresh.catalog()
    assert next(p for p in catalog.packs if p.id == "local.guides").tutorials[0].id == "intro"
    assert next(n for n in catalog.node_types if n.id == "local.guides.card").tutorials[0].steps[0].id == "open"
