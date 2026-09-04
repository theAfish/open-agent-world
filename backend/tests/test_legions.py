from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from backend.agents import MockAgentRuntime
from backend.config import Settings
from backend.errors import PluginCompatibilityError, ResourceValidationError
from backend.events.models import EventType
from backend.main import create_app
from backend.legions import LegionCapture, LegionInstantiate
from backend.plugins import (
    PLUGIN_API_VERSION,
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeLifecycleTransaction,
    NodeTemplateCaptureContext,
    NodeTemplateDependency,
    NodeTemplateHandler,
    NodeTemplateRestoreContext,
    NodeTypeDefinition,
    PluginDefinition,
    PluginDescriptor,
    PluginRegistration,
    RelationshipDefinition,
    create_builtin_registry,
)
from backend.services import create_services
from backend.sandbox import (
    CommandResult,
    SandboxEvent,
    SandboxEventType,
    SandboxInfo,
    SandboxNotFoundError,
    SandboxState,
)
from backend.world.models import Card, CardCreate


def _create_node(client: TestClient, card_type: str, **values: Any) -> dict[str, Any]:
    response = client.post("/api/nodes", json={"type": card_type, **values})
    assert response.status_code == 201, response.text
    return response.json()


def _create_edge(
    client: TestClient, source: str, target: str, relationship: str
) -> dict[str, Any]:
    response = client.post(
        "/api/edges",
        json={"source": source, "target": target, "relationship": relationship},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_legion_captures_induced_graph_and_restores_portable_state(
    client: TestClient,
) -> None:
    agent = _create_node(
        client,
        "agent",
        name="Scout",
        position={"x": 100, "y": 300},
        status="running",
        config={"api_key": "machine-local-secret"},
    )
    text = _create_node(
        client,
        "text",
        name="Orders",
        position={"x": -50, "y": 25},
        size={"width": 210, "height": 130},
        expanded=True,
        config={"filename": "orders.txt"},
        content="hold the bridge",
    )
    sandbox = _create_node(
        client,
        "sandbox",
        name="Field",
        position={"x": 500, "y": 100},
        status="running",
    )
    outsider = _create_node(client, "agent", name="Outside")
    _create_edge(client, agent["id"], text["id"], "read")
    _create_edge(client, text["id"], sandbox["id"], "mount_read_only")
    _create_edge(client, outsider["id"], agent["id"], "communicate")

    captured = client.post(
        "/api/legions",
        json={
            "name": " Bridge team ",
            "description": " Portable formation ",
            "node_ids": [agent["id"], text["id"], sandbox["id"]],
        },
    )
    assert captured.status_code == 201, captured.text
    legion = captured.json()
    assert legion["name"] == "Bridge team"
    assert legion["description"] == "Portable formation"
    assert legion["node_count"] == 3
    assert legion["edge_count"] == 2
    assert legion["bounds"] == {"width": 890.0, "height": 465.0}
    assert legion["node_types"] == ["agent", "sandbox", "text"]
    assert legion["plugin_ids"] == ["open-agent-world.core"]
    assert legion["compatible"] is True
    assert legion["issues"] == []
    assert client.get("/api/legions").json() == [legion]

    for node in (agent, text, sandbox):
        assert client.delete(f"/api/nodes/{node['id']}").status_code == 200

    deployed = client.post(
        f"/api/legions/{legion['id']}/instances",
        json={"position": {"x": 1000, "y": 2000}},
    )
    assert deployed.status_code == 201, deployed.text
    instance = deployed.json()
    assert instance["legion_id"] == legion["id"]
    assert len(instance["nodes"]) == 3
    assert len(instance["edges"]) == 2
    by_name = {node["name"]: node for node in instance["nodes"]}
    assert by_name["Orders"]["position"] == {"x": 1000.0, "y": 2000.0}
    assert by_name["Scout"]["position"] == {"x": 1150.0, "y": 2275.0}
    assert by_name["Field"]["position"] == {"x": 1550.0, "y": 2075.0}
    assert by_name["Orders"]["size"] == {"width": 210.0, "height": 130.0}
    assert by_name["Orders"]["expanded"] is True
    assert by_name["Scout"]["status"] == "idle"
    assert "api_key" not in by_name["Scout"]["config"]
    assert by_name["Field"]["status"] == "stopped"
    batch_updated = client.post("/api/nodes/batch-update", json={"updates": [
        {
            "node_id": by_name["Scout"]["id"],
            "patch": {"position": {"x": 1250, "y": 2275}},
        },
        {
            "node_id": by_name["Orders"]["id"],
            "patch": {"position": {"x": 1100, "y": 2000}},
        },
    ]})
    assert batch_updated.status_code == 200, batch_updated.text
    assert [node["position"] for node in batch_updated.json()] == [
        {"x": 1250.0, "y": 2275.0},
        {"x": 1100.0, "y": 2000.0},
    ]
    text_response = client.get(f"/api/resources/{by_name['Orders']['id']}/text")
    assert text_response.status_code == 200
    assert text_response.json()["content"] == "hold the bridge"
    assert {
        (edge["relationship"], edge["source"], edge["target"])
        for edge in instance["edges"]
    } == {
        ("read", by_name["Scout"]["id"], by_name["Orders"]["id"]),
        ("mount_read_only", by_name["Orders"]["id"], by_name["Field"]["id"]),
    }

    batch_deleted = client.post(
        "/api/nodes/batch-delete",
        json={"node_ids": [node["id"] for node in instance["nodes"]]},
    )
    assert batch_deleted.status_code == 200, batch_deleted.text
    assert {node["id"] for node in batch_deleted.json()} == {
        node["id"] for node in instance["nodes"]
    }

    removed = client.delete(f"/api/legions/{legion['id']}")
    assert removed.status_code == 200
    assert removed.json()["id"] == legion["id"]
    assert client.get("/api/legions").json() == []


def test_legion_image_instances_own_independent_resource_files(
    client: TestClient,
) -> None:
    image_bytes = b"GIF89a\x01\x00\x01\x00"
    image = _create_node(
        client,
        "image",
        name="Badge",
        config={"filename": "badge.gif"},
        data_base64=base64.b64encode(image_bytes).decode("ascii"),
        media_type="image/gif",
    )
    agent = _create_node(client, "agent", name="Bearer", position={"x": 320, "y": 0})
    captured = client.post("/api/legions", json={
        "name": "Badge bearers",
        "node_ids": [image["id"], agent["id"]],
    })
    assert captured.status_code == 201, captured.text
    legion_id = captured.json()["id"]

    instances = [
        client.post(
            f"/api/legions/{legion_id}/instances",
            json={"position": {"x": offset, "y": 500}},
        ).json()
        for offset in (1000, 1700)
    ]
    image_ids = [
        next(node["id"] for node in instance["nodes"] if node["type"] == "image")
        for instance in instances
    ]
    assert image_ids[0] != image_ids[1]
    assert all(
        client.get(f"/api/resources/{image_id}/content").content == image_bytes
        for image_id in image_ids
    )
    paths = [
        client.get(f"/api/resources/{image_id}").json()["relative_path"]
        for image_id in image_ids
    ]
    assert paths[0] != paths[1]


def test_modified_resource_statuses_remain_compatible_and_round_trip(
    client: TestClient,
) -> None:
    text = _create_node(
        client,
        "text",
        name="Working notes",
        status="modified",
        config={"filename": "working-notes.txt"},
        content="draft",
    )
    image = _create_node(
        client,
        "image",
        name="Working diagram",
        status="modified",
        config={"filename": "working-diagram.gif"},
        data_base64=base64.b64encode(b"GIF89a\x01\x00\x01\x00").decode("ascii"),
        media_type="image/gif",
    )

    captured = client.post("/api/legions", json={
        "name": "Work in progress",
        "node_ids": [text["id"], image["id"]],
    })
    assert captured.status_code == 201, captured.text
    legion = captured.json()
    assert legion["compatible"] is True
    assert legion["issues"] == []

    deployed = client.post(
        f"/api/legions/{legion['id']}/instances",
        json={"position": {"x": 800, "y": 600}},
    )
    assert deployed.status_code == 201, deployed.text
    by_type = {node["type"]: node for node in deployed.json()["nodes"]}
    assert by_type["text"]["status"] == "modified"
    assert by_type["text"]["config"]["status"] == "modified"
    assert by_type["image"]["status"] == "modified"
    assert by_type["image"]["config"]["status"] == "modified"


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int = 0


def _plugin_node(
    node_type: str,
    *,
    templateable: bool,
    lifecycle: NodeLifecycleHandler | None = None,
    template_handler: NodeTemplateHandler | None = None,
) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        id=node_type,
        label="Plugin node",
        description="Template test node",
        icon="box",
        color="#777777",
        deck_id="test",
        deck_label="Test",
        deck_icon="boxes",
        default_name="Plugin node",
        default_size=(200, 120),
        default_status="available",
        statuses=frozenset({"available"}),
        config_model=PluginConfig,
        lifecycle=lifecycle,
        templateable=templateable,
        template_handler=template_handler,
    )


def _registry_with_node(definition: NodeTypeDefinition):
    registry = create_builtin_registry()

    def configure(registration: PluginRegistration) -> None:
        registration.register_node_type(definition)

    registry.install(PluginDefinition(
        descriptor=PluginDescriptor(
            id="example.legion",
            version="1.0.0",
            plugin_api_version=PLUGIN_API_VERSION,
        ),
        configure=configure,
    ))
    return registry


def _registry_with_runtime_provider(
    plugin_id: str, provider_id: str
):
    registry = create_builtin_registry()

    def configure(registration: PluginRegistration) -> None:
        registration.register_runtime_provider(
            provider_id,
            lambda capability_provider, **options: MockAgentRuntime(
                capability_provider
            ),
        )

    registry.install(PluginDefinition(
        descriptor=PluginDescriptor(
            id=plugin_id,
            version="1.0.0",
            plugin_api_version=PLUGIN_API_VERSION,
        ),
        configure=configure,
    ))
    return registry


@pytest.mark.asyncio
async def test_legion_records_runtime_provider_plugin_dependency(
    tmp_path: Path,
) -> None:
    provider_id = "example.portable-runtime"
    owner_id = "example.runtime-owner"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_runtime_provider(owner_id, provider_id),
    )
    try:
        nodes = [
            await services.create_card(CardCreate(
                type="agent",
                name=name,
                config={"runtime_provider_id": provider_id},
            ))
            for name in ("One", "Two")
        ]

        legion = await services.capture_legion(LegionCapture(
            name="Provider formation",
            node_ids=[node.id for node in nodes],
        ))

        assert legion.compatible is True
        assert legion.plugin_ids == ["example.runtime-owner", "open-agent-world.core"]
        record = services.legions.get(legion.id)
        for node in record.blueprint.nodes:
            assert [dependency.model_dump() for dependency in node.dependencies] == [{
                "kind": "runtime_provider",
                "id": provider_id,
                "plugin_id": owner_id,
            }]
        instance = await services.instantiate_legion(
            legion.id, LegionInstantiate(position={"x": 100, "y": 200})
        )
        assert len(instance.nodes) == 2
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replacement_owner", "expected_issue"),
    [
        (None, "requires missing plugin 'example.runtime-owner'"),
        (
            "example.replacement-owner",
            "is now owned by 'example.replacement-owner', not 'example.runtime-owner'",
        ),
    ],
    ids=["removed", "reowned"],
)
async def test_legion_runtime_provider_dependency_fails_closed_after_registry_change(
    tmp_path: Path,
    replacement_owner: str | None,
    expected_issue: str,
) -> None:
    settings = Settings.for_data_root(tmp_path / "managed")
    provider_id = "example.portable-runtime"
    services = create_services(
        settings,
        plugins=_registry_with_runtime_provider(
            "example.runtime-owner", provider_id
        ),
    )
    try:
        nodes = [
            await services.create_card(CardCreate(
                type="agent",
                config={"runtime_provider_id": provider_id},
            ))
            for _ in range(2)
        ]
        legion = await services.capture_legion(LegionCapture(
            name="Provider formation",
            node_ids=[node.id for node in nodes],
        ))
        await services.delete_cards([node.id for node in nodes])
    finally:
        services.close()

    replacement_registry = (
        create_builtin_registry()
        if replacement_owner is None
        else _registry_with_runtime_provider(replacement_owner, provider_id)
    )
    reopened = create_services(settings, plugins=replacement_registry)
    try:
        summary = reopened.list_legions()[0]
        assert summary.id == legion.id
        assert summary.compatible is False
        assert expected_issue in " ".join(summary.issues)
        with pytest.raises(PluginCompatibilityError, match="is incompatible"):
            await reopened.instantiate_legion(
                legion.id, LegionInstantiate(position={"x": 0, "y": 0})
            )
        assert reopened.world.list_cards() == []
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_legion_capture_rejects_unattributed_injected_runtime_provider(
    tmp_path: Path,
) -> None:
    services = create_services(Settings.for_data_root(tmp_path / "managed"))
    try:
        manager = services.run_manager
        assert manager is not None
        services.install_runtime_provider(
            "injected.runtime",
            MockAgentRuntime(manager.capability_provider),
        )
        nodes = [
            await services.create_card(CardCreate(
                type="agent",
                config={"runtime_provider_id": "injected.runtime"},
            ))
            for _ in range(2)
        ]

        with pytest.raises(
            PluginCompatibilityError,
            match="unattributed template dependency runtime provider 'injected.runtime'",
        ):
            await services.capture_legion(LegionCapture(
                name="Unattributed",
                node_ids=[node.id for node in nodes],
            ))
        assert services.legions.list() == []
    finally:
        services.close()


@pytest.mark.asyncio
async def test_missing_plugin_marks_saved_legion_incompatible_without_blocking_startup(
    tmp_path: Path,
) -> None:
    settings = Settings.for_data_root(tmp_path / "managed")
    registry = _registry_with_node(
        _plugin_node("example.legion-node", templateable=True)
    )
    services = create_services(settings, plugins=registry)
    try:
        node = await services.create_card(CardCreate(
            id="plugin-source",
            type="example.legion-node",
            config={"value": 7},
        ))
        second = await services.create_card(CardCreate(
            id="plugin-source-two",
            type="example.legion-node",
            config={"value": 8},
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Plugin formation",
            node_ids=[node.id, second.id],
        ))
        await services.delete_card(node.id)
        await services.delete_card(second.id)
    finally:
        services.close()

    application = create_app(settings)
    with TestClient(application) as client:
        listed = client.get("/api/legions")
        assert listed.status_code == 200
        summary = listed.json()[0]
        assert summary["id"] == legion.id
        assert summary["compatible"] is False
        assert "missing plugin 'example.legion'" in " ".join(summary["issues"])
        deployed = client.post(
            f"/api/legions/{legion.id}/instances",
            json={"position": {"x": 0, "y": 0}},
        )
        assert deployed.status_code == 422
        assert client.get("/api/world").json()["nodes"] == []


class FailingCloneLifecycle(NodeLifecycleHandler):
    fail_name: str | None = None

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del context, request
        lifecycle = self

        class Mutation(NodeLifecycleTransaction):
            async def commit(self) -> None:
                if node.name == lifecycle.fail_name:
                    raise RuntimeError("clone lifecycle failed")

        return Mutation()


class FailingDeleteLifecycle(NodeLifecycleHandler):
    fail_name: str | None = None

    def __init__(self) -> None:
        self.deleted_node_ids: set[str] = set()

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        del context
        lifecycle = self

        class Mutation(NodeLifecycleTransaction):
            async def commit(self) -> None:
                if node.name == lifecycle.fail_name:
                    raise RuntimeError("batch delete lifecycle failed")
                lifecycle.deleted_node_ids.add(node.id)

            async def rollback(self, error: BaseException) -> None:
                del error
                lifecycle.deleted_node_ids.discard(node.id)

        return Mutation()


class BlockingFailingCloneLifecycle(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.enabled = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del context, request
        lifecycle = self

        class Mutation(NodeLifecycleTransaction):
            async def commit(self) -> None:
                if lifecycle.enabled and node.name == "Two":
                    lifecycle.entered.set()
                    await lifecycle.release.wait()
                    raise RuntimeError("blocked clone failed")

        return Mutation()


class CoordinatedSandboxBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ids: set[str] = set()
        self.event_sink: Any = None
        self.delay_create_events = False
        self.event_release = asyncio.Event()
        self.event_tasks: list[asyncio.Task[None]] = []
        self.block_commands = False
        self.command_entered = asyncio.Event()
        self.command_release = asyncio.Event()

    async def create(self, sandbox_id: str) -> SandboxInfo:
        self.ids.add(sandbox_id)
        if self.delay_create_events and self.event_sink is not None:
            async def emit_later() -> None:
                await self.event_release.wait()
                await self.event_sink(SandboxEvent(
                    sandbox_id=sandbox_id,
                    type=SandboxEventType.STATE_CHANGED,
                    payload={"state": SandboxState.READY.value},
                ))

            self.event_tasks.append(asyncio.create_task(emit_later()))
        return await self.get(sandbox_id)

    async def start(self, sandbox_id: str) -> SandboxInfo:
        return await self.get(sandbox_id)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        if sandbox_id not in self.ids:
            raise SandboxNotFoundError(sandbox_id)
        return SandboxInfo(
            sandbox_id=sandbox_id,
            state=SandboxState.READY,
            workspace=self.root / sandbox_id / "workspace",
        )

    async def execute(
        self,
        sandbox_id: str,
        argv: Any,
        *,
        timeout_seconds: float | None = None,
        env: Any = None,
    ) -> CommandResult:
        del timeout_seconds, env
        await self.get(sandbox_id)
        if self.block_commands:
            self.command_entered.set()
            await self.command_release.wait()
        return CommandResult(
            sandbox_id=sandbox_id,
            argv=tuple(argv),
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0,
        )

    async def terminate(self, sandbox_id: str) -> None:
        await self.get(sandbox_id)

    async def destroy(self, sandbox_id: str) -> None:
        if sandbox_id not in self.ids:
            raise SandboxNotFoundError(sandbox_id)
        self.ids.remove(sandbox_id)

    async def attach_resource(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def detach_resource(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


@pytest.mark.asyncio
async def test_legion_instance_is_hidden_from_snapshots_until_complete(
    tmp_path: Path,
) -> None:
    lifecycle = BlockingFailingCloneLifecycle()
    node_type = "example.snapshot-barrier"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(
            node_type, templateable=True, lifecycle=lifecycle
        )),
    )
    try:
        one = await services.create_card(CardCreate(
            id="barrier-source-one", type=node_type, name="One"
        ))
        two = await services.create_card(CardCreate(
            id="barrier-source-two", type=node_type, name="Two"
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Snapshot barrier", node_ids=[one.id, two.id]
        ))
        lifecycle.enabled = True
        deployment = asyncio.create_task(services.instantiate_legion(
            legion.id, LegionInstantiate(position={"x": 500, "y": 500})
        ))
        await lifecycle.entered.wait()
        snapshot = asyncio.create_task(services.snapshot())
        edges = asyncio.create_task(services.read_edges())
        source = asyncio.create_task(services.read_card(one.id))
        await asyncio.sleep(0.02)
        assert not snapshot.done()
        assert not edges.done()
        assert not source.done()

        lifecycle.release.set()
        with pytest.raises(RuntimeError, match="blocked clone failed"):
            await deployment
        visible = await snapshot
        assert await edges == []
        assert (await source).id == one.id
        assert {node.id for node in visible.nodes} == {one.id, two.id}
    finally:
        services.close()


@pytest.mark.asyncio
async def test_mutation_ownership_is_not_inherited_by_child_tasks(
    tmp_path: Path,
) -> None:
    services = create_services(Settings.for_data_root(tmp_path / "managed"))
    try:
        async with services._node_mutation():
            child_read = asyncio.create_task(services.read_edges())
            await asyncio.sleep(0.02)
            assert not child_read.done()
        assert await child_read == []
    finally:
        services.close()


@pytest.mark.asyncio
async def test_sandbox_execution_blocks_capture_but_not_world_reads(
    tmp_path: Path,
) -> None:
    backend = CoordinatedSandboxBackend(tmp_path / "sandboxes")
    backend.block_commands = True
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        sandbox_backend=backend,  # type: ignore[arg-type]
    )
    try:
        sandbox = await services.create_card(CardCreate(type="sandbox", name="Field"))
        agent = await services.create_card(CardCreate(type="agent", name="Scout"))
        execution = asyncio.create_task(
            services.execute_sandbox(sandbox.id, ["cmd.exe", "/c", "exit", "0"])
        )
        await backend.command_entered.wait()
        capture = asyncio.create_task(services.capture_legion(LegionCapture(
            name="Stable state", node_ids=[sandbox.id, agent.id]
        )))
        await asyncio.sleep(0.02)
        assert not capture.done()

        visible = await asyncio.wait_for(services.snapshot(), timeout=0.5)
        assert {node.id for node in visible.nodes} == {sandbox.id, agent.id}

        backend.command_release.set()
        await execution
        assert (await capture).node_count == 2
    finally:
        backend.command_release.set()
        services.close()


@pytest.mark.asyncio
async def test_cancelled_sandbox_execution_keeps_capture_lease_until_terminated(
    tmp_path: Path,
) -> None:
    class CancellableBackend(CoordinatedSandboxBackend):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.terminate_entered = asyncio.Event()
            self.terminate_release = asyncio.Event()

        async def terminate(self, sandbox_id: str) -> None:
            await self.get(sandbox_id)
            self.terminate_entered.set()
            await self.terminate_release.wait()
            self.command_release.set()

    backend = CancellableBackend(tmp_path / "sandboxes")
    backend.block_commands = True
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        sandbox_backend=backend,  # type: ignore[arg-type]
    )
    try:
        sandbox = await services.create_card(CardCreate(type="sandbox", name="Field"))
        agent = await services.create_card(CardCreate(type="agent", name="Scout"))
        execution = asyncio.create_task(
            services.execute_sandbox(sandbox.id, ["long-command"])
        )
        await backend.command_entered.wait()
        execution.cancel()
        await backend.terminate_entered.wait()

        capture = asyncio.create_task(services.capture_legion(LegionCapture(
            name="After cancellation", node_ids=[sandbox.id, agent.id]
        )))
        await asyncio.sleep(0.02)
        assert not capture.done()
        assert len((await asyncio.wait_for(services.snapshot(), timeout=0.5)).nodes) == 2

        backend.terminate_release.set()
        with pytest.raises(asyncio.CancelledError):
            await execution
        assert (await capture).node_count == 2
    finally:
        backend.terminate_release.set()
        backend.command_release.set()
        services.close()


@pytest.mark.asyncio
async def test_failed_legion_instance_compensates_nodes_created_earlier(
    tmp_path: Path,
) -> None:
    lifecycle = FailingCloneLifecycle()
    registry = _registry_with_node(_plugin_node(
        "example.fallible", templateable=True, lifecycle=lifecycle
    ))
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        one = await services.create_card(CardCreate(
            id="source-one", type="example.fallible", name="One"
        ))
        two = await services.create_card(CardCreate(
            id="source-two", type="example.fallible", name="Two", position={"x": 250, "y": 0}
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Fallible",
            node_ids=[one.id, two.id],
        ))
        lifecycle.fail_name = "Two"

        with pytest.raises(RuntimeError, match="clone lifecycle failed"):
            await services.instantiate_legion(
                legion.id, LegionInstantiate(position={"x": 900, "y": 900})
            )

        assert {node.id for node in services.world.list_cards()} == {
            "source-one",
            "source-two",
        }
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failed_legion_instance_discards_sandbox_lifecycle_events(
    tmp_path: Path,
) -> None:
    class EmittingSandboxBackend:
        def __init__(self) -> None:
            self.ids: set[str] = set()
            self.event_sink: Any = None

        async def _emit(self, sandbox_id: str, state: SandboxState) -> None:
            if self.event_sink is not None:
                await self.event_sink(SandboxEvent(
                    sandbox_id=sandbox_id,
                    type=SandboxEventType.STATE_CHANGED,
                    payload={"state": state.value},
                ))

        async def create(self, sandbox_id: str) -> SandboxInfo:
            self.ids.add(sandbox_id)
            await self._emit(sandbox_id, SandboxState.READY)
            return await self.get(sandbox_id)

        async def get(self, sandbox_id: str) -> SandboxInfo:
            if sandbox_id not in self.ids:
                raise SandboxNotFoundError(sandbox_id)
            return SandboxInfo(
                sandbox_id=sandbox_id,
                state=SandboxState.READY,
                workspace=tmp_path / sandbox_id / "workspace",
            )

        async def terminate(self, sandbox_id: str) -> None:
            await self.get(sandbox_id)

        async def destroy(self, sandbox_id: str) -> None:
            if sandbox_id not in self.ids:
                raise SandboxNotFoundError(sandbox_id)
            self.ids.remove(sandbox_id)
            await self._emit(sandbox_id, SandboxState.STOPPED)

    lifecycle = FailingCloneLifecycle()
    node_type = "example.after-sandbox"
    backend = EmittingSandboxBackend()
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(
            node_type, templateable=True, lifecycle=lifecycle
        )),
        sandbox_backend=backend,  # type: ignore[arg-type]
    )
    backend.event_sink = services.publish_sandbox_event
    try:
        sandbox = await services.create_card(CardCreate(
            id="event-source-sandbox", type="sandbox", name="Field"
        ))
        failing = await services.create_card(CardCreate(
            id="event-source-failure", type=node_type, name="Two"
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Event isolation", node_ids=[sandbox.id, failing.id]
        ))
        lifecycle.fail_name = "Two"

        async with services.events.subscribe() as events:
            with pytest.raises(RuntimeError, match="clone lifecycle failed"):
                await services.instantiate_legion(
                    legion.id, LegionInstantiate(position={"x": 900, "y": 900})
                )
            assert events.empty()
    finally:
        services.close()


@pytest.mark.asyncio
async def test_late_sandbox_events_follow_formation_commit_or_rollback(
    tmp_path: Path,
) -> None:
    backend = CoordinatedSandboxBackend(tmp_path / "sandboxes")
    lifecycle = FailingCloneLifecycle()
    node_type = "example.after-delayed-sandbox"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(
            node_type, templateable=True, lifecycle=lifecycle
        )),
        sandbox_backend=backend,  # type: ignore[arg-type]
    )
    backend.event_sink = services.publish_sandbox_event
    try:
        sandbox = await services.create_card(CardCreate(
            id="delayed-event-sandbox", type="sandbox", name="Field"
        ))
        plugin_node = await services.create_card(CardCreate(
            id="delayed-event-plugin", type=node_type, name="Two"
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Delayed events", node_ids=[sandbox.id, plugin_node.id]
        ))
        backend.delay_create_events = True

        async with services.events.subscribe() as committed_events:
            instance = await services.instantiate_legion(
                legion.id, LegionInstantiate(position={"x": 400, "y": 400})
            )
            cloned_sandbox_id = next(
                node.id for node in instance.nodes if node.type == "sandbox"
            )
            backend.event_release.set()
            await asyncio.gather(*backend.event_tasks)
            observed = []
            while not committed_events.empty():
                observed.append(committed_events.get_nowait())
            assert any(
                event.type is EventType.SANDBOX_STATE_CHANGED
                and event.sandbox_id == cloned_sandbox_id
                for event in observed
            )

        backend.event_release = asyncio.Event()
        backend.event_tasks.clear()
        lifecycle.fail_name = "Two"
        async with services.events.subscribe() as rolled_back_events:
            with pytest.raises(RuntimeError, match="clone lifecycle failed"):
                await services.instantiate_legion(
                    legion.id, LegionInstantiate(position={"x": 800, "y": 800})
                )
            backend.event_release.set()
            await asyncio.gather(*backend.event_tasks)
            assert rolled_back_events.empty()
    finally:
        backend.event_release.set()
        services.close()


@pytest.mark.asyncio
async def test_legion_undo_batch_delete_is_atomic_and_delays_events(
    tmp_path: Path,
) -> None:
    lifecycle = FailingDeleteLifecycle()
    registry = _registry_with_node(_plugin_node(
        "example.delete-atomic", templateable=True, lifecycle=lifecycle
    ))
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        one = await services.create_card(CardCreate(
            id="delete-source-one", type="example.delete-atomic", name="One"
        ))
        two = await services.create_card(CardCreate(
            id="delete-source-two", type="example.delete-atomic", name="Two"
        ))
        lifecycle.fail_name = "Two"

        async with services.events.subscribe() as events:
            with pytest.raises(RuntimeError, match="batch delete lifecycle failed"):
                await services.delete_cards([one.id, two.id])
            assert events.empty()

        assert lifecycle.deleted_node_ids == set()
        assert {node.id for node in services.world.list_cards()} == {one.id, two.id}
    finally:
        services.close()


def test_plugin_nodes_must_explicitly_opt_in_to_legion_templates(
    tmp_path: Path,
) -> None:
    registry = _registry_with_node(
        _plugin_node("example.not-templateable", templateable=False)
    )
    settings = Settings.for_data_root(tmp_path / "managed")
    services = create_services(settings, plugins=registry)
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client:
            definition = next(
                item
                for item in client.get("/api/catalog").json()["node_types"]
                if item["id"] == "example.not-templateable"
            )
            assert definition["templateable"] is False
            node = _create_node(client, "example.not-templateable")
            second = _create_node(client, "example.not-templateable")
            response = client.post(
                "/api/legions",
                json={"name": "Unsafe", "node_ids": [node["id"], second["id"]]},
            )
            assert response.status_code == 422
            assert "does not support Legion templates" in response.text
    finally:
        services.close()


class RecordingTemplateHandler(NodeTemplateHandler):
    def __init__(self) -> None:
        self.restored_node_ids: set[str] = set()

    async def capture(
        self,
        context: NodeTemplateCaptureContext,
        node: Card,
        node_keys: Mapping[str, str],
    ) -> dict[str, Any]:
        del node, node_keys
        assert not hasattr(context.resources, "replace_text")
        return {"portable": True}

    def validate_payload(
        self, payload: Mapping[str, Any], payload_version: int
    ) -> None:
        super().validate_payload(payload, payload_version)
        if payload != {"portable": True}:
            raise PluginCompatibilityError("invalid recording payload")

    async def prepare_restore(
        self,
        context: NodeTemplateRestoreContext,
        node: Card,
        payload: Mapping[str, Any],
        payload_version: int,
        node_ids: Mapping[str, str],
    ) -> NodeLifecycleTransaction:
        del context, node_ids
        self.validate_payload(payload, payload_version)
        restored_node_ids = self.restored_node_ids

        class Mutation(NodeLifecycleTransaction):
            async def commit(self) -> None:
                restored_node_ids.add(node.id)

            async def rollback(self, error: BaseException) -> None:
                del error
                restored_node_ids.discard(node.id)

        return Mutation()


@pytest.mark.asyncio
async def test_adding_template_handler_invalidates_config_only_legion(
    tmp_path: Path,
) -> None:
    node_type = "example.handler-added"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(node_type, templateable=True)),
    )
    try:
        one = await services.create_card(CardCreate(type=node_type, name="One"))
        two = await services.create_card(CardCreate(type=node_type, name="Two"))
        legion = await services.capture_legion(LegionCapture(
            name="Before handler", node_ids=[one.id, two.id]
        ))
        current = services.plugins.node_type(node_type)
        services.plugins._nodes[node_type] = replace(  # type: ignore[attr-defined]
            current, template_handler=RecordingTemplateHandler()
        )

        summary = next(
            item for item in services.list_legions() if item.id == legion.id
        )
        assert summary.compatible is False
        assert "now requires template payload" in " ".join(summary.issues)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failed_legion_restore_rolls_back_template_sidecars_without_events(
    tmp_path: Path,
) -> None:
    lifecycle = FailingCloneLifecycle()
    handler = RecordingTemplateHandler()
    registry = _registry_with_node(_plugin_node(
        "example.atomic", templateable=True, lifecycle=lifecycle,
        template_handler=handler,
    ))
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        one = await services.create_card(CardCreate(
            id="atomic-source-one", type="example.atomic", name="One"
        ))
        two = await services.create_card(CardCreate(
            id="atomic-source-two", type="example.atomic", name="Two",
            position={"x": 250, "y": 0},
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Atomic", node_ids=[one.id, two.id]
        ))
        lifecycle.fail_name = "Two"

        async with services.events.subscribe() as events:
            with pytest.raises(RuntimeError, match="clone lifecycle failed"):
                await services.instantiate_legion(
                    legion.id, LegionInstantiate(position={"x": 900, "y": 900})
                )
            assert events.empty()

        assert handler.restored_node_ids == set()
        assert {node.id for node in services.world.list_cards()} == {
            one.id,
            two.id,
        }
    finally:
        services.close()


def test_relationships_must_explicitly_opt_in_to_legion_templates(
    tmp_path: Path,
) -> None:
    registry = create_builtin_registry()

    def configure(registration: PluginRegistration) -> None:
        registration.register_relationship(RelationshipDefinition(
            id="example.private-link",
            label="Private",
            short_label="private",
            description="Not portable unless the plugin opts in.",
            source_types=frozenset({"agent"}),
            target_types=frozenset({"agent"}),
        ))

    registry.install(PluginDefinition(
        descriptor=PluginDescriptor(
            id="example.relationships",
            version="1.0.0",
            plugin_api_version=PLUGIN_API_VERSION,
        ),
        configure=configure,
    ))
    settings = Settings.for_data_root(tmp_path / "managed")
    services = create_services(settings, plugins=registry)
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client:
            relationship = next(
                item
                for item in client.get("/api/catalog").json()["relationships"]
                if item["id"] == "example.private-link"
            )
            assert relationship["templateable"] is False
            one = _create_node(client, "agent")
            two = _create_node(client, "agent")
            _create_edge(client, one["id"], two["id"], "example.private-link")
            response = client.post("/api/legions", json={
                "name": "Unsafe relationship",
                "node_ids": [one["id"], two["id"]],
            })
            assert response.status_code == 422
            assert "does not support Legion templates" in response.text
    finally:
        services.close()


def test_template_status_must_be_representable_by_plugin_config() -> None:
    definition = replace(
        _plugin_node("example.strict-status", templateable=True),
        template_status="available",
    )
    with pytest.raises(ValueError, match="cannot be represented"):
        _registry_with_node(definition)

    class IgnoringStatusConfig(BaseModel):
        value: int = 0

    ignored = replace(
        _plugin_node("example.ignored-status", templateable=True),
        config_model=IgnoringStatusConfig,
        statuses=frozenset({"available", "idle"}),
        template_status="idle",
    )
    with pytest.raises(ValueError, match="cannot be represented"):
        _registry_with_node(ignored)


@pytest.mark.asyncio
async def test_config_model_must_preserve_saved_legion_configuration(
    tmp_path: Path,
) -> None:
    node_type = "example.config-upgrade"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(node_type, templateable=True)),
    )
    try:
        one = await services.create_card(CardCreate(
            type=node_type, name="One", config={"value": 41}
        ))
        two = await services.create_card(CardCreate(
            type=node_type, name="Two", config={"value": 42}
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Old config", node_ids=[one.id, two.id]
        ))

        class NewConfig(BaseModel):
            pass

        current = services.plugins.node_type(node_type)
        services.plugins._nodes[node_type] = replace(  # type: ignore[attr-defined]
            current, config_model=NewConfig
        )
        summary = next(
            item for item in services.list_legions() if item.id == legion.id
        )
        assert summary.compatible is False
        assert "no longer accepted unchanged" in " ".join(summary.issues)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_invalid_upgraded_config_skips_template_dependency_recomputation(
    tmp_path: Path,
) -> None:
    node_type = "example.dependency-upgrade"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(
            node_type,
            templateable=True,
            template_handler=RecordingTemplateHandler(),
        )),
    )
    try:
        one = await services.create_card(CardCreate(
            type=node_type, name="One", config={"value": 41}
        ))
        two = await services.create_card(CardCreate(
            type=node_type, name="Two", config={"value": 42}
        ))
        legion = await services.capture_legion(LegionCapture(
            name="Before dependency upgrade", node_ids=[one.id, two.id]
        ))

        class NewConfig(BaseModel):
            required_dependency: str

        class UpgradedTemplateHandler(RecordingTemplateHandler):
            def dependencies(
                self, config: Mapping[str, Any]
            ) -> tuple[NodeTemplateDependency, ...]:
                config["required_dependency"]
                return ()

        current = services.plugins.node_type(node_type)
        services.plugins._nodes[node_type] = replace(  # type: ignore[attr-defined]
            current,
            config_model=NewConfig,
            template_handler=UpgradedTemplateHandler(),
        )

        summary = next(
            item for item in services.list_legions() if item.id == legion.id
        )
        assert summary.compatible is False
        assert "required_dependency" in " ".join(summary.issues)
        with pytest.raises(PluginCompatibilityError, match="is incompatible"):
            await services.instantiate_legion(
                legion.id, LegionInstantiate(position={"x": 0, "y": 0})
            )
    finally:
        services.close()


@pytest.mark.asyncio
async def test_new_template_dependency_invalidates_older_legion(
    tmp_path: Path,
) -> None:
    node_type = "example.new-dependency"
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=_registry_with_node(_plugin_node(
            node_type,
            templateable=True,
            template_handler=RecordingTemplateHandler(),
        )),
    )
    try:
        one = await services.create_card(CardCreate(type=node_type, name="One"))
        two = await services.create_card(CardCreate(type=node_type, name="Two"))
        legion = await services.capture_legion(LegionCapture(
            name="Before dependency", node_ids=[one.id, two.id]
        ))

        class RequiredDependencyTemplateHandler(RecordingTemplateHandler):
            def dependencies(
                self, config: Mapping[str, Any]
            ) -> tuple[NodeTemplateDependency, ...]:
                del config
                return (NodeTemplateDependency(kind="node_type", id="agent"),)

        current = services.plugins.node_type(node_type)
        services.plugins._nodes[node_type] = replace(  # type: ignore[attr-defined]
            current,
            template_handler=RequiredDependencyTemplateHandler(),
        )

        summary = next(
            item for item in services.list_legions() if item.id == legion.id
        )
        assert summary.compatible is False
        assert "requires unrecorded template dependency node type 'agent'" in (
            " ".join(summary.issues)
        )
        with pytest.raises(PluginCompatibilityError, match="is incompatible"):
            await services.instantiate_legion(
                legion.id, LegionInstantiate(position={"x": 0, "y": 0})
            )
    finally:
        services.close()


@pytest.mark.asyncio
async def test_changed_plugin_template_status_invalidates_older_legion(
    tmp_path: Path,
) -> None:
    services = create_services(Settings.for_data_root(tmp_path / "managed"))
    try:
        one = await services.create_card(CardCreate(type="agent", name="One"))
        two = await services.create_card(CardCreate(type="agent", name="Two"))
        legion = await services.capture_legion(LegionCapture(
            name="Old reset policy", node_ids=[one.id, two.id]
        ))
        current = services.plugins.node_type("agent")
        services.plugins._nodes["agent"] = replace(  # type: ignore[attr-defined]
            current, template_status="waiting"
        )

        summary = next(
            item for item in services.list_legions() if item.id == legion.id
        )
        assert summary.compatible is False
        assert "now requires template status 'waiting'" in " ".join(summary.issues)
        with pytest.raises(PluginCompatibilityError, match="is incompatible"):
            await services.instantiate_legion(
                legion.id, LegionInstantiate(position={"x": 0, "y": 0})
            )
    finally:
        services.close()


@pytest.mark.asyncio
async def test_legion_blueprint_has_an_aggregate_size_limit(tmp_path: Path) -> None:
    services = create_services(Settings.for_data_root(tmp_path / "managed"))
    try:
        one = await services.create_card(CardCreate(type="agent", name="One"))
        two = await services.create_card(CardCreate(type="agent", name="Two"))
        services.legions.MAX_BLUEPRINT_BYTES = 1
        with pytest.raises(ResourceValidationError, match="64 MiB"):
            await services.capture_legion(LegionCapture(
                name="Too large", node_ids=[one.id, two.id]
            ))
    finally:
        services.close()
