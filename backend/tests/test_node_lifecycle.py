from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from backend.agents import AgentNotFoundError, MockAgentRuntime
from backend.config import Settings
from backend.plugins import (
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeTypeDefinition,
    create_builtin_registry,
)
from backend.sandbox import SandboxInfo, SandboxNotFoundError, SandboxState
from backend.services import create_services
from backend.world.models import Card, CardCreate, CardPatch


class PluginNodeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: int = 0


def _plugin_node(
    type_id: str, lifecycle: NodeLifecycleHandler | None
) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        id=type_id,
        label="Plugin node",
        description="Lifecycle test node",
        icon="box",
        color="#777777",
        deck_id="test.nodes",
        deck_label="Test",
        deck_icon="boxes",
        default_name="Plugin node",
        default_size=(240, 160),
        default_status="available",
        statuses=frozenset({"available"}),
        config_model=PluginNodeConfig,
        lifecycle=lifecycle,
    )


class RecordingBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.values: dict[str, int] = {}

    @staticmethod
    def _assert_context_is_narrow(context: NodeLifecycleContext) -> None:
        assert not hasattr(context, "database")
        assert not hasattr(context, "events")
        assert not hasattr(context, "settings")

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        self._assert_context_is_narrow(context)
        self.calls.append(("startup", node.id))

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        self._assert_context_is_narrow(context)
        self.calls.append(("shutdown", node.id))

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        self._assert_context_is_narrow(context)
        self.values[node.id] = int(request.config.get("value", 0))
        self.calls.append(("create", node.id))

    async def on_update(
        self, context: NodeLifecycleContext, node: Card, request: CardPatch
    ) -> None:
        self._assert_context_is_narrow(context)
        if request.config is not None and "value" in request.config:
            self.values[node.id] = int(request.config["value"])
        self.calls.append(("update", node.id))

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        self._assert_context_is_narrow(context)
        self.values.pop(node.id, None)
        self.calls.append(("delete", node.id))


class FailingCreateBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.active: set[str] = set()
        self.rolled_back: list[str] = []

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        del context, request
        self.active.add(node.id)
        raise RuntimeError("plugin runtime creation failed")

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        del context, request
        assert str(error) == "plugin runtime creation failed"
        self.active.remove(node.id)
        self.rolled_back.append(node.id)


@pytest.mark.asyncio
async def test_custom_plugin_lifecycle_dispatches_without_core_changes(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    registry.register_node_type(_plugin_node("example.executable", behavior))
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        node = await services.create_card(
            CardCreate(id="plugin-node", type="example.executable", config={"value": 3})
        )
        updated = await services.update_card(node.id, CardPatch(config={"value": 8}))
        await services.startup()
        await services.shutdown()
        deleted = await services.delete_card(node.id)

        assert updated.config["value"] == 8
        assert deleted.id == node.id
        assert behavior.values == {}
        assert behavior.calls == [
            ("create", node.id),
            ("update", node.id),
            ("startup", node.id),
            ("shutdown", node.id),
            ("delete", node.id),
        ]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failed_plugin_creation_runs_rollback_and_removes_persisted_node(
    tmp_path: Path,
) -> None:
    behavior = FailingCreateBehavior()
    registry = create_builtin_registry()
    registry.register_node_type(_plugin_node("example.failing", behavior))
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        with pytest.raises(RuntimeError, match="plugin runtime creation failed"):
            await services.create_card(
                CardCreate(id="failed-node", type="example.failing")
            )

        assert behavior.active == set()
        assert behavior.rolled_back == ["failed-node"]
        assert services.world.maybe_get_card("failed-node") is None
    finally:
        services.close()


@pytest.mark.asyncio
async def test_node_without_lifecycle_is_a_passive_persisted_object(
    tmp_path: Path,
) -> None:
    registry = create_builtin_registry()
    registry.register_node_type(_plugin_node("example.passive", None))
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        node = await services.create_card(
            CardCreate(id="passive-node", type="example.passive", config={"value": 1})
        )
        await services.startup()
        updated = await services.update_card(node.id, CardPatch(config={"value": 2}))
        await services.shutdown()
        deleted = await services.delete_card(node.id)

        assert updated.config["value"] == 2
        assert deleted.id == "passive-node"
        assert services.world.maybe_get_card(node.id) is None
    finally:
        services.close()


class EmptyCapabilityProvider:
    async def list_tools(self, agent_id: str) -> tuple[Any, ...]:
        del agent_id
        return ()

    async def invoke_tool(
        self, agent_id: str, capability_id: str, arguments: dict[str, Any]
    ) -> Any:
        del agent_id, capability_id, arguments
        raise AssertionError("no tools are available")


class RecordingAgentRuntime(MockAgentRuntime):
    def __init__(self) -> None:
        super().__init__(EmptyCapabilityProvider())  # type: ignore[arg-type]
        self.stopped: list[str] = []

    async def stop(self, agent_id: str) -> None:
        self.stopped.append(agent_id)
        await super().stop(agent_id)


class RecordingSandboxBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.states: dict[str, SandboxState] = {}
        self.calls: list[tuple[str, str]] = []

    async def create(self, sandbox_id: str) -> SandboxInfo:
        self.calls.append(("create", sandbox_id))
        self.states[sandbox_id] = SandboxState.READY
        return await self.get(sandbox_id)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        if sandbox_id not in self.states:
            raise SandboxNotFoundError(f"sandbox not found: {sandbox_id}")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            state=self.states[sandbox_id],
            workspace=self.root / sandbox_id / "workspace",
        )

    async def terminate(self, sandbox_id: str) -> None:
        await self.get(sandbox_id)
        self.calls.append(("terminate", sandbox_id))
        self.states[sandbox_id] = SandboxState.STOPPED

    async def destroy(self, sandbox_id: str) -> None:
        await self.get(sandbox_id)
        self.calls.append(("destroy", sandbox_id))
        del self.states[sandbox_id]

    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        await self.get(sandbox_id)
        self.calls.append(("detach", f"{sandbox_id}:{resource_id}"))


@pytest.mark.asyncio
async def test_builtin_agent_startup_reconstructs_and_shutdown_stops_runtime(
    tmp_path: Path,
) -> None:
    settings = Settings.for_data_root(tmp_path / "managed")
    seed = create_services(settings)
    try:
        await seed.create_card(
            CardCreate(id="restored-agent", type="agent", status="running")
        )
    finally:
        seed.close()

    runtime = RecordingAgentRuntime()
    restored = create_services(
        settings,
        runtime_providers={"test.runtime": runtime},
        default_runtime_provider_id="test.runtime",
    )
    try:
        await restored.startup()
        info = await runtime.get_agent("restored-agent")
        assert info.config.agent_id == "restored-agent"
        assert restored.get_card("restored-agent").status == "idle"

        await restored.shutdown()
        assert runtime.stopped == []
        assert restored._require_run_manager()._runtime_tasks == {}
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_builtin_node_lifecycle_behavior_is_registered_and_preserved(
    tmp_path: Path,
) -> None:
    registry = create_builtin_registry()
    assert all(
        registry.node_type(type_id).lifecycle is not None
        for type_id in ("agent", "sandbox", "conversation", "text", "image")
    )

    runtime = MockAgentRuntime(EmptyCapabilityProvider())  # type: ignore[arg-type]
    sandbox = RecordingSandboxBackend(tmp_path / "sandboxes")
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=registry,
        runtime_providers={"test.runtime": runtime},
        default_runtime_provider_id="test.runtime",
        sandbox_backend=sandbox,  # type: ignore[arg-type]
    )
    try:
        sandbox_node = await services.create_card(
            CardCreate(id="sandbox-node", type="sandbox")
        )
        assert sandbox_node.status == "stopped"
        await services.startup()
        assert services.get_card(sandbox_node.id).status == "ready"
        await services.shutdown()
        await services.delete_card(sandbox_node.id)
        assert sandbox.calls == [
            ("create", sandbox_node.id),
            ("terminate", sandbox_node.id),
            ("destroy", sandbox_node.id),
        ]

        agent = await services.create_card(
            CardCreate(id="agent-node", type="agent", name="Atlas")
        )
        assert (await runtime.get_agent(agent.id)).config.name == "Atlas"
        await services.update_card(agent.id, CardPatch(name="Nova"))
        assert (await runtime.get_agent(agent.id)).config.name == "Nova"
        await services.delete_card(agent.id)
        with pytest.raises(AgentNotFoundError):
            await runtime.get_agent(agent.id)

        conversation = await services.create_card(
            CardCreate(id="conversation-node", type="conversation")
        )
        assert [
            session.title for session in services.conversations.list_sessions(conversation.id)
        ] == ["General"]

        text = await services.create_card(
            CardCreate(
                id="text-node",
                type="text",
                config={"filename": "notes.txt"},
                content="hello",
            )
        )
        text_path = services.resources.resolve_relative_path(
            services.resources.get_record(text.id).relative_path
        )
        assert text_path.read_text(encoding="utf-8") == "hello"
        await services.delete_card(text.id)
        assert not text_path.exists()

        image = await services.create_card(
            CardCreate(
                id="image-node",
                type="image",
                config={"filename": "pixel.gif"},
                media_type="image/gif",
                data_base64=base64.b64encode(b"GIF89a\x01\x00\x01\x00").decode(),
            )
        )
        image_path = services.resources.resolve_relative_path(
            services.resources.get_record(image.id).relative_path
        )
        assert image_path.exists()
        await services.delete_card(image.id)
        assert not image_path.exists()
    finally:
        services.close()
