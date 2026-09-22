from importlib.metadata import entry_points
from pathlib import Path

import pytest

from backend.config import Settings
from backend.plugins import ENTRY_POINT_GROUP, load_plugin_registry
from backend.services import create_services
from open_agent_world.plugin_api import CardCreate, CardPatch


@pytest.mark.asyncio
async def test_tutorial_package_is_discovered_and_configuration_survives_reload(tmp_path: Path):
    declarations = entry_points().select(group=ENTRY_POINT_GROUP)
    assert any(item.name == "community-hello" and item.value == "oaw_hello:create_plugin" for item in declarations)
    registry = load_plugin_registry()
    assert registry.node_type_owner_id("community.hello.message") == "community.hello"
    assert any(pack.id == "community.hello.starter" for pack in registry.catalog().packs)
    settings = Settings.for_data_root(tmp_path / "world")
    services = create_services(settings, plugins=registry)
    try:
        card = await services.create_card(CardCreate(type="community.hello.message"))
        assert card.config["message"] == "Hello, world!"
        await services.update_card(card.id, CardPatch(config={"message": "My first plugin works"}))
    finally:
        services.close()
    reloaded = create_services(settings, plugins=load_plugin_registry())
    try:
        assert reloaded.get_card(card.id).config["message"] == "My first plugin works"
        await reloaded.delete_card(card.id)
    finally:
        reloaded.close()
