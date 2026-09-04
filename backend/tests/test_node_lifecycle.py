from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Mapping

import pytest
from pydantic import BaseModel, ConfigDict

from backend.agents import AgentNotFoundError, MockAgentRuntime
from backend.config import Settings
from backend.errors import (
    ConflictError,
    PluginCompatibilityError,
    RuntimeUnavailableError,
)
from backend.plugins import (
    PLUGIN_API_VERSION,
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeLifecycleTransaction,
    NodeTypeDefinition,
    PluginDefinition,
    PluginDescriptor,
    PluginRegistration,
    create_builtin_registry,
)
from backend.runs import RunStatus
from backend.sandbox import (
    CommandResult,
    SandboxInfo,
    SandboxNotFoundError,
    SandboxState,
)
from backend.services import create_services
from backend.world.models import Card, CardBatchPatch, CardCreate, CardPatch, EdgeCreate


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


def _install_node(
    registry: Any,
    type_id: str,
    lifecycle: NodeLifecycleHandler | None,
    *,
    plugin_api_version: str = PLUGIN_API_VERSION,
) -> None:
    def configure(registration: PluginRegistration) -> None:
        registration.register_node_type(_plugin_node(type_id, lifecycle))

    registry.install(PluginDefinition(
        descriptor=PluginDescriptor(
            id=f"{type_id}.plugin",
            version="1.0.0",
            plugin_api_version=plugin_api_version,
        ),
        configure=configure,
    ))


class RecordingMutation(NodeLifecycleTransaction):
    def __init__(
        self,
        behavior: RecordingBehavior,
        operation: str,
        node_id: str,
        before: int | None,
        after: int | None,
    ) -> None:
        self.behavior = behavior
        self.operation = operation
        self.node_id = node_id
        self.before = before
        self.after = after

    async def commit(self) -> None:
        if self.after is None:
            self.behavior.values.pop(self.node_id, None)
        else:
            self.behavior.values[self.node_id] = self.after
        self.behavior.calls.append((self.operation, self.node_id))
        if self.behavior.fail_on == self.operation and (
            self.behavior.fail_node_id is None
            or self.behavior.fail_node_id == self.node_id
        ):
            raise RuntimeError(f"plugin {self.operation} failed")

    async def rollback(self, error: BaseException) -> None:
        del error
        if self.before is None:
            self.behavior.values.pop(self.node_id, None)
        else:
            self.behavior.values[self.node_id] = self.before
        self.behavior.calls.append((f"rollback_{self.operation}", self.node_id))


class RecordingBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.values: dict[str, int] = {}
        self.fail_on: str | None = None
        self.fail_node_id: str | None = None

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

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        self._assert_context_is_narrow(context)
        return RecordingMutation(
            self, "create", node.id, None, int(request.config.get("value", 0))
        )

    async def prepare_update(
        self,
        context: NodeLifecycleContext,
        current: Card,
        updated: Card,
        request: CardPatch,
    ) -> NodeLifecycleTransaction:
        self._assert_context_is_narrow(context)
        del request
        return RecordingMutation(
            self,
            "update",
            current.id,
            int(current.config.get("value", 0)),
            int(updated.config.get("value", 0)),
        )

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        self._assert_context_is_narrow(context)
        return RecordingMutation(
            self, "delete", node.id, int(node.config.get("value", 0)), None
        )


class BlockingFailingDeleteBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.commit_entered = asyncio.Event()
        self.release_commit = asyncio.Event()

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        del context, node
        behavior = self

        class Transaction(NodeLifecycleTransaction):
            async def commit(self) -> None:
                behavior.commit_entered.set()
                await behavior.release_commit.wait()
                raise RuntimeError("plugin delete failed")

        return Transaction()


class DurableDeleteMutation(NodeLifecycleTransaction):
    def __init__(
        self,
        behavior: "DurableDeleteBehavior",
        node_id: str,
        before: int,
        commit_before_node_ids: frozenset[str],
    ) -> None:
        self.behavior = behavior
        self.node_id = node_id
        self.before = before
        self.commit_before_node_ids = commit_before_node_ids

    @property
    def delete_recovery_payload(self) -> Mapping[str, Any]:
        return {"before": self.before}

    async def commit(self) -> None:
        assert self.behavior.services is not None
        with self.behavior.services.database.locked() as connection:
            row = connection.execute(
                """
                SELECT commit_state FROM pending_node_deletions
                WHERE node_id = ?
                """,
                (self.node_id,),
            ).fetchone()
        assert row is not None and row["commit_state"] == "started"
        self.behavior.commit_saw_durable_intent.append(self.node_id)
        self.behavior.values.pop(self.node_id, None)

    async def rollback(self, error: BaseException) -> None:
        del error
        self.behavior.values[self.node_id] = self.before
        self.behavior.rollback_order.append(self.node_id)


class DurableDeleteBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.services: Any | None = None
        self.values: dict[str, int] = {}
        self.dependencies: dict[str, frozenset[str]] = {}
        self.commit_saw_durable_intent: list[str] = []
        self.rollback_order: list[str] = []
        self.recovery_payloads: list[tuple[str, int]] = []

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        del context
        return DurableDeleteMutation(
            self,
            node.id,
            int(node.config.get("value", 0)),
            self.dependencies.get(node.id, frozenset()),
        )

    async def prepare_delete_recovery(
        self,
        context: NodeLifecycleContext,
        node: Card,
        *,
        plugin_version: str,
        payload: Mapping[str, Any],
    ) -> NodeLifecycleTransaction:
        del context, plugin_version
        before = payload.get("before")
        assert isinstance(before, int)
        self.recovery_payloads.append((node.id, before))
        return DurableDeleteMutation(
            self,
            node.id,
            before,
            self.dependencies.get(node.id, frozenset()),
        )


class HangingDeleteMutation(NodeLifecycleTransaction):
    def __init__(self, behavior: "HangingDeleteBehavior", node_id: str) -> None:
        self.behavior = behavior
        self.node_id = node_id

    @property
    def has_delete_finalizer(self) -> bool:
        return True

    async def rollback(self, error: BaseException) -> None:
        del error
        self.behavior.rollback_entered.set()
        await self.behavior.release.wait()

    async def finalize(self) -> None:
        self.behavior.finalize_entered.set()
        await self.behavior.release.wait()


class HangingDeleteBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.rollback_entered = asyncio.Event()
        self.finalize_entered = asyncio.Event()
        self.startup_calls: list[str] = []

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        del context
        self.startup_calls.append(node.id)

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        del context
        return HangingDeleteMutation(self, node.id)


class SelectiveFinalizeMutation(NodeLifecycleTransaction):
    def __init__(
        self, behavior: "SelectiveFinalizeBehavior", node_id: str
    ) -> None:
        self.behavior = behavior
        self.node_id = node_id

    @property
    def has_delete_finalizer(self) -> bool:
        return True

    async def finalize(self) -> None:
        if self.node_id in self.behavior.hanging_node_ids:
            await self.behavior.release.wait()
        self.behavior.finalized.append(self.node_id)


class SelectiveFinalizeBehavior(NodeLifecycleHandler):
    def __init__(self, hanging_node_ids: set[str]) -> None:
        self.hanging_node_ids = hanging_node_ids
        self.release = asyncio.Event()
        self.finalized: list[str] = []

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        del context
        return SelectiveFinalizeMutation(self, node.id)


class FailingCreateBehavior(NodeLifecycleHandler):
    def __init__(self) -> None:
        self.active: set[str] = set()
        self.rolled_back: list[str] = []

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del context, request
        behavior = self

        class FailingMutation(NodeLifecycleTransaction):
            async def commit(self) -> None:
                behavior.active.add(node.id)
                raise RuntimeError("plugin runtime creation failed")

            async def rollback(self, error: BaseException) -> None:
                assert str(error) == "plugin runtime creation failed"
                behavior.active.discard(node.id)
                behavior.rolled_back.append(node.id)

        return FailingMutation()


@pytest.mark.asyncio
async def test_custom_plugin_lifecycle_dispatches_without_core_changes(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.executable", behavior)
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
async def test_startup_rolls_back_staged_delete_when_graph_never_committed(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.crash-rollback", behavior)
    settings = Settings.for_data_root(tmp_path / "managed")
    first = create_services(settings, plugins=registry)
    try:
        node = await first.create_card(CardCreate(
            id="staged-delete", type="example.crash-rollback", config={"value": 9}
        ))
        lifecycle = registry.node_type(node.type).lifecycle
        assert lifecycle is not None
        transaction = await lifecycle.prepare_delete(
            first._node_lifecycle_context(), node
        )
        first._stage_pending_node_deletions(
            "simulated-crash", [(node, transaction)], ()
        )
        first._set_pending_node_deletion_state(node.id, "started")
        await transaction.commit()
        first._set_pending_node_deletion_state(node.id, "committed")
        assert node.id not in behavior.values
    finally:
        first.close()

    recovered = create_services(settings, plugins=registry)
    try:
        await recovered.startup()
        assert behavior.values[node.id] == 9
        assert ("rollback_delete", node.id) in behavior.calls
        with recovered.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0] == 0
    finally:
        await recovered.shutdown()
        recovered.close()


@pytest.mark.asyncio
async def test_delete_journals_intent_before_plugin_commit(tmp_path: Path) -> None:
    behavior = DurableDeleteBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.durable-delete", behavior)
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    behavior.services = services
    try:
        node = await services.create_card(CardCreate(
            id="durable-intent",
            type="example.durable-delete",
            config={"value": 4},
        ))
        behavior.values[node.id] = 4

        await services.delete_card(node.id)

        assert behavior.commit_saw_durable_intent == [node.id]
        assert services.world.maybe_get_card(node.id) is None
        with services.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0] == 0
    finally:
        services.close()


@pytest.mark.asyncio
async def test_startup_rolls_back_live_delete_batch_in_reverse_dependency_order(
    tmp_path: Path,
) -> None:
    behavior = DurableDeleteBehavior()
    registry = create_builtin_registry()
    node_type = "example.durable-batch-delete"
    _install_node(registry, node_type, behavior)
    settings = Settings.for_data_root(tmp_path / "managed")
    first = create_services(settings, plugins=registry)
    behavior.services = first
    try:
        first_node = await first.create_card(CardCreate(
            id="delete-first", type=node_type, config={"value": 11}
        ))
        second_node = await first.create_card(CardCreate(
            id="delete-second", type=node_type, config={"value": 22}
        ))
        behavior.values = {first_node.id: 11, second_node.id: 22}
        behavior.dependencies[first_node.id] = frozenset({second_node.id})
        lifecycle = registry.node_type(node_type).lifecycle
        assert lifecycle is not None
        transactions = [
            await lifecycle.prepare_delete(first._node_lifecycle_context(), node)
            for node in (second_node, first_node)
        ]
        ordered = first._order_delete_transactions(
            [second_node, first_node], transactions
        )
        assert [node.id for node, _ in ordered] == [first_node.id, second_node.id]
        first._stage_pending_node_deletions("crashed-batch", ordered, ())
        for node, transaction in ordered:
            first._set_pending_node_deletion_state(node.id, "started")
            await transaction.commit()
            first._set_pending_node_deletion_state(node.id, "committed")
        assert behavior.values == {}
    finally:
        first.close()

    recovered = create_services(settings, plugins=registry)
    behavior.services = recovered
    try:
        await recovered.startup()

        assert behavior.rollback_order == [second_node.id, first_node.id]
        assert behavior.recovery_payloads == [
            (second_node.id, 22),
            (first_node.id, 11),
        ]
        assert behavior.values == {first_node.id: 11, second_node.id: 22}
        with recovered.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0] == 0
    finally:
        await recovered.shutdown()
        recovered.close()


@pytest.mark.asyncio
async def test_plugin_api_1_0_live_delete_debt_fails_startup_closed(
    tmp_path: Path,
) -> None:
    behavior = DurableDeleteBehavior()
    registry = create_builtin_registry()
    node_type = "example.legacy-delete"
    _install_node(registry, node_type, behavior, plugin_api_version="1.0")
    settings = Settings.for_data_root(tmp_path / "managed")
    first = create_services(settings, plugins=registry)
    behavior.services = first
    try:
        node = await first.create_card(CardCreate(
            id="legacy-live-debt", type=node_type, config={"value": 8}
        ))
        lifecycle = registry.node_type(node_type).lifecycle
        assert lifecycle is not None
        transaction = await lifecycle.prepare_delete(
            first._node_lifecycle_context(), node
        )
        first._stage_pending_node_deletions(
            "legacy-live-batch", [(node, transaction)], ()
        )
        first._set_pending_node_deletion_state(node.id, "started")
        await transaction.commit()
        first._set_pending_node_deletion_state(node.id, "committed")
    finally:
        first.close()

    recovered = create_services(settings, plugins=registry)
    behavior.services = recovered
    try:
        with pytest.raises(
            PluginCompatibilityError,
            match="cannot restore live node 'legacy-live-debt'",
        ):
            await recovered.startup()
        assert behavior.rollback_order == []
        with recovered.database.locked() as connection:
            row = connection.execute(
                """
                SELECT plugin_api_version FROM pending_node_deletions
                WHERE node_id = ?
                """,
                (node.id,),
            ).fetchone()
        assert row is not None and row["plugin_api_version"] == "1.0"
    finally:
        recovered.close()


@pytest.mark.asyncio
async def test_hanging_delete_finalizer_does_not_hold_graph_or_block_startup(
    tmp_path: Path,
) -> None:
    behavior = HangingDeleteBehavior()
    registry = create_builtin_registry()
    node_type = "example.hanging-finalizer"
    _install_node(registry, node_type, behavior)
    settings = Settings.for_data_root(tmp_path / "managed")
    first = create_services(settings, plugins=registry)
    first._lifecycle_cleanup_timeout_seconds = 0.03
    try:
        deleted_node = await first.create_card(CardCreate(
            id="hanging-delete", type=node_type
        ))
        await first.create_card(CardCreate(id="startup-survivor", type=node_type))
        deletion = asyncio.create_task(first.delete_card(deleted_node.id))
        await asyncio.wait_for(behavior.finalize_entered.wait(), timeout=0.2)

        snapshot = await asyncio.wait_for(first.snapshot(), timeout=0.2)
        assert deleted_node.id not in {node.id for node in snapshot.nodes}
        await asyncio.wait_for(deletion, timeout=0.2)
        with first.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0] == 1
    finally:
        first.close()

    behavior.finalize_entered.clear()
    recovered = create_services(settings, plugins=registry)
    recovered._lifecycle_cleanup_timeout_seconds = 0.03
    recovered._lifecycle_startup_cleanup_budget_seconds = 0.05
    try:
        await asyncio.wait_for(recovered.startup(), timeout=0.2)
        assert behavior.finalize_entered.is_set()
        assert behavior.startup_calls == ["startup-survivor"]
        with recovered.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0] == 1
    finally:
        behavior.release.set()
        await recovered.shutdown()
        recovered.close()


@pytest.mark.asyncio
async def test_hanging_deleted_batch_does_not_starve_independent_cleanup(
    tmp_path: Path,
) -> None:
    node_type = "example.fair-finalizer"
    behavior = SelectiveFinalizeBehavior({"cleanup-hangs"})
    registry = create_builtin_registry()
    _install_node(registry, node_type, behavior)
    settings = Settings.for_data_root(tmp_path / "managed")
    first = create_services(settings, plugins=registry)
    try:
        nodes = [
            await first.create_card(CardCreate(id=node_id, type=node_type))
            for node_id in ("cleanup-hangs", "cleanup-finishes")
        ]
        lifecycle = registry.node_type(node_type).lifecycle
        assert lifecycle is not None
        for index, node in enumerate(nodes):
            transaction = await lifecycle.prepare_delete(
                first._node_lifecycle_context(), node
            )
            first._stage_pending_node_deletions(
                f"deleted-batch-{index}", [(node, transaction)], ()
            )
            first._set_pending_node_deletion_state(node.id, "started")
            await transaction.commit()
            first._set_pending_node_deletion_state(node.id, "committed")
            first.world.delete_card(node.id)
    finally:
        first.close()

    recovered = create_services(settings, plugins=registry)
    recovered._lifecycle_cleanup_timeout_seconds = 0.03
    recovered._lifecycle_startup_cleanup_budget_seconds = 0.1
    try:
        await asyncio.wait_for(recovered.startup(), timeout=0.2)
        assert behavior.finalized == ["cleanup-finishes"]
        with recovered.database.locked() as connection:
            pending_ids = {
                row["node_id"]
                for row in connection.execute(
                    "SELECT node_id FROM pending_node_deletions"
                ).fetchall()
            }
        assert pending_ids == {"cleanup-hangs"}
    finally:
        behavior.release.set()
        await recovered.shutdown()
        recovered.close()


@pytest.mark.asyncio
async def test_live_delete_recovery_failure_blocks_startup(tmp_path: Path) -> None:
    behavior = HangingDeleteBehavior()
    registry = create_builtin_registry()
    node_type = "example.hanging-rollback"
    _install_node(registry, node_type, behavior)
    settings = Settings.for_data_root(tmp_path / "managed")
    first = create_services(settings, plugins=registry)
    try:
        node = await first.create_card(CardCreate(
            id="live-delete-debt", type=node_type
        ))
        lifecycle = registry.node_type(node_type).lifecycle
        assert lifecycle is not None
        transaction = await lifecycle.prepare_delete(
            first._node_lifecycle_context(), node
        )
        first._stage_pending_node_deletions(
            "live-delete-crash", [(node, transaction)], ()
        )
        first._set_pending_node_deletion_state(node.id, "started")
        await transaction.commit()
        first._set_pending_node_deletion_state(node.id, "committed")
    finally:
        first.close()

    recovered = create_services(settings, plugins=registry)
    recovered._lifecycle_cleanup_timeout_seconds = 0.03
    recovered._lifecycle_startup_cleanup_budget_seconds = 0.05
    try:
        with pytest.raises(
            PluginCompatibilityError,
            match="cannot restore live node 'live-delete-debt'",
        ):
            await asyncio.wait_for(recovered.startup(), timeout=0.2)
        assert behavior.rollback_entered.is_set()
        assert behavior.startup_calls == []
        assert recovered.world.get_card(node.id).revision == node.revision
        with recovered.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0] == 1
    finally:
        behavior.release.set()
        recovered.close()


@pytest.mark.asyncio
async def test_failed_live_delete_compensation_blocks_further_mutations(
    tmp_path: Path,
) -> None:
    class FailingRollbackBehavior(NodeLifecycleHandler):
        async def prepare_delete(
            self, context: NodeLifecycleContext, node: Card
        ) -> NodeLifecycleTransaction:
            del context, node

            class Transaction(NodeLifecycleTransaction):
                async def commit(self) -> None:
                    raise RuntimeError("delete commit failed")

                async def rollback(self, error: BaseException) -> None:
                    del error
                    raise RuntimeError("delete rollback failed")

            return Transaction()

    registry = create_builtin_registry()
    node_type = "example.failed-delete-rollback"
    _install_node(registry, node_type, FailingRollbackBehavior())
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        node = await services.create_card(CardCreate(id="quarantined", type=node_type))
        with pytest.raises(RuntimeError, match="delete commit failed"):
            await services.delete_card(node.id)

        assert services.world.get_card(node.id).revision == node.revision
        with pytest.raises(ConflictError, match="world mutations are blocked"):
            await services.update_card(node.id, CardPatch(name="must not change"))
        assert services.world.get_card(node.id).name == node.name
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failed_plugin_creation_runs_rollback_and_removes_persisted_node(
    tmp_path: Path,
) -> None:
    behavior = FailingCreateBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.failing", behavior)
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
async def test_lifecycle_failures_compensate_update_and_delete(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.compensated", behavior)
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        node = await services.create_card(CardCreate(
            id="compensated-node", type="example.compensated", config={"value": 3}
        ))

        behavior.fail_on = "update"
        with pytest.raises(RuntimeError, match="plugin update failed"):
            await services.update_card(node.id, CardPatch(config={"value": 8}))
        assert services.get_card(node.id).config["value"] == 3
        assert behavior.values[node.id] == 3
        assert behavior.calls[-2:] == [
            ("update", node.id),
            ("rollback_update", node.id),
        ]

        behavior.fail_on = "delete"
        with pytest.raises(RuntimeError, match="plugin delete failed"):
            await services.delete_card(node.id)
        assert services.get_card(node.id).id == node.id
        assert behavior.values[node.id] == 3
        assert behavior.calls[-2:] == [
            ("delete", node.id),
            ("rollback_delete", node.id),
        ]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_batch_update_rolls_back_only_attempted_plugin_transactions(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.batch-update", behavior)
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        nodes = [
            await services.create_card(CardCreate(
                id=f"batch-node-{index}",
                type="example.batch-update",
                config={"value": index},
            ))
            for index in range(1, 4)
        ]
        behavior.calls.clear()
        behavior.fail_on = "update"
        behavior.fail_node_id = nodes[1].id
        updates = [
            CardBatchPatch(
                node_id=node.id,
                patch=CardPatch(config={"value": index + 10}),
            )
            for index, node in enumerate(nodes, start=1)
        ]

        with pytest.raises(RuntimeError, match="plugin update failed"):
            await services.update_cards(updates)

        assert [services.get_card(node.id).config["value"] for node in nodes] == [1, 2, 3]
        assert behavior.values == {
            nodes[0].id: 1,
            nodes[1].id: 2,
            nodes[2].id: 3,
        }
        assert behavior.calls == [
            ("update", nodes[0].id),
            ("update", nodes[1].id),
            ("rollback_update", nodes[1].id),
            ("rollback_update", nodes[0].id),
        ]

        behavior.calls.clear()
        behavior.fail_on = None
        updated = await services.update_cards(updates)
        assert [node.config["value"] for node in updated] == [11, 12, 13]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failed_batch_delete_preserves_agent_state_until_commit(
    tmp_path: Path,
) -> None:
    behavior = BlockingFailingDeleteBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.delete-failure", behavior)
    runtime = BlockingAgentRuntime()
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=registry,
        runtime_providers={"test.runtime": runtime},
        default_runtime_provider_id="test.runtime",
    )
    deletion: asyncio.Task[list[Card]] | None = None
    try:
        agent = await services.create_card(
            CardCreate(
                id="stateful-agent",
                type="agent",
                config={"max_concurrent_runs": 2},
            )
        )
        failing = await services.create_card(
            CardCreate(
                id="delete-failure",
                type="example.delete-failure",
                config={"value": 7},
            )
        )
        agent_scope = services.state.get_scope("agent", agent.id)
        services.state.set(agent_scope, "memory", {"kept": True})
        manager = services._require_run_manager()
        resumable = await manager.start_run(agent.id, "wait for resume")
        assert (
            await manager.wait_execution(resumable.run_id)
        ).status is RunStatus.WAITING
        await manager.suspend_run(
            resumable.run_id,
            reason="external work",
            release_agent_slot=True,
        )
        run = await manager.start_run(agent.id, "keep working")
        await runtime.started.wait()
        assert manager.get_run(run.run_id).status is RunStatus.RUNNING

        deletion = asyncio.create_task(
            services.delete_cards([agent.id, failing.id])
        )
        await behavior.commit_entered.wait()
        with pytest.raises(RuntimeUnavailableError, match="being deleted"):
            await manager.start_run(agent.id, "must not slip through")
        with pytest.raises(RuntimeUnavailableError, match="being deleted"):
            await manager.transition_run(resumable.run_id, RunStatus.RUNNING)
        behavior.release_commit.set()
        with pytest.raises(RuntimeError, match="plugin delete failed"):
            await deletion

        restored_scope = services.state.get_scope("agent", agent.id)
        assert services.state.get(restored_scope, "memory") == {"kept": True}
        assert services.world.get_card(agent.id).id == agent.id
        assert manager.get_run(run.run_id).status is RunStatus.RUNNING
        assert manager.holds_agent_slot(run.run_id)
        assert runtime.stopped_runs == []
        assert (await runtime.get_agent(agent.id)).config.agent_id == agent.id

        resumed = await manager.transition_run(resumable.run_id, RunStatus.RUNNING)
        assert resumed.status is RunStatus.RUNNING
        assert manager.holds_agent_slot(resumable.run_id)
        await services.delete_card(agent.id)
        await manager.wait_execution(run.run_id)
        assert manager.get_run(resumable.run_id).status is RunStatus.CANCELLED
        assert manager.get_run(run.run_id).status is RunStatus.CANCELLED
        assert not manager.holds_agent_slot(run.run_id)
        assert not manager.holds_agent_slot(resumable.run_id)
        assert set(runtime.stopped_runs) == {resumable.run_id, run.run_id}
        with pytest.raises(AgentNotFoundError):
            await runtime.get_agent(agent.id)
    finally:
        behavior.release_commit.set()
        if deletion is not None and not deletion.done():
            deletion.cancel()
            await asyncio.gather(deletion, return_exceptions=True)
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_persistence_failures_compensate_plugin_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, f"example.persistence-{operation}", behavior)
    services = create_services(
        Settings.for_data_root(tmp_path / operation), plugins=registry
    )
    node_id = f"persistence-{operation}"
    try:
        if operation == "create":
            monkeypatch.setattr(
                services.world,
                "create_card",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    OSError("create persistence failed")
                ),
            )
            with pytest.raises(OSError, match="create persistence failed"):
                await services.create_card(CardCreate(
                    id=node_id,
                    type=f"example.persistence-{operation}",
                    config={"value": 3},
                ))
            assert services.world.maybe_get_card(node_id) is None
            assert behavior.values == {}
            assert behavior.calls == []
            return

        await services.create_card(CardCreate(
            id=node_id,
            type=f"example.persistence-{operation}",
            config={"value": 3},
        ))
        behavior.calls.clear()
        method_name = "update_card" if operation == "update" else "delete_card"
        monkeypatch.setattr(
            services.world,
            method_name,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError(f"{operation} persistence failed")
            ),
        )
        with pytest.raises(OSError, match=f"{operation} persistence failed"):
            if operation == "update":
                await services.update_card(node_id, CardPatch(config={"value": 9}))
            else:
                await services.delete_card(node_id)

        assert services.get_card(node_id).config["value"] == 3
        assert behavior.values[node_id] == 3
        assert behavior.calls == [
            (operation, node_id),
            (f"rollback_{operation}", node_id),
        ]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_cancelled_lifecycle_commit_finishes_compensation(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    rolled_back = asyncio.Event()
    active: set[str] = set()

    class CancellationBehavior(NodeLifecycleHandler):
        async def prepare_create(
            self, context: NodeLifecycleContext, node: Card, request: CardCreate
        ) -> NodeLifecycleTransaction:
            del context, request

            class Mutation(NodeLifecycleTransaction):
                async def commit(self) -> None:
                    active.add(node.id)
                    started.set()
                    await asyncio.Event().wait()

                async def rollback(self, error: BaseException) -> None:
                    assert isinstance(error, asyncio.CancelledError)
                    active.discard(node.id)
                    rolled_back.set()

            return Mutation()

    registry = create_builtin_registry()
    _install_node(registry, "example.cancelled", CancellationBehavior())
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        task = asyncio.create_task(services.create_card(CardCreate(
            id="cancelled-node", type="example.cancelled"
        )))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rolled_back.is_set()
        assert active == set()
        assert services.world.maybe_get_card("cancelled-node") is None
    finally:
        services.close()


@pytest.mark.asyncio
async def test_builtin_resource_delete_restores_file_when_world_delete_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = create_services(Settings.for_data_root(tmp_path / "managed"))
    try:
        node = await services.create_card(CardCreate(
            id="durable-text",
            type="text",
            config={"filename": "durable.txt"},
            content="preserve me",
        ))
        record = services.resources.get_record(node.id)
        path = services.resources.resolve_relative_path(record.relative_path)
        monkeypatch.setattr(
            services.world,
            "delete_card",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("database delete failed")
            ),
        )

        with pytest.raises(OSError, match="database delete failed"):
            await services.delete_card(node.id)

        assert services.get_card(node.id).id == node.id
        assert services.resources.get_record(node.id).relative_path == record.relative_path
        assert path.read_text(encoding="utf-8") == "preserve me"
    finally:
        services.close()


@pytest.mark.asyncio
async def test_node_without_lifecycle_is_a_passive_persisted_object(
    tmp_path: Path,
) -> None:
    registry = create_builtin_registry()
    _install_node(registry, "example.passive", None)
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


class BlockingAgentRuntime(MockAgentRuntime):
    def __init__(self) -> None:
        super().__init__(EmptyCapabilityProvider())  # type: ignore[arg-type]
        self.started = asyncio.Event()
        self.stopped_runs: list[str] = []

    async def execute(self, config: Any, context: Any, runtime_input: Any) -> Any:
        del config, context
        if runtime_input.prompt == "wait for resume":
            return
        self.started.set()
        await asyncio.Event().wait()
        if False:  # pragma: no cover - marks this coroutine as an async generator
            yield None

    async def stop(self, run_id: str) -> None:
        self.stopped_runs.append(run_id)


class FlakyDeleteAgentRuntime(MockAgentRuntime):
    def __init__(self, failures: int) -> None:
        super().__init__(EmptyCapabilityProvider())  # type: ignore[arg-type]
        self.failures = failures
        self.delete_calls = 0

    async def delete_agent(self, agent_id: str) -> None:
        self.delete_calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("provider deletion is temporarily unavailable")
        await super().delete_agent(agent_id)


@pytest.mark.asyncio
async def test_failed_agent_finalization_keeps_admission_reserved_until_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = BlockingAgentRuntime()
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        runtime_providers={"test.runtime": runtime},
        default_runtime_provider_id="test.runtime",
    )
    try:
        agent = await services.create_card(CardCreate(id="cleanup-debt", type="agent"))
        manager = services._require_run_manager()
        run = await manager.start_run(agent.id, "wait for resume")
        assert (await manager.wait_execution(run.run_id)).status is RunStatus.WAITING
        assert manager.holds_agent_slot(run.run_id)

        manager_type = type(manager)
        original_cancel_agent_runs = manager_type.cancel_agent_runs
        cancellation_attempts = 0

        async def fail_twice_then_cancel(
            current_manager: Any, agent_id: str
        ) -> list[Any]:
            nonlocal cancellation_attempts
            cancellation_attempts += 1
            if cancellation_attempts <= 2:
                raise RuntimeError("Agent cancellation is temporarily unavailable")
            return await original_cancel_agent_runs(current_manager, agent_id)

        monkeypatch.setattr(
            manager_type, "cancel_agent_runs", fail_twice_then_cancel
        )
        await services.delete_card(agent.id)

        assert services.world.maybe_get_card(agent.id) is None
        assert cancellation_attempts == 2
        assert agent.id in manager._deleting_agents
        with pytest.raises(RuntimeUnavailableError, match="being deleted"):
            await manager.transition_run(run.run_id, RunStatus.RUNNING)
        with services.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions WHERE node_id = ?",
                (agent.id,),
            ).fetchone()[0] == 1

        await services._retry_pending_node_deletions()

        assert cancellation_attempts == 3
        assert agent.id not in manager._deleting_agents
        assert manager.get_run(run.run_id).status is RunStatus.CANCELLED
        with services.database.locked() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions WHERE node_id = ?",
                (agent.id,),
            ).fetchone()[0] == 0
        with pytest.raises(AgentNotFoundError):
            await runtime.get_agent(agent.id)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_committed_agent_delete_recovers_with_saved_provider_after_restart(
    tmp_path: Path,
) -> None:
    settings = Settings.for_data_root(tmp_path / "managed")
    provider_a = FlakyDeleteAgentRuntime(failures=2)
    provider_b = MockAgentRuntime(EmptyCapabilityProvider())  # type: ignore[arg-type]
    providers = {"provider.a": provider_a, "provider.b": provider_b}
    first = create_services(
        settings,
        runtime_providers=providers,
        default_runtime_provider_id="provider.a",
    )
    try:
        agent = await first.create_card(CardCreate(id="durable-delete", type="agent"))
        await first.delete_card(agent.id)
        assert provider_a.delete_calls == 2
        assert (await provider_a.get_agent(agent.id)).config.agent_id == agent.id
        with first.database.locked() as connection:
            pending = connection.execute(
                "SELECT cleanup_json FROM pending_node_deletions WHERE node_id = ?",
                (agent.id,),
            ).fetchone()
        assert pending is not None
        assert '"runtime_provider_id":"provider.a"' in pending["cleanup_json"]
    finally:
        first.close()

    recovered = create_services(
        settings,
        runtime_providers=providers,
        default_runtime_provider_id="provider.b",
    )
    try:
        await recovered.startup()
        assert provider_a.delete_calls == 3
        with pytest.raises(AgentNotFoundError):
            await provider_a.get_agent(agent.id)
        with recovered.database.locked() as connection:
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM pending_node_deletions"
            ).fetchone()[0]
        assert pending_count == 0
    finally:
        await recovered.shutdown()
        recovered.close()


class RecordingSandboxBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.states: dict[str, SandboxState] = {}
        self.calls: list[tuple[str, str]] = []
        self.mounts: set[tuple[str, str]] = set()

    async def create(self, sandbox_id: str) -> SandboxInfo:
        self.calls.append(("create", sandbox_id))
        self.states[sandbox_id] = SandboxState.READY
        return await self.get(sandbox_id)

    async def start(self, sandbox_id: str) -> SandboxInfo:
        await self.get(sandbox_id)
        self.calls.append(("start", sandbox_id))
        self.states[sandbox_id] = SandboxState.RUNNING
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
        self.mounts = {
            mount for mount in self.mounts if mount[0] != sandbox_id
        }

    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        await self.get(sandbox_id)
        self.calls.append(("detach", f"{sandbox_id}:{resource_id}"))
        self.mounts.discard((sandbox_id, resource_id))

    async def attach_resource(
        self,
        sandbox_id: str,
        resource_id: str,
        source: Path,
        relative_path: str,
        access: Any,
    ) -> None:
        del source, relative_path, access
        await self.get(sandbox_id)
        self.calls.append(("attach", f"{sandbox_id}:{resource_id}"))
        self.mounts.add((sandbox_id, resource_id))


class ActiveCommandSandboxBackend(RecordingSandboxBackend):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.command_started = asyncio.Event()
        self.command_release = asyncio.Event()
        self.command_done = asyncio.Event()

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
        command = tuple(str(item) for item in argv)
        self.calls.append(("execute", sandbox_id))
        self.states[sandbox_id] = SandboxState.RUNNING
        self.command_started.set()
        await self.command_release.wait()
        cancelled = self.states[sandbox_id] == SandboxState.STOPPED
        if not cancelled:
            self.states[sandbox_id] = SandboxState.READY
        self.command_done.set()
        return CommandResult(
            sandbox_id=sandbox_id,
            argv=command,
            exit_code=-1 if cancelled else 0,
            stdout="completed" if not cancelled else "",
            stderr="",
            duration_seconds=0.01,
            cancelled=cancelled,
        )

    async def terminate(self, sandbox_id: str) -> None:
        await self.get(sandbox_id)
        self.calls.append(("terminate", sandbox_id))
        self.states[sandbox_id] = SandboxState.STOPPED
        self.command_release.set()
        if self.command_started.is_set():
            await self.command_done.wait()


@pytest.mark.asyncio
async def test_failed_batch_delete_does_not_terminate_active_sandbox_command(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.delete-failure", behavior)
    sandbox = ActiveCommandSandboxBackend(tmp_path / "sandboxes")
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=registry,
        sandbox_backend=sandbox,  # type: ignore[arg-type]
    )
    command_task: asyncio.Task[CommandResult] | None = None
    try:
        sandbox_node = await services.create_card(CardCreate(
            id="active-sandbox", type="sandbox"
        ))
        failing = await services.create_card(CardCreate(
            id="delete-failure",
            type="example.delete-failure",
            config={"value": 7},
        ))
        command_task = asyncio.create_task(
            services.execute_sandbox(sandbox_node.id, ["long-command"])
        )
        await asyncio.wait_for(sandbox.command_started.wait(), timeout=0.2)
        behavior.fail_on = "delete"
        behavior.fail_node_id = failing.id

        with pytest.raises(RuntimeError, match="plugin delete failed"):
            await services.delete_cards([sandbox_node.id, failing.id])

        assert not command_task.done()
        assert sandbox.states[sandbox_node.id] == SandboxState.RUNNING
        assert ("terminate", sandbox_node.id) not in sandbox.calls
        assert ("start", sandbox_node.id) not in sandbox.calls
        assert services.world.get_card(sandbox_node.id).id == sandbox_node.id

        sandbox.command_release.set()
        result = await asyncio.wait_for(command_task, timeout=0.2)
        assert result.stdout == "completed"
        assert result.cancelled is False
        assert sandbox.states[sandbox_node.id] == SandboxState.READY
    finally:
        sandbox.command_release.set()
        if command_task is not None:
            await asyncio.gather(command_task, return_exceptions=True)
        services.close()


@pytest.mark.asyncio
async def test_failed_batch_delete_does_not_detach_active_sandbox_resource(
    tmp_path: Path,
) -> None:
    class MountAwareSandboxBackend(ActiveCommandSandboxBackend):
        async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
            if self.states.get(sandbox_id) is SandboxState.RUNNING:
                await self.terminate(sandbox_id)
            await super().detach_resource(sandbox_id, resource_id)

    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    node_type = "example.delete-after-resource"
    _install_node(registry, node_type, behavior)
    sandbox = MountAwareSandboxBackend(tmp_path / "sandboxes")
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=registry,
        sandbox_backend=sandbox,  # type: ignore[arg-type]
    )
    command_task: asyncio.Task[CommandResult] | None = None
    try:
        sandbox_node = await services.create_card(CardCreate(
            id="mounted-active-sandbox", type="sandbox"
        ))
        text = await services.create_card(CardCreate(
            id="mounted-active-text",
            type="text",
            config={"filename": "mounted.txt"},
            content="preserve me",
        ))
        failing = await services.create_card(CardCreate(
            id="delete-after-resource-failure",
            type=node_type,
            config={"value": 7},
        ))
        edge = await services.create_edge(EdgeCreate(
            source=text.id,
            target=sandbox_node.id,
            relationship="mount_read_only",
        ))
        command_task = asyncio.create_task(
            services.execute_sandbox(sandbox_node.id, ["long-command"])
        )
        await asyncio.wait_for(sandbox.command_started.wait(), timeout=0.2)
        sandbox.calls.clear()
        behavior.fail_on = "delete"
        behavior.fail_node_id = failing.id

        with pytest.raises(RuntimeError, match="plugin delete failed"):
            await services.delete_cards([text.id, failing.id])

        assert not command_task.done()
        assert sandbox.calls == []
        assert sandbox.states[sandbox_node.id] is SandboxState.RUNNING
        assert (sandbox_node.id, text.id) in sandbox.mounts
        assert services.resources.read_text(text.id).content == "preserve me"
        assert services.world.get_card(text.id).id == text.id
        assert services.world.get_edge(edge.id).id == edge.id

        sandbox.command_release.set()
        result = await asyncio.wait_for(command_task, timeout=0.2)
        assert result.stdout == "completed"
        assert result.cancelled is False
    finally:
        sandbox.command_release.set()
        if command_task is not None:
            await asyncio.gather(command_task, return_exceptions=True)
        services.close()


@pytest.mark.asyncio
async def test_batch_delete_honors_plugin_transaction_dependencies(
    tmp_path: Path,
) -> None:
    behavior = RecordingBehavior()
    registry = create_builtin_registry()
    _install_node(registry, "example.delete-after-mount", behavior)
    sandbox = RecordingSandboxBackend(tmp_path / "sandboxes")
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"),
        plugins=registry,
        sandbox_backend=sandbox,  # type: ignore[arg-type]
    )
    try:
        sandbox_node = await services.create_card(CardCreate(
            id="dependency-sandbox", type="sandbox"
        ))
        text = await services.create_card(CardCreate(
            id="dependency-text", type="text", content="preserve me"
        ))
        failing = await services.create_card(CardCreate(
            id="dependency-failure",
            type="example.delete-after-mount",
            config={"value": 7},
        ))
        await services.create_edge(EdgeCreate(
            source=text.id,
            target=sandbox_node.id,
            relationship="mount_read_only",
        ))
        behavior.fail_on = "delete"
        behavior.fail_node_id = failing.id

        with pytest.raises(RuntimeError, match="plugin delete failed"):
            await services.delete_cards([sandbox_node.id, text.id, failing.id])

        assert {node.id for node in services.world.list_cards()} >= {
            sandbox_node.id,
            text.id,
            failing.id,
        }
        assert sandbox_node.id in sandbox.states
        assert (sandbox_node.id, text.id) in sandbox.mounts
        assert services.resources.read_text(text.id).content == "preserve me"
        assert ("terminate", sandbox_node.id) not in sandbox.calls
        assert ("start", sandbox_node.id) not in sandbox.calls
    finally:
        services.close()


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
