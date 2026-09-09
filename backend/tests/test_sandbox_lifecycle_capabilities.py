import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.services import create_services
from backend.tests.test_sandbox_manager import make_manager
from backend.world.models import CardCreate, EdgeCreate, EdgePatch


@pytest.mark.asyncio
async def test_lifecycle_grant_and_live_revocation(tmp_path):
    manager, backend = make_manager(tmp_path)
    services = create_services(Settings.for_data_root(manager.root), sandbox_backend=manager)
    try:
        agent = await services.create_card(CardCreate(type="agent"))
        sandbox = await services.create_card(CardCreate(type="sandbox"))
        edge = await services.create_edge(EdgeCreate(source=agent.id, target=sandbox.id, relationship="execute"))
        provider = WorldAgentCapabilityProvider(services)
        for action in ("start", "stop"):
            with pytest.raises(PermissionDeniedError):
                await provider.invoke_tool(agent.id, f"sandbox.{action}:{sandbox.id}", {})
        await services.update_edge(edge.id, EdgePatch(relationship="execute_manage"))
        kinds = {c.kind for c in services.capabilities.derive(agent.id).capabilities}
        assert {"sandbox.execute", "sandbox.inspect", "sandbox.start", "sandbox.stop"} <= kinds
        definitions = {tool.name: tool for tool in await provider.list_tools(agent.id)}
        assert {"start_sandbox", "stop_sandbox", "execute_command"} <= definitions.keys()
        started = await provider.invoke_tool(agent.id, definitions["start_sandbox"].capability_id, {"sandbox": sandbox.id})
        assert started["state"] == "ready"
        assert services.world.get_card(sandbox.id).status == "ready"
        stopped = await provider.invoke_tool(agent.id, f"sandbox.stop:{sandbox.id}", {})
        assert stopped["state"] == "stopped"
        assert services.world.get_card(sandbox.id).status == "stopped"
        await services.update_edge(edge.id, EdgePatch(relationship="execute"))
        for action in ("start", "stop"):
            with pytest.raises(PermissionDeniedError):
                await provider.invoke_tool(agent.id, f"sandbox.{action}:{sandbox.id}", {})
            with pytest.raises(PermissionDeniedError):
                await getattr(services, f"{action}_sandbox")(sandbox.id, agent_id=agent.id)
        with pytest.raises(PermissionDeniedError):
            await provider.invoke_tool(agent.id, definitions["start_sandbox"].capability_id, {"sandbox": sandbox.id})
        assert "sandbox.execute" in {c.kind for c in services.capabilities.derive(agent.id).capabilities}
    finally:
        services.close()
