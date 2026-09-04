from __future__ import annotations

from pathlib import Path

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins import create_builtin_registry
from backend.services import create_services
from backend.world.models import CardCreate, CardPatch, EdgeCreate
from oaw_greeter_plugin import register


@pytest.mark.asyncio
async def test_greeter_enters_the_catalog_graph_and_agent_tool_flow(
    tmp_path: Path,
) -> None:
    registry = create_builtin_registry()
    register(registry)
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        catalog = registry.catalog()
        assert "community.greeter" in {item.id for item in catalog.node_types}
        assert "community.greet" in {item.id for item in catalog.relationships}

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
    finally:
        services.close()
