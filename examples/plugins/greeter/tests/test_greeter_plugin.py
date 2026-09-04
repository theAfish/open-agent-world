from __future__ import annotations

from pathlib import Path
from importlib.metadata import entry_points

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins import ENTRY_POINT_GROUP, load_plugin_registry
from backend.services import create_services
from backend.world.models import CardCreate, CardPatch, EdgeCreate


@pytest.mark.asyncio
async def test_greeter_enters_the_catalog_graph_and_agent_tool_flow(
    tmp_path: Path,
) -> None:
    discovered = {
        item.name: item.value
        for item in entry_points().select(group=ENTRY_POINT_GROUP)
    }
    assert discovered["community-greeter"] == "oaw_greeter_plugin:create_plugin"

    registry = load_plugin_registry()
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    second_registry = load_plugin_registry()
    second_services = create_services(
        Settings.for_data_root(tmp_path / "managed-second"),
        plugins=second_registry,
    )
    try:
        catalog = registry.catalog()
        assert {
            plugin.id for plugin in catalog.plugins
        } >= {"open-agent-world.core", "community.greeter"}
        assert "community.greeter" in {item.id for item in catalog.node_types}
        assert "community.greet" in {item.id for item in catalog.relationships}
        assert registry.node_type_owner_id("community.greeter") == "community.greeter"
        assert registry.relationship_owner_id("community.greet") == "community.greeter"

        agent = await services.create_card(CardCreate(id="agent-1", type="agent"))
        greeter = await services.create_card(CardCreate(
            id="greeter-1",
            type="community.greeter",
            config={"greeting": "Welcome", "punctuation": "!"},
        ))

        # A reverse drag gesture is accepted and stored in canonical Agent -> Greeter order.
        edge = await services.create_edge(EdgeCreate(
            source=greeter.id,
            target=agent.id,
            relationship="community.greet",
        ))
        assert (edge.source, edge.target) == (agent.id, greeter.id)

        capability = services.capabilities.derive(agent.id).capabilities[0]
        assert capability.kind == "community.greeter.greet"
        provider = WorldAgentCapabilityProvider(services)
        assert await provider.invoke_tool(
            agent.id, capability.id, {"name": "Ada"}
        ) == {"text": "Welcome, Ada!", "greeter_id": greeter.id}

        second_agent = await second_services.create_card(
            CardCreate(id="agent-1", type="agent")
        )
        second_greeter = await second_services.create_card(CardCreate(
            id="greeter-1",
            type="community.greeter",
            config={"greeting": "Bonjour", "punctuation": "."},
        ))
        await second_services.create_edge(EdgeCreate(
            source=second_agent.id,
            target=second_greeter.id,
            relationship="community.greet",
        ))
        second_provider = WorldAgentCapabilityProvider(second_services)
        second_capability = second_services.capabilities.derive(
            second_agent.id
        ).capabilities[0]
        assert await second_provider.invoke_tool(
            second_agent.id, second_capability.id, {"name": "Ada"}
        ) == {"text": "Bonjour, Ada.", "greeter_id": second_greeter.id}
        with pytest.raises(ResourceValidationError, match="non-empty string name"):
            await provider.invoke_tool(agent.id, capability.id, {"name": ""})

        await services.update_card(
            greeter.id,
            CardPatch(config={"greeting": "hello", "uppercase": True}),
        )
        assert await provider.invoke_tool(
            agent.id, capability.id, {"name": "Grace"}
        ) == {"text": "HELLO, GRACE!", "greeter_id": greeter.id}

        await services.delete_card(greeter.id)
        assert services.capabilities.derive(agent.id).capabilities == []
        with pytest.raises(PermissionDeniedError, match="not currently available"):
            await provider.invoke_tool(agent.id, capability.id, {"name": "Ada"})
        assert await second_provider.invoke_tool(
            second_agent.id, second_capability.id, {"name": "Grace"}
        ) == {"text": "Bonjour, Grace.", "greeter_id": second_greeter.id}
    finally:
        services.close()
        second_services.close()
