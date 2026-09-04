from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Coroutine, Mapping
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, TypeVar
from uuid import uuid4

from backend.agents import (
    AgentNotFoundError,
    GoogleAdkAgentRuntime,
    RuntimeProvider,
)
from backend.capabilities.broker import CapabilityBroker
from backend.config import Settings
from backend.conversations import (
    ConversationAgent,
    ConversationMessage,
    ConversationParticipantsAdd,
    ConversationPost,
    ConversationPostResult,
    ConversationSession,
    ConversationSessionCreate,
    ConversationStore,
    ConversationSummary,
)
from backend.errors import (
    ConflictError,
    ConversationValidationError,
    GraphValidationError,
    NotFoundError,
    PermissionDeniedError,
    PluginCompatibilityError,
    ResourceValidationError,
    RevisionConflictError,
    RuntimeUnavailableError,
)
from backend.events.hub import EventHub
from backend.events.models import EventType, RuntimeEvent
from backend.persistence.database import Database
from backend.legions import (
    LegionBlueprint,
    LegionBounds,
    LegionCapture,
    LegionInstance,
    LegionInstantiate,
    LegionRecord,
    LegionStore,
    LegionSummary,
    LegionTemplateDependency,
    LegionTemplateEdge,
    LegionTemplateNode,
)
from backend.plugins import (
    NodeLifecycleContext,
    NodeLifecycleTransaction,
    PluginRegistry,
    NodeTemplateBinary,
    NodeTemplateCaptureContext,
    NodeTemplateDependency,
    NodeTemplateHandler,
    NodeTemplateRestoreContext,
    load_plugin_registry,
)
from backend.resources.manager import ManagedResourceStore
from backend.resources.models import (
    ImageImport,
    ResourceRecord,
    TextDocument,
    TextPatch,
    TextReplace,
)
from backend.runs import RunRecord, RunStatus, RunStore
from backend.runs.manager import RunManager
from backend.runs.models import TERMINAL_RUN_STATUSES
from backend.sandbox import (
    CommandResult,
    ResourceAccess,
    SandboxBackend,
    SandboxEvent,
    SandboxEventType,
    SandboxNotFoundError,
    WindowsSandboxBackend,
)
from backend.state import StateMutation, StateMutationKind, StateStore
from backend.world.models import (
    Card,
    CardBatchPatch,
    CardCreate,
    CardPatch,
    CardType,
    Edge,
    EdgeCreate,
    EdgeDirection,
    EdgePatch,
    Relationship,
    WorldSnapshot,
)
from backend.world.store import WorldStore


_SANDBOX_EVENT_TYPES = {
    SandboxEventType.STATE_CHANGED: EventType.SANDBOX_STATE_CHANGED,
    SandboxEventType.COMMAND_STARTED: EventType.SANDBOX_COMMAND_STARTED,
    SandboxEventType.STDOUT: EventType.STDOUT,
    SandboxEventType.STDERR: EventType.STDERR,
    SandboxEventType.COMMAND_FINISHED: EventType.COMMAND_FINISHED,
    SandboxEventType.RESOURCE_ATTACHED: EventType.SANDBOX_RESOURCE_ATTACHED,
    SandboxEventType.RESOURCE_DETACHED: EventType.SANDBOX_RESOURCE_DETACHED,
    SandboxEventType.RUNTIME_ERROR: EventType.RUNTIME_ERROR,
}

_conversation_turn_depth: ContextVar[int] = ContextVar(
    "conversation_turn_depth", default=0
)
_MAX_CONVERSATION_TURN_DEPTH = 4

logger = logging.getLogger(__name__)

_CommittedResult = TypeVar("_CommittedResult")


@dataclass(slots=True)
class _SandboxEventTransaction:
    """Share one formation event gate with inherited asyncio tasks.

    Context variables are copied into child tasks. Keeping the mutable state
    here means a late child can observe whether its formation eventually
    committed or rolled back instead of appending to an orphaned list.
    """

    events: list[SandboxEvent] = field(default_factory=list)
    state: str = "open"


class _PortableStateGate:
    """Let sandbox commands run together while capture gets a stable cut."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._executions = 0
        self._capture_active = False
        self._capture_waiters = 0

    @asynccontextmanager
    async def execution(self):
        async with self._condition:
            while self._capture_active or self._capture_waiters:
                await self._condition.wait()
            self._executions += 1
        try:
            yield
        finally:
            async with self._condition:
                self._executions -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def capture(self):
        async with self._condition:
            self._capture_waiters += 1
            try:
                while self._capture_active or self._executions:
                    await self._condition.wait()
                self._capture_active = True
            finally:
                self._capture_waiters -= 1
                self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                self._capture_active = False
                self._condition.notify_all()


@dataclass(frozen=True, slots=True)
class _LifecycleNodes:
    world: WorldStore

    def update_status(self, node_id: str, status: str) -> Card:
        return self.world.update_card(node_id, CardPatch(status=status))

    def list_edges_from(self, node_id: str) -> list[Edge]:
        return self.world.list_edges_from(node_id)


@dataclass(frozen=True, slots=True)
class _RecoveryLifecycleNodes:
    edges: tuple[Edge, ...]

    def update_status(self, node_id: str, status: str) -> Card:
        del node_id, status
        raise RuntimeError("delete finalization cannot mutate a deleted graph node")

    def list_edges_from(self, node_id: str) -> list[Edge]:
        return [edge for edge in self.edges if edge.source == node_id]


@dataclass(frozen=True, slots=True)
class _LifecycleAgents:
    manager: RunManager

    async def reserve_delete(self, node_id: str) -> None:
        await self.manager.reserve_agent_deletion(node_id)

    def release_delete(self, node_id: str) -> None:
        self.manager.release_agent_deletion(node_id)

    async def create(self, node: Card) -> None:
        await self.manager.register_agent(node)

    async def update(self, node: Card) -> None:
        await self.manager.update_agent(node)

    async def delete(
        self,
        node_id: str,
        *,
        missing_ok: bool = False,
        provider_id: str | None = None,
    ) -> None:
        try:
            await self.manager.delete_agent(
                node_id, missing_ok=missing_ok, provider_id=provider_id
            )
        except AgentNotFoundError:
            if not missing_ok:
                raise

    def provider_id(self, node: Card) -> str | None:
        return self.manager.provider_id_for_card(node)

    async def stop(self, node_id: str) -> None:
        await self.manager.cancel_agent_runs(node_id)


@dataclass(frozen=True, slots=True)
class _RecoveryLifecycleAgents:
    manager: RunManager
    card: Card

    async def reserve_delete(self, node_id: str) -> None:
        if node_id != self.card.id:
            raise RuntimeError("delete recovery targeted an unexpected Agent")
        await self.manager.reserve_agent_deletion(node_id)

    def release_delete(self, node_id: str) -> None:
        if node_id != self.card.id:
            raise RuntimeError("delete recovery targeted an unexpected Agent")
        self.manager.release_agent_deletion(node_id)

    async def create(self, node: Card) -> None:
        del node
        raise RuntimeError("delete finalization cannot create an Agent")

    async def update(self, node: Card) -> None:
        del node
        raise RuntimeError("delete finalization cannot update an Agent")

    async def delete(
        self,
        node_id: str,
        *,
        missing_ok: bool = False,
        provider_id: str | None = None,
    ) -> None:
        if node_id != self.card.id:
            raise RuntimeError("delete finalization targeted an unexpected Agent")
        await self.manager.delete_agent(
            node_id,
            missing_ok=missing_ok,
            provider_id=(
                provider_id
                if provider_id is not None
                else self.manager.provider_id_for_card(self.card)
            ),
        )

    def provider_id(self, node: Card) -> str | None:
        if node.id != self.card.id:
            raise RuntimeError("delete finalization targeted an unexpected Agent")
        return self.manager.provider_id_for_card(self.card)

    async def stop(self, node_id: str) -> None:
        await self.manager.cancel_agent_runs(node_id)


@dataclass(frozen=True, slots=True)
class _LifecycleSandboxes:
    backend: SandboxBackend
    resources: ManagedResourceStore

    async def ensure(self, node_id: str) -> str:
        try:
            await self.backend.get(node_id)
        except SandboxNotFoundError:
            await self.backend.create(node_id)
        return (await self.backend.get(node_id)).state.value

    async def create(self, node_id: str) -> None:
        await self.backend.create(node_id)

    async def start(self, node_id: str) -> None:
        await self.backend.start(node_id)

    async def destroy(self, node_id: str, *, missing_ok: bool = False) -> None:
        try:
            await self.backend.destroy(node_id)
        except SandboxNotFoundError:
            if not missing_ok:
                raise

    async def terminate(self, node_id: str, *, missing_ok: bool = False) -> None:
        try:
            await self.backend.terminate(node_id)
        except SandboxNotFoundError:
            if not missing_ok:
                raise

    async def detach_resource(
        self, node_id: str, resource_id: str, *, missing_ok: bool = False
    ) -> None:
        try:
            await self.backend.detach_resource(node_id, resource_id)
        except SandboxNotFoundError:
            if not missing_ok:
                raise

    async def attach_resource(
        self,
        node_id: str,
        resource_id: str,
        *,
        writable: bool,
        missing_ok: bool = False,
    ) -> None:
        try:
            await self.backend.get(node_id)
        except SandboxNotFoundError:
            if missing_ok:
                return
            raise
        record = self.resources.maybe_get_record(resource_id)
        if record is None:
            if missing_ok:
                return
            raise NotFoundError(f"resource {resource_id!r} does not exist")
        _, source = self.resources.read_bytes(resource_id)
        relative = str(PureWindowsPath("resources", record.filename))
        await self.backend.attach_resource(
            node_id,
            resource_id,
            source,
            relative,
            ResourceAccess.READ_WRITE if writable else ResourceAccess.READ_ONLY,
        )


@dataclass(slots=True)
class _PreparedResourceRemoval:
    path: Path

    def commit(self) -> None:
        self.path.unlink(missing_ok=True)

    def rollback(self) -> None:
        # Resource deletion is intentionally deferred to finalize, after the
        # graph commit, so there is nothing to restore during compensation.
        pass


@dataclass(frozen=True, slots=True)
class _LifecycleResources:
    resources: ManagedResourceStore

    def create_text(self, node_id: str, filename: str, content: str = "") -> None:
        self.resources.create_text(node_id, filename, content)

    def create_image(
        self, node_id: str, filename: str, media_type: str, data_base64: str
    ) -> None:
        self.resources.create_image(node_id, filename, media_type, data_base64)

    def remove_file(self, node_id: str) -> None:
        record = self.resources.maybe_get_record(node_id)
        if record is not None:
            self.resources.remove_file(record)

    def prepare_file_removal(self, node_id: str) -> _PreparedResourceRemoval | None:
        record = self.resources.maybe_get_record(node_id)
        if record is None:
            return None
        path = self.resources.resolve_relative_path(record.relative_path)
        return _PreparedResourceRemoval(path=path)


@dataclass(frozen=True, slots=True)
class _RecoveryLifecycleResources:
    resources: ManagedResourceStore
    record: ResourceRecord | None

    @staticmethod
    def _invalid() -> None:
        raise RuntimeError("delete finalization cannot create a managed resource")

    def create_text(self, node_id: str, filename: str, content: str = "") -> None:
        del node_id, filename, content
        self._invalid()

    def create_image(
        self, node_id: str, filename: str, media_type: str, data_base64: str
    ) -> None:
        del node_id, filename, media_type, data_base64
        self._invalid()

    def remove_file(self, node_id: str) -> None:
        if self.record is None or self.record.card_id != node_id:
            return
        self.resources.resolve_relative_path(self.record.relative_path).unlink(
            missing_ok=True
        )

    def prepare_file_removal(self, node_id: str) -> _PreparedResourceRemoval | None:
        if self.record is None or self.record.card_id != node_id:
            return None
        path = self.resources.resolve_relative_path(self.record.relative_path)
        return _PreparedResourceRemoval(path=path)


@dataclass(frozen=True, slots=True)
class _TemplateCaptureResources:
    resources: ManagedResourceStore

    def read_text(self, node_id: str) -> str:
        return self.resources.read_text(node_id).content

    def read_binary(self, node_id: str) -> NodeTemplateBinary | None:
        record = self.resources.maybe_get_record(node_id)
        if record is None:
            return None
        _, path = self.resources.read_bytes(node_id)
        return NodeTemplateBinary(
            filename=record.filename,
            media_type=record.media_type,
            data_base64=base64.b64encode(path.read_bytes()).decode("ascii"),
        )


@dataclass(frozen=True, slots=True)
class _TemplateRestoreResources:
    resources: ManagedResourceStore

    def replace_text(self, node_id: str, content: str) -> None:
        self.resources.replace_text(node_id, content)

    def create_image(
        self, node_id: str, filename: str, media_type: str, data_base64: str
    ) -> None:
        self.resources.create_image(node_id, filename, media_type, data_base64)

    def remove_file(self, node_id: str) -> None:
        record = self.resources.maybe_get_record(node_id)
        if record is not None:
            self.resources.remove_file(record)


@dataclass(frozen=True, slots=True)
class _LifecycleConversations:
    conversations: ConversationStore
    state: StateStore

    def create_initial_session(self, node_id: str, title: str) -> None:
        session = self.conversations.create_session(
            node_id, ConversationSessionCreate(title=title)
        )
        self.state.ensure_scope("session", session.id, schema_id="core.session")

    def delete_session_state(self, node_id: str) -> None:
        for session in self.conversations.list_sessions(node_id):
            self.state.delete_scope("session", session.id)


@dataclass(slots=True)
class ApplicationServices:
    settings: Settings
    database: Database
    world: WorldStore
    resources: ManagedResourceStore
    capabilities: CapabilityBroker
    events: EventHub
    plugins: PluginRegistry
    conversations: ConversationStore
    state: StateStore
    legions: LegionStore
    run_manager: RunManager | None = None
    sandbox_backend: SandboxBackend | None = None
    _node_mutation_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )
    _node_mutation_owner: ContextVar[asyncio.Task[Any] | None] = field(
        default_factory=lambda: ContextVar(
            f"node_mutation_owner_{uuid4()}", default=None
        ),
        init=False,
        repr=False,
    )
    _sandbox_event_transaction: ContextVar[_SandboxEventTransaction | None] = field(
        default_factory=lambda: ContextVar(
            f"sandbox_event_transaction_{uuid4()}", default=None
        ),
        init=False,
        repr=False,
    )
    _portable_state_gate: _PortableStateGate = field(
        default_factory=_PortableStateGate, init=False, repr=False
    )
    _lifecycle_cleanup_timeout_seconds: float = field(
        default=5.0, init=False, repr=False
    )
    _lifecycle_startup_cleanup_budget_seconds: float = field(
        default=30.0, init=False, repr=False
    )

    @asynccontextmanager
    async def _node_mutation(self, *, read_only: bool = False):
        current_task = asyncio.current_task()
        if current_task is not None and self._node_mutation_owner.get() is current_task:
            yield
            return
        async with self._node_mutation_lock:
            if not read_only:
                self._assert_no_live_pending_deletions()
            token = self._node_mutation_owner.set(current_task)
            try:
                yield
            finally:
                self._node_mutation_owner.reset(token)

    @staticmethod
    async def _complete_committed(
        operation: Coroutine[Any, Any, _CommittedResult],
    ) -> _CommittedResult:
        """Finish an irreversible post-commit tail despite caller cancellation.

        Once the authoritative world mutation has committed, surfacing
        ``CancelledError`` would falsely tell the caller that it did not happen.
        Tail operations must be idempotent because plugin cleanup may be
        retried after a transient failure.
        """

        task = asyncio.create_task(operation)
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done():
                    return task.result()
                continue

    @staticmethod
    def _consume_background_task(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    async def _run_bounded_lifecycle_cleanup(
        self,
        operation: Coroutine[Any, Any, _CommittedResult],
        *,
        timeout_seconds: float,
    ) -> _CommittedResult:
        """Bound a cooperative plugin cleanup without waiting on cancellation.

        A plugin task that ignores cancellation must not retain the global graph
        barrier or prevent application startup. Its durable debt remains and the
        idempotent cleanup can be retried on a later startup.
        """

        task = asyncio.create_task(operation)
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=max(0.0, timeout_seconds)
            )
        except BaseException:
            task.cancel()
            task.add_done_callback(self._consume_background_task)
            raise
        if task in done:
            return task.result()
        task.cancel()
        task.add_done_callback(self._consume_background_task)
        raise TimeoutError("plugin lifecycle cleanup exceeded its deadline")

    async def startup(self) -> None:
        manager = self._require_run_manager()
        await manager.startup()
        await self._retry_pending_node_deletions()
        context = self._node_lifecycle_context()
        for card in self.world.list_cards():
            lifecycle = self.plugins.node_type(card.type).lifecycle
            if lifecycle is not None:
                await lifecycle.on_startup(context, card)

    async def shutdown(self) -> None:
        context = self._node_lifecycle_context()
        for card in self.world.list_cards():
            lifecycle = self.plugins.node_type(card.type).lifecycle
            if lifecycle is not None:
                await lifecycle.on_shutdown(context, card)
        await self._require_run_manager().shutdown()

    def enrich_card(self, card: Card) -> Card:
        record = self.resources.maybe_get_record(card.id)
        if record is None:
            return card
        summary = self.resources.summary(card.id)
        config = {
            **card.config,
            "filename": summary.filename,
            "media_type": summary.media_type,
            "bytes": summary.size_bytes,
            "revision": summary.revision,
        }
        if summary.preview is not None:
            config["preview"] = summary.preview
        if summary.width is not None:
            config["image_width"] = summary.width
        if summary.height is not None:
            config["image_height"] = summary.height
        return card.model_copy(update={"resource": summary, "config": config})

    async def create_card(self, request: CardCreate) -> Card:
        return await self._create_card(request)

    async def _create_card(
        self,
        request: CardCreate,
        *,
        template_payload_version: int | None = None,
        template_payload: Mapping[str, Any] | None = None,
        template_node_ids: Mapping[str, str] | None = None,
        _creation_receipts: dict[str, tuple[NodeLifecycleTransaction, ...]] | None = None,
        _publish_event: bool = True,
    ) -> Card:
        async with self._node_mutation():
            if request.id is not None and self._has_pending_node_deletion(request.id):
                raise ConflictError(
                    f"node id {request.id!r} is reserved by pending lifecycle cleanup"
                )
            definition = self.plugins.node_type(request.type)
            lifecycle = definition.lifecycle
            context = self._node_lifecycle_context()
            preview = self.world.preview_card(request)
            transactions = [
                await lifecycle.prepare_create(context, preview, request)
                if lifecycle is not None
                else NodeLifecycleTransaction()
            ]
            if template_payload is not None:
                handler = definition.template_handler
                if handler is None:
                    raise PluginCompatibilityError(
                        f"node type {request.type!r} has no template sidecar handler"
                    )
                if (
                    template_payload_version is None
                    or not handler.supports_payload_version(template_payload_version)
                ):
                    raise PluginCompatibilityError(
                        f"node type {request.type!r} template payload version "
                        f"{template_payload_version!r} is not supported"
                    )
                handler.validate_payload(template_payload, template_payload_version)
                transactions.append(await handler.prepare_restore(
                    self._node_template_restore_context(),
                    preview,
                    template_payload,
                    template_payload_version,
                    template_node_ids or {},
                ))
            card: Card | None = None
            attempted_transactions: list[NodeLifecycleTransaction] = []
            try:
                card = self.world.create_card(request, card_id=preview.id)
                for transaction in transactions:
                    attempted_transactions.append(transaction)
                    await transaction.commit()
                card = self.enrich_card(self.world.get_card(card.id))
                if _publish_event:
                    await self._publish_card_created(card)
            except BaseException as error:
                cleanup_errors: list[BaseException] = []
                for transaction in reversed(attempted_transactions):
                    rollback_error = await self._rollback_lifecycle(transaction, error)
                    if rollback_error is not None:
                        cleanup_errors.append(rollback_error)
                if card is not None:
                    try:
                        self.world.delete_card(card.id)
                    except BaseException as cleanup_error:
                        cleanup_errors.append(cleanup_error)
                for cleanup_error in cleanup_errors:
                    error.add_note(
                        "node creation compensation also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise
            if _creation_receipts is not None:
                _creation_receipts[card.id] = tuple(transactions)
        return card

    async def _publish_card_created(self, card: Card) -> None:
        await self.events.publish(
            EventType.CARD_CREATED,
            node_id=card.id,
            payload={"node": card.model_dump(mode="json")},
        )

    def _publish_card_created_nowait(self, card: Card) -> None:
        self.events.publish_event_nowait(RuntimeEvent(
            type=EventType.CARD_CREATED,
            node_id=card.id,
            payload={"node": card.model_dump(mode="json")},
        ))

    @staticmethod
    async def _rollback_lifecycle(
        transaction: NodeLifecycleTransaction, error: BaseException
    ) -> BaseException | None:
        rollback_task = asyncio.create_task(transaction.rollback(error))
        while True:
            try:
                await asyncio.shield(rollback_task)
                return None
            except asyncio.CancelledError:
                if rollback_task.done():
                    try:
                        rollback_task.result()
                    except BaseException as cleanup_error:
                        return cleanup_error
                    return None
                continue
            except BaseException as cleanup_error:
                return cleanup_error

    def get_card(self, card_id: str) -> Card:
        return self.enrich_card(self.world.get_card(card_id))

    async def read_card(self, card_id: str) -> Card:
        """Read one public graph node without observing a partial formation."""

        async with self._node_mutation(read_only=True):
            return self.get_card(card_id)

    async def read_edges(self) -> list[Edge]:
        """Read public graph edges without observing a partial formation."""

        async with self._node_mutation(read_only=True):
            return self.world.list_edges()

    async def read_edge(self, edge_id: str) -> Edge:
        """Read one public graph edge without observing a partial formation."""

        async with self._node_mutation(read_only=True):
            return self.world.get_edge(edge_id)

    async def update_card(self, card_id: str, request: CardPatch) -> Card:
        async with self._node_mutation():
            current = self.world.get_card(card_id)
            updated = self.world.preview_update_card(card_id, request)
            lifecycle = self.plugins.node_type(current.type).lifecycle
            context = self._node_lifecycle_context()
            transaction = (
                await lifecycle.prepare_update(context, current, updated, request)
                if lifecycle is not None
                else NodeLifecycleTransaction()
            )
            try:
                await transaction.commit()
                card = self.enrich_card(self.world.update_card(card_id, request))
            except BaseException as error:
                rollback_error = await self._rollback_lifecycle(transaction, error)
                if rollback_error is not None:
                    error.add_note(
                        "node update compensation also failed: "
                        f"{type(rollback_error).__name__}: {rollback_error}"
                    )
                raise
            await self.events.publish(
                EventType.CARD_UPDATED,
                node_id=card.id,
                payload={"node": card.model_dump(mode="json")},
            )
        return card

    async def update_cards(self, updates: list[CardBatchPatch]) -> list[Card]:
        async with self._node_mutation():
            context = self._node_lifecycle_context()
            prepared: list[tuple[CardBatchPatch, NodeLifecycleTransaction]] = []
            for item in updates:
                current = self.world.get_card(item.node_id)
                updated = self.world.preview_update_card(item.node_id, item.patch)
                lifecycle = self.plugins.node_type(current.type).lifecycle
                transaction = (
                    await lifecycle.prepare_update(
                        context, current, updated, item.patch
                    )
                    if lifecycle is not None
                    else NodeLifecycleTransaction()
                )
                prepared.append((item, transaction))

            attempted: list[NodeLifecycleTransaction] = []
            try:
                for _, transaction in prepared:
                    attempted.append(transaction)
                    await transaction.commit()
                cards = [
                    self.enrich_card(card)
                    for card in self.world.update_cards(
                        [item for item, _ in prepared]
                    )
                ]
            except BaseException as error:
                for transaction in reversed(attempted):
                    rollback_error = await self._rollback_lifecycle(transaction, error)
                    if rollback_error is not None:
                        error.add_note(
                            "node batch update compensation also failed: "
                            f"{type(rollback_error).__name__}: {rollback_error}"
                        )
                raise
            for card in cards:
                await self.events.publish(
                    EventType.CARD_UPDATED,
                    node_id=card.id,
                    payload={"node": card.model_dump(mode="json")},
                )
        return cards

    async def delete_card(self, card_id: str) -> Card:
        return (await self.delete_cards([card_id]))[0]

    async def delete_cards(self, card_ids: list[str]) -> list[Card]:
        finish_committed_delete: Coroutine[Any, Any, None]
        async with self._node_mutation():
            ids = list(dict.fromkeys(card_ids))
            cards = [self.world.get_card(card_id) for card_id in ids]
            attached_edges: dict[str, Edge] = {}
            for card in cards:
                for edge in (
                    *self.world.list_edges_from(card.id),
                    *self.world.list_edges_to(card.id),
                ):
                    attached_edges[edge.id] = edge
            affected = {
                edge.id: self._affected_agents(edge) for edge in attached_edges.values()
            }
            context = self._node_lifecycle_context()
            transactions: list[NodeLifecycleTransaction] = []
            for card in cards:
                lifecycle = self.plugins.node_type(card.type).lifecycle
                transactions.append(
                    await lifecycle.prepare_delete(context, card)
                    if lifecycle is not None
                    else NodeLifecycleTransaction()
                )
            ordered = self._order_delete_transactions(cards, transactions)
            cleanup_batch_id = str(uuid4())
            # Persist the complete recovery snapshot before the first external
            # commit. Per-row intent markers then close the hard-crash window
            # between invoking a plugin and recording what must be compensated.
            self._stage_pending_node_deletions(
                cleanup_batch_id,
                ordered,
                tuple(attached_edges.values()),
            )
            attempted: list[tuple[Card, NodeLifecycleTransaction]] = []
            try:
                for transaction_card, transaction in ordered:
                    self._set_pending_node_deletion_state(
                        transaction_card.id, "started"
                    )
                    attempted.append((transaction_card, transaction))
                    await transaction.commit()
                    self._set_pending_node_deletion_state(
                        transaction_card.id, "committed"
                    )
                deleted = (
                    [self.world.delete_card(ids[0])]
                    if len(ids) == 1
                    else self.world.delete_cards(ids)
                )
            except BaseException as error:
                rollback_blocked = False
                attempted_ids = {card.id for card, _ in attempted}
                for transaction_card, transaction in reversed(attempted):
                    if rollback_blocked:
                        continue
                    rollback_error = await self._rollback_lifecycle(transaction, error)
                    if rollback_error is not None:
                        rollback_blocked = True
                        self._record_pending_node_deletion_error(
                            transaction_card.id, rollback_error
                        )
                        error.add_note(
                            "node deletion compensation also failed: "
                            f"{type(rollback_error).__name__}: {rollback_error}"
                        )
                    else:
                        self._complete_pending_node_deletion(transaction_card.id)
                # Transactions whose commit intent was never recorded have no
                # side effects to compensate and need no durable recovery row.
                for transaction_card, _ in ordered:
                    if transaction_card.id not in attempted_ids:
                        self._complete_pending_node_deletion(transaction_card.id)
                raise

            # The graph is authoritative once this synchronous section
            # completes. Publish its change before potentially slow plugin
            # cleanup and release the global mutation barrier first.
            for edge in attached_edges.values():
                self._publish_edge_change_nowait(
                    EventType.EDGE_DELETED,
                    edge,
                    affected_agents=affected[edge.id],
                )
            for card in deleted:
                self.events.publish_event_nowait(RuntimeEvent(
                    type=EventType.CARD_DELETED,
                    node_id=card.id,
                    payload={"node": card.model_dump(mode="json")},
                ))

            async def finalize_committed_delete() -> None:
                for transaction_card, transaction in ordered:
                    # A one-time provider/filesystem failure must not strand a
                    # successfully deleted node. The lifecycle contract makes
                    # finalization idempotent, so retry once before recording
                    # cleanup debt for operator attention.
                    for attempt in range(2):
                        try:
                            await self._run_bounded_lifecycle_cleanup(
                                transaction.finalize(),
                                timeout_seconds=self._lifecycle_cleanup_timeout_seconds,
                            )
                            self._complete_pending_node_deletion(transaction_card.id)
                            break
                        except BaseException as finalize_error:
                            if attempt == 1 or isinstance(finalize_error, TimeoutError):
                                try:
                                    self._record_pending_node_deletion_error(
                                        transaction_card.id, finalize_error
                                    )
                                except BaseException:
                                    logger.exception(
                                        "failed to record pending deletion error for %s",
                                        transaction_card.id,
                                    )
                                logger.error(
                                    "node deletion committed but plugin finalization failed",
                                    exc_info=(
                                        type(finalize_error),
                                        finalize_error,
                                        finalize_error.__traceback__,
                                    ),
                                )
                                # Preserve dependency order: do not finalize a
                                # successor while its predecessor remains debt.
                                return

            finish_committed_delete = finalize_committed_delete()

        # Cleanup is bounded and outside the graph barrier. Once graph deletion
        # commits, finish this short tail despite request cancellation so callers
        # cannot mistake a committed deletion for an aborted one.
        await self._complete_committed(finish_committed_delete)
        return deleted

    def _has_pending_node_deletion(self, node_id: str) -> bool:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT 1 FROM pending_node_deletions WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        return row is not None

    def _assert_no_live_pending_deletions(self) -> None:
        with self.database.locked() as connection:
            row = connection.execute(
                """
                SELECT pending.node_id
                FROM pending_node_deletions AS pending
                INNER JOIN cards ON cards.id = pending.node_id
                LIMIT 1
                """
            ).fetchone()
        if row is not None:
            raise ConflictError(
                "world mutations are blocked while interrupted deletion cleanup "
                f"for live node {row['node_id']!r} awaits recovery"
            )

    def _stage_pending_node_deletions(
        self,
        batch_id: str,
        ordered: list[tuple[Card, NodeLifecycleTransaction]],
        edges: tuple[Edge, ...],
    ) -> None:
        plugin_versions = {
            descriptor.id: (descriptor.version, descriptor.plugin_api_version)
            for descriptor in self.plugins.plugins()
        }
        outgoing_edges: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            outgoing_edges.setdefault(edge.source, []).append(
                edge.model_dump(mode="json")
            )
        with self.database.transaction(immediate=True) as connection:
            for sequence, (card, transaction) in enumerate(ordered):
                plugin_id = self.plugins.node_type_owner_id(card.type)
                resource = self.resources.maybe_get_record(card.id)
                edges_json = json.dumps(
                    outgoing_edges.get(card.id, []), separators=(",", ":")
                )
                connection.execute(
                    """
                    INSERT INTO pending_node_deletions (
                        node_id, batch_id, sequence, commit_state, plugin_id,
                        plugin_version, plugin_api_version, requires_finalize, cleanup_json,
                        card_json, edges_json, resource_json, created_at
                    ) VALUES (?, ?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        card.id,
                        batch_id,
                        sequence,
                        plugin_id,
                        plugin_versions[plugin_id][0],
                        plugin_versions[plugin_id][1],
                        int(transaction.has_delete_finalizer),
                        json.dumps(
                            dict(transaction.delete_recovery_payload),
                            separators=(",", ":"),
                        ),
                        card.model_dump_json(),
                        edges_json,
                        resource.model_dump_json() if resource is not None else None,
                    ),
                )

    def _set_pending_node_deletion_state(self, node_id: str, state: str) -> None:
        if state not in {"started", "committed"}:
            raise ValueError(f"invalid pending deletion state {state!r}")
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE pending_node_deletions
                SET commit_state = ?
                WHERE node_id = ?
                """,
                (state, node_id),
            )
            if cursor.rowcount != 1:
                raise ConflictError(
                    f"pending deletion recovery row for {node_id!r} is missing"
                )

    def _discard_pending_deletion_batch(self, batch_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM pending_node_deletions WHERE batch_id = ?",
                (batch_id,),
            )

    def _complete_pending_node_deletion(self, node_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM pending_node_deletions WHERE node_id = ?",
                (node_id,),
            )

    def _record_pending_node_deletion_error(
        self, node_id: str, error: BaseException
    ) -> None:
        detail = f"{type(error).__name__}: {error}"[:4000]
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE pending_node_deletions
                SET attempts = attempts + 1, last_error = ?
                WHERE node_id = ?
                """,
                (detail, node_id),
            )

    async def _prepare_pending_node_deletion_recovery(
        self,
        row: Any,
        *,
        node_still_exists: bool,
        timeout_seconds: float,
    ) -> NodeLifecycleTransaction:
        plugin_id = str(row["plugin_id"])
        if not self.plugins.has_plugin(plugin_id):
            raise PluginCompatibilityError(
                f"pending deletion requires missing plugin {plugin_id!r}"
            )
        card = Card.model_validate_json(str(row["card_json"]))
        owner_id = self.plugins.node_type_owner_id(card.type)
        if owner_id != plugin_id:
            raise PluginCompatibilityError(
                f"pending deletion for {card.type!r} belongs to plugin "
                f"{plugin_id!r}, not current owner {owner_id!r}"
            )
        if node_still_exists:
            current = self.world.get_card(card.id)
            if current.revision != card.revision:
                raise RevisionConflictError(
                    f"pending deletion rollback for {card.id!r} no longer "
                    "matches the live node revision"
                )
        lifecycle = self.plugins.node_type(card.type).lifecycle
        if lifecycle is None:
            if bool(row["requires_finalize"]):
                raise PluginCompatibilityError(
                    f"node type {card.type!r} no longer provides deletion cleanup"
                )
            return NodeLifecycleTransaction()
        current_plugin_version = next(
            descriptor.version
            for descriptor in self.plugins.plugins()
            if descriptor.id == plugin_id
        )
        saved_plugin_version = str(row["plugin_version"])
        if not lifecycle.supports_delete_recovery_version(
            saved_plugin_version, current_plugin_version
        ):
            raise PluginCompatibilityError(
                f"pending deletion was prepared by plugin {plugin_id!r} "
                f"version {saved_plugin_version!r}; installed version "
                f"{current_plugin_version!r} does not support that cleanup snapshot"
            )
        cleanup_payload = json.loads(str(row["cleanup_json"]))
        if not isinstance(cleanup_payload, dict):
            raise PluginCompatibilityError(
                "pending deletion cleanup payload is not an object"
            )
        edges = tuple(
            Edge.model_validate(item)
            for item in json.loads(str(row["edges_json"]))
        )
        raw_resource = row["resource_json"]
        resource = (
            ResourceRecord.model_validate_json(str(raw_resource))
            if raw_resource is not None
            else None
        )
        context = (
            self._node_lifecycle_context()
            if node_still_exists
            else self._recovery_node_lifecycle_context(card, edges, resource)
        )
        transaction = await self._run_bounded_lifecycle_cleanup(
            lifecycle.prepare_delete_recovery(
                context,
                card,
                plugin_version=saved_plugin_version,
                payload=cleanup_payload,
            ),
            timeout_seconds=timeout_seconds,
        )
        if bool(row["requires_finalize"]) and not transaction.has_delete_finalizer:
            raise PluginCompatibilityError(
                f"node type {card.type!r} no longer provides deletion cleanup"
            )
        return transaction

    async def _retry_pending_node_deletions(self) -> None:
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pending_node_deletions
                ORDER BY created_at, batch_id, sequence
                """
            ).fetchall()
        batches: dict[str, list[Any]] = {}
        for row in rows:
            batches.setdefault(str(row["batch_id"]), []).append(row)
        classified_batches: list[tuple[str, list[Any], bool]] = []
        for batch_id, batch_rows in batches.items():
            with self.database.locked() as connection:
                live_node_ids = {
                    str(row["node_id"])
                    for row in batch_rows
                    if connection.execute(
                        "SELECT 1 FROM cards WHERE id = ?", (str(row["node_id"]),)
                    ).fetchone() is not None
                }
            if live_node_ids and len(live_node_ids) != len(batch_rows):
                error = RuntimeError(
                    f"pending deletion batch {batch_id!r} has a partially deleted graph"
                )
                for row in batch_rows:
                    self._record_pending_node_deletion_error(str(row["node_id"]), error)
                logger.error("%s; cleanup remains fail-closed", error)
                raise PluginCompatibilityError(str(error)) from error

            graph_is_live = bool(live_node_ids)
            classified_batches.append((batch_id, batch_rows, graph_is_live))

        # A live graph can still expose half-applied plugin state, so its
        # rollback is a startup safety prerequisite. Best-effort finalization
        # for already-deleted graphs must never consume that recovery budget.
        classified_batches.sort(key=lambda item: (
            not item[2],
            0 if item[2] else sum(int(row["attempts"]) for row in item[1]),
        ))
        deadline = (
            asyncio.get_running_loop().time()
            + self._lifecycle_startup_cleanup_budget_seconds
        )

        for batch_id, batch_rows, graph_is_live in classified_batches:
            if not graph_is_live and any(
                str(row["commit_state"]) != "committed" for row in batch_rows
            ):
                error = RuntimeError(
                    f"pending deletion batch {batch_id!r} removed its graph before "
                    "all plugin commits were recorded"
                )
                for row in batch_rows:
                    self._record_pending_node_deletion_error(str(row["node_id"]), error)
                logger.error("%s; cleanup remains fail-closed", error)
                continue

            recovery_rows = list(reversed(batch_rows)) if graph_is_live else batch_rows
            for row in recovery_rows:
                node_id = str(row["node_id"])
                commit_state = str(row["commit_state"])
                try:
                    if graph_is_live and commit_state == "prepared":
                        self._complete_pending_node_deletion(node_id)
                        continue
                    if graph_is_live and commit_state in {"started", "committed"}:
                        try:
                            api_version = tuple(
                                int(part)
                                for part in str(row["plugin_api_version"]).split(".")
                            )
                        except ValueError as error:
                            raise PluginCompatibilityError(
                                "pending deletion records an invalid Plugin API version"
                            ) from error
                        if api_version < (1, 1):
                            raise PluginCompatibilityError(
                                f"plugin API {row['plugin_api_version']!r} does not "
                                "declare restart-safe delete rollback recovery"
                            )
                    if not graph_is_live and not bool(row["requires_finalize"]):
                        self._complete_pending_node_deletion(node_id)
                        continue
                    now = asyncio.get_running_loop().time()
                    remaining = deadline - now
                    if remaining <= 0:
                        if graph_is_live:
                            raise TimeoutError(
                                "live plugin deletion recovery exceeded its startup deadline"
                            )
                        return
                    row_deadline = min(
                        deadline, now + self._lifecycle_cleanup_timeout_seconds
                    )
                    transaction = await self._prepare_pending_node_deletion_recovery(
                        row,
                        node_still_exists=graph_is_live,
                        timeout_seconds=row_deadline - now,
                    )
                    remaining = row_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError(
                            "pending plugin deletion recovery exceeded its startup deadline"
                        )
                    operation = (
                        transaction.rollback(RuntimeError(
                            "process stopped before the staged node deletion committed"
                        ))
                        if graph_is_live
                        else transaction.finalize()
                    )
                    await self._run_bounded_lifecycle_cleanup(
                        operation, timeout_seconds=remaining
                    )
                    self._complete_pending_node_deletion(node_id)
                except asyncio.CancelledError:
                    raise
                except BaseException as error:
                    try:
                        self._record_pending_node_deletion_error(node_id, error)
                    except BaseException:
                        logger.exception(
                            "failed to record pending node deletion cleanup error for %s",
                            node_id,
                        )
                    logger.error(
                        "pending node deletion cleanup remains for %s",
                        node_id,
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    if graph_is_live:
                        raise PluginCompatibilityError(
                            f"cannot restore live node {node_id!r} after an "
                            "interrupted deletion"
                        ) from error
                    # Preserve topological order. A dependent recovery is not
                    # attempted while its predecessor remains incomplete.
                    break

    @staticmethod
    def _order_delete_transactions(
        cards: list[Card], transactions: list[NodeLifecycleTransaction]
    ) -> list[tuple[Card, NodeLifecycleTransaction]]:
        """Topologically order plugin-declared delete dependencies."""

        pairs = list(zip(cards, transactions, strict=True))
        by_id = {card.id: (card, transaction) for card, transaction in pairs}
        index = {card.id: position for position, (card, _) in enumerate(pairs)}
        successors: dict[str, set[str]] = {card.id: set() for card, _ in pairs}
        indegree = {card.id: 0 for card, _ in pairs}
        for card, transaction in pairs:
            for successor_id in transaction.commit_before_node_ids:
                if successor_id not in by_id or successor_id == card.id:
                    continue
                if successor_id not in successors[card.id]:
                    successors[card.id].add(successor_id)
                    indegree[successor_id] += 1

        ready = sorted(
            (node_id for node_id, degree in indegree.items() if degree == 0),
            key=index.__getitem__,
        )
        ordered_ids: list[str] = []
        while ready:
            node_id = ready.pop(0)
            ordered_ids.append(node_id)
            for successor_id in sorted(successors[node_id], key=index.__getitem__):
                indegree[successor_id] -= 1
                if indegree[successor_id] == 0:
                    ready.append(successor_id)
                    ready.sort(key=index.__getitem__)
        if len(ordered_ids) != len(pairs):
            raise PluginCompatibilityError(
                "node lifecycle delete dependencies contain a cycle"
            )
        return [by_id[node_id] for node_id in ordered_ids]

    async def snapshot(
        self, chunks: list[tuple[int, int]] | None = None
    ) -> WorldSnapshot:
        async with self._node_mutation(read_only=True):
            cards = self.world.list_cards(chunks)
            enriched = [self.enrich_card(card) for card in cards]
            card_ids = [card.id for card in cards] if chunks is not None else None
            edges = (
                self.world.list_incident_edges(card_ids)
                if card_ids is not None
                else self.world.list_edges()
            )
            loaded_chunks = (
                sorted(set(chunks))
                if chunks is not None
                else sorted({card.chunk for card in cards})
            )
            return WorldSnapshot(
                nodes=enriched,
                edges=edges,
                chunks=loaded_chunks,
                chunk_size=self.world.chunk_size,
            )

    async def capture_legion(self, request: LegionCapture) -> LegionSummary:
        # Commands may mutate read-write hard links before their resource
        # revisions are refreshed. Captures take the exclusive side of this
        # gate; command execution is shared and does not freeze graph CRUD.
        async with self._portable_state_gate.capture():
            async with self._node_mutation():
                return await self._capture_legion_locked(request)

    async def _capture_legion_locked(self, request: LegionCapture) -> LegionSummary:
        cards = [self.world.get_card(node_id) for node_id in request.node_ids]
        edges = self.world.list_edges(request.node_ids)
        card_revisions = {card.id: card.revision for card in cards}
        edge_revisions = {edge.id: edge.revision for edge in edges}
        resource_revisions = {
            card.id: (
                record.revision
                if (record := self.resources.maybe_get_record(card.id)) is not None
                else None
            )
            for card in cards
        }
        node_keys = {
            card.id: f"node-{index + 1}" for index, card in enumerate(cards)
        }
        for card in cards:
            definition = self.plugins.node_type(card.type)
            if not definition.templateable:
                raise PluginCompatibilityError(
                    f"node type {card.type!r} does not support Legion templates"
                )
        for edge in edges:
            definition = self.plugins.relationship(edge.relationship)
            if not definition.templateable:
                raise PluginCompatibilityError(
                    f"relationship {edge.relationship!r} does not support Legion templates"
                )

        min_x = min(card.position.x for card in cards)
        min_y = min(card.position.y for card in cards)
        max_x = max(card.position.x + card.size.width for card in cards)
        max_y = max(card.position.y + card.size.height for card in cards)
        template_nodes: list[LegionTemplateNode] = []
        captured_bytes = 0
        template_context = self._node_template_capture_context()
        for card in cards:
            definition = self.plugins.node_type(card.type)
            status = definition.template_status or card.status
            config = (
                definition.template_handler.capture_config(card)
                if definition.template_handler is not None
                else dict(card.config)
            )
            if not isinstance(config, dict):
                raise PluginCompatibilityError(
                    f"node type {card.type!r} template handler returned a "
                    "non-object configuration"
                )
            if definition.template_status is not None:
                config["status"] = definition.template_status
            config = self.plugins.validate_config(card.type, config)
            dependencies: list[LegionTemplateDependency] = []
            if definition.template_handler is not None:
                for dependency in self._node_template_dependencies(
                    definition.template_handler, config
                ):
                    try:
                        owner = self.plugins.owner_id(dependency.kind, dependency.id)
                    except ValueError as error:
                        raise PluginCompatibilityError(
                            f"node type {card.type!r} requires unattributed template "
                            f"dependency {dependency.kind.replace('_', ' ')} "
                            f"{dependency.id!r}"
                        ) from error
                    dependencies.append(LegionTemplateDependency(
                        kind=dependency.kind,
                        id=dependency.id,
                        plugin_id=owner,
                    ))
            payload: dict[str, Any] | None = None
            payload_version: int | None = None
            if definition.template_handler is not None:
                payload = await definition.template_handler.capture(
                    template_context, card, node_keys
                )
                if not isinstance(payload, dict):
                    raise PluginCompatibilityError(
                        f"node type {card.type!r} template handler returned a non-object payload"
                    )
                payload_version = definition.template_handler.payload_version
                definition.template_handler.validate_payload(payload, payload_version)
            template_node = LegionTemplateNode(
                key=node_keys[card.id],
                type=card.type,
                plugin_id=self.plugins.node_type_owner_id(card.type),
                name=card.name,
                position={"x": card.position.x - min_x, "y": card.position.y - min_y},
                size=card.size,
                expanded=card.expanded,
                status=status,
                config=config,
                dependencies=dependencies,
                payload_version=payload_version,
                payload=payload,
            )
            captured_bytes += len(template_node.model_dump_json().encode("utf-8"))
            if captured_bytes > self.legions.MAX_BLUEPRINT_BYTES:
                raise ResourceValidationError(
                    "Legion blueprint exceeds the 64 MiB portable-state limit"
                )
            template_nodes.append(template_node)

        # Capture handlers are deliberately read-only, so collect their
        # sidecars twice. This optimistic snapshot check catches native
        # sandbox writes to read-write mounts even before the command returns
        # and advances the managed-resource revision. It avoids freezing the
        # whole graph for the lifetime of a long sandbox command.
        for card, template_node in zip(cards, template_nodes, strict=True):
            handler = self.plugins.node_type(card.type).template_handler
            if handler is None:
                continue
            current_payload = await handler.capture(
                template_context, card, node_keys
            )
            if not isinstance(current_payload, dict):
                raise PluginCompatibilityError(
                    f"node type {card.type!r} template handler returned a non-object payload"
                )
            handler.validate_payload(current_payload, handler.payload_version)
            if current_payload != template_node.payload:
                raise RevisionConflictError(
                    "the world changed while the Legion was being captured; retry"
                )

        try:
            current_cards = [self.world.get_card(card.id) for card in cards]
        except NotFoundError as error:
            raise RevisionConflictError(
                "the world changed while the Legion was being captured; retry"
            ) from error
        current_edges = self.world.list_edges(request.node_ids)
        current_resource_revisions = {
            card.id: (
                record.revision
                if (record := self.resources.maybe_get_record(card.id)) is not None
                else None
            )
            for card in cards
        }
        if (
            {card.id: card.revision for card in current_cards} != card_revisions
            or {edge.id: edge.revision for edge in current_edges} != edge_revisions
            or current_resource_revisions != resource_revisions
        ):
            raise RevisionConflictError(
                "the world changed while the Legion was being captured; retry"
            )

        template_edges = [
            LegionTemplateEdge(
                key=f"edge-{index + 1}",
                source=node_keys[edge.source],
                target=node_keys[edge.target],
                relationship=edge.relationship,
                plugin_id=self.plugins.relationship_owner_id(edge.relationship),
                direction=edge.direction,
            )
            for index, edge in enumerate(edges)
        ]
        record = self.legions.create(
            request.name,
            request.description,
            LegionBlueprint(
                bounds=LegionBounds(width=max_x - min_x, height=max_y - min_y),
                nodes=template_nodes,
                edges=template_edges,
            ),
        )
        return self._legion_summary(record)

    def list_legions(self) -> list[LegionSummary]:
        return [self._legion_summary(record) for record in self.legions.list()]

    async def delete_legion(self, legion_id: str) -> LegionSummary:
        async with self._node_mutation():
            return self._legion_summary(self.legions.delete(legion_id))

    async def instantiate_legion(
        self, legion_id: str, request: LegionInstantiate
    ) -> LegionInstance:
        async with self._node_mutation():
            event_transaction = _SandboxEventTransaction()
            token = self._sandbox_event_transaction.set(event_transaction)
            try:
                instance = await self._instantiate_legion_locked(legion_id, request)
            except BaseException:
                event_transaction.state = "discarded"
                self._sandbox_event_transaction.reset(token)
                raise
            event_transaction.state = "committing"
            self._sandbox_event_transaction.reset(token)
            for node in instance.nodes:
                self._publish_card_created_nowait(node)
            for edge in instance.edges:
                self._publish_edge_change_nowait(EventType.EDGE_CREATED, edge)
            index = 0
            while index < len(event_transaction.events):
                self._emit_sandbox_event_nowait(event_transaction.events[index])
                index += 1
            # No await exists between the final length check and this state
            # transition, so an inherited task either joined the flush above
            # or will publish directly after observing "committed".
            event_transaction.state = "committed"
            return instance

    async def _instantiate_legion_locked(
        self, legion_id: str, request: LegionInstantiate
    ) -> LegionInstance:
        record = self.legions.get(legion_id)
        summary = self._legion_summary(record)
        if not summary.compatible:
            raise PluginCompatibilityError(
                f"legion {legion_id!r} is incompatible: " + "; ".join(summary.issues)
            )

        node_ids = {node.key: str(uuid4()) for node in record.blueprint.nodes}
        created_nodes: list[Card] = []
        created_edges: list[Edge] = []
        creation_receipts: dict[
            str, tuple[NodeLifecycleTransaction, ...]
        ] = {}
        try:
            for node in record.blueprint.nodes:
                created_nodes.append(await self._create_card(
                    CardCreate(
                        id=node_ids[node.key],
                        type=node.type,
                        name=node.name,
                        position={
                            "x": request.position.x + node.position.x,
                            "y": request.position.y + node.position.y,
                        },
                        size=node.size,
                        expanded=node.expanded,
                        config=dict(node.config),
                    ),
                    template_payload_version=node.payload_version,
                    template_payload=node.payload,
                    template_node_ids=node_ids,
                    _creation_receipts=creation_receipts,
                    _publish_event=False,
                ))
            for edge in record.blueprint.edges:
                created_edges.append(await self.create_edge(EdgeCreate(
                    source=node_ids[edge.source],
                    target=node_ids[edge.target],
                    relationship=edge.relationship,
                    direction=edge.direction,
                ), _publish_event=False))
        except BaseException as error:
            cleanup_errors = await self._compensate_legion_instance(
                created_nodes, created_edges, creation_receipts, error
            )
            for cleanup_error in cleanup_errors:
                error.add_note(
                    "Legion instantiation compensation also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise
        return LegionInstance(
            legion_id=record.id,
            nodes=created_nodes,
            edges=created_edges,
        )

    async def _compensate_legion_instance(
        self,
        nodes: list[Card],
        edges: list[Edge],
        creation_receipts: Mapping[str, tuple[NodeLifecycleTransaction, ...]],
        cause: BaseException,
    ) -> list[BaseException]:
        async def cleanup() -> list[BaseException]:
            failures: list[BaseException] = []
            for edge in reversed(edges):
                try:
                    # This cleanup runs in a child task while its parent owns
                    # the formation barrier. Reacquiring it would deadlock.
                    await self._delete_edge_locked(edge.id, _publish_event=False)
                except BaseException as error:
                    failures.append(error)
            for node in reversed(nodes):
                for transaction in reversed(creation_receipts.get(node.id, ())):
                    rollback_error = await self._rollback_lifecycle(transaction, cause)
                    if rollback_error is not None:
                        failures.append(rollback_error)
                try:
                    self.world.delete_card(node.id)
                except BaseException as error:
                    failures.append(error)
            return failures

        cleanup_task = asyncio.create_task(cleanup())
        while True:
            try:
                return await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                if cleanup_task.done():
                    return cleanup_task.result()
                continue

    def _legion_summary(self, record: LegionRecord) -> LegionSummary:
        issues = self._legion_compatibility_issues(record)
        return LegionSummary(
            id=record.id,
            name=record.name,
            description=record.description,
            node_count=len(record.blueprint.nodes),
            edge_count=len(record.blueprint.edges),
            bounds=record.blueprint.bounds,
            node_types=sorted({node.type for node in record.blueprint.nodes}),
            plugin_ids=sorted({
                *(node.plugin_id for node in record.blueprint.nodes),
                *(
                    dependency.plugin_id
                    for node in record.blueprint.nodes
                    for dependency in node.dependencies
                ),
                *(edge.plugin_id for edge in record.blueprint.edges),
            }),
            compatible=not issues,
            issues=issues,
            created_at=record.created_at,
            updated_at=record.updated_at,
            revision=record.revision,
        )

    def _legion_compatibility_issues(self, record: LegionRecord) -> list[str]:
        if record.blueprint.format_version != 1:
            return [
                f"blueprint format {record.blueprint.format_version} is not supported"
            ]
        issues: list[str] = []
        nodes = {node.key: node for node in record.blueprint.nodes}
        if len(nodes) != len(record.blueprint.nodes):
            issues.append("blueprint contains duplicate node keys")
        for node in record.blueprint.nodes:
            if not self.plugins.has_plugin(node.plugin_id):
                issues.append(
                    f"node type {node.type!r} requires missing plugin {node.plugin_id!r}"
                )
                continue
            try:
                definition = self.plugins.node_type(node.type)
                owner = self.plugins.node_type_owner_id(node.type)
            except (GraphValidationError, ValueError) as error:
                issues.append(str(error))
                continue
            if owner != node.plugin_id:
                issues.append(
                    f"node type {node.type!r} is now owned by {owner!r}, not "
                    f"{node.plugin_id!r}"
                )
            if not definition.templateable:
                issues.append(f"node type {node.type!r} is no longer templateable")
            if (
                definition.template_status is not None
                and node.status != definition.template_status
            ):
                issues.append(
                    f"node type {node.type!r} now requires template status "
                    f"{definition.template_status!r}, not {node.status!r}"
                )
            validated_config = node.config
            config_is_valid = True
            try:
                self.plugins.validate_status(node.type, node.status)
                validated_config = self.plugins.validate_config(node.type, node.config)
                if validated_config != node.config:
                    issues.append(
                        f"node type {node.type!r} portable configuration is no longer "
                        "accepted unchanged"
                    )
                restored_status = str(
                    validated_config.get("status", definition.default_status)
                )
                if restored_status != node.status:
                    issues.append(
                        f"node type {node.type!r} template status {node.status!r} "
                        f"does not match its portable configuration ({restored_status!r})"
                    )
            except GraphValidationError as error:
                issues.append(str(error))
                config_is_valid = False
            handler = definition.template_handler
            if handler is not None and config_is_valid:
                try:
                    declared_dependencies = {
                        (dependency.kind, dependency.id)
                        for dependency in self._node_template_dependencies(
                            handler, validated_config
                        )
                    }
                except PluginCompatibilityError as error:
                    issues.append(
                        f"node type {node.type!r} template dependencies are invalid: "
                        f"{error}"
                    )
                    declared_dependencies = set()
                stored_dependencies = {
                    (dependency.kind, dependency.id)
                    for dependency in node.dependencies
                }
                for dependency_kind, dependency_id in sorted(
                    declared_dependencies - stored_dependencies
                ):
                    issues.append(
                        f"node type {node.type!r} requires unrecorded template "
                        f"dependency {dependency_kind.replace('_', ' ')} "
                        f"{dependency_id!r}"
                    )
            seen_dependencies: set[tuple[str, str]] = set()
            for dependency in node.dependencies:
                dependency_key = (dependency.kind, dependency.id)
                if dependency_key in seen_dependencies:
                    issues.append(
                        f"node type {node.type!r} contains duplicate template dependency "
                        f"{dependency.kind.replace('_', ' ')} {dependency.id!r}"
                    )
                    continue
                seen_dependencies.add(dependency_key)
                try:
                    dependency_owner = self.plugins.owner_id(
                        dependency.kind, dependency.id
                    )
                except ValueError:
                    if not self.plugins.has_plugin(dependency.plugin_id):
                        issues.append(
                            f"node type {node.type!r} template dependency "
                            f"{dependency.kind.replace('_', ' ')} {dependency.id!r} "
                            f"requires missing plugin {dependency.plugin_id!r}"
                        )
                    else:
                        issues.append(
                            f"node type {node.type!r} requires missing template "
                            f"dependency {dependency.kind.replace('_', ' ')} "
                            f"{dependency.id!r}"
                        )
                    continue
                if dependency_owner != dependency.plugin_id:
                    issues.append(
                        f"node type {node.type!r} template dependency "
                        f"{dependency.kind.replace('_', ' ')} {dependency.id!r} "
                        f"is now owned by {dependency_owner!r}, not "
                        f"{dependency.plugin_id!r}"
                    )
                elif not self.plugins.has_plugin(dependency.plugin_id):
                    issues.append(
                        f"node type {node.type!r} template dependency "
                        f"{dependency.kind.replace('_', ' ')} {dependency.id!r} "
                        f"requires missing plugin {dependency.plugin_id!r}"
                    )
            if node.payload is None and handler is not None:
                issues.append(
                    f"node type {node.type!r} now requires template payload that the "
                    "saved Legion does not contain"
                )
            elif node.payload is not None:
                if handler is None:
                    issues.append(
                        f"node type {node.type!r} no longer provides its template handler"
                    )
                elif node.payload_version is None or not handler.supports_payload_version(
                    node.payload_version
                ):
                    issues.append(
                        f"node type {node.type!r} template payload version "
                        f"{node.payload_version!r} is unsupported"
                    )
                else:
                    try:
                        handler.validate_payload(node.payload, node.payload_version)
                    except PluginCompatibilityError as error:
                        issues.append(
                            f"node type {node.type!r} template payload is invalid: "
                            f"{error}"
                        )
        for edge in record.blueprint.edges:
            source = nodes.get(edge.source)
            target = nodes.get(edge.target)
            if source is None or target is None:
                issues.append(f"edge {edge.key!r} references an unknown node")
                continue
            if not self.plugins.has_plugin(edge.plugin_id):
                issues.append(
                    f"relationship {edge.relationship!r} requires missing plugin "
                    f"{edge.plugin_id!r}"
                )
                continue
            try:
                definition = self.plugins.relationship(edge.relationship)
                owner = self.plugins.relationship_owner_id(edge.relationship)
                if owner != edge.plugin_id:
                    issues.append(
                        f"relationship {edge.relationship!r} is now owned by {owner!r}, "
                        f"not {edge.plugin_id!r}"
                    )
                if not definition.templateable:
                    issues.append(
                        f"relationship {edge.relationship!r} is no longer templateable"
                    )
                self.plugins.validate_relationship_order(
                    source.type, target.type, edge.relationship
                )
                self.plugins.validate_direction(
                    edge.relationship, edge.direction.value
                )
            except (GraphValidationError, ValueError) as error:
                issues.append(str(error))
        return list(dict.fromkeys(issues))

    @staticmethod
    def _node_template_dependencies(
        handler: NodeTemplateHandler, config: Mapping[str, Any]
    ) -> tuple[NodeTemplateDependency, ...]:
        dependencies = handler.dependencies(config)
        if not isinstance(dependencies, tuple):
            raise PluginCompatibilityError(
                "template dependencies must be returned as a tuple"
            )
        if not all(
            isinstance(dependency, NodeTemplateDependency)
            for dependency in dependencies
        ):
            raise PluginCompatibilityError(
                "template dependencies must be NodeTemplateDependency values"
            )
        keys = [(dependency.kind, dependency.id) for dependency in dependencies]
        if len(keys) != len(set(keys)):
            raise PluginCompatibilityError(
                "template dependencies must not contain duplicates"
            )
        return dependencies

    async def create_edge(
        self, request: EdgeCreate, *, _publish_event: bool = True
    ) -> Edge:
        async with self._node_mutation():
            return await self._create_edge_locked(
                request, _publish_event=_publish_event
            )

    async def _create_edge_locked(
        self, request: EdgeCreate, *, _publish_event: bool = True
    ) -> Edge:
        request = self.world.normalize_edge_request(request)
        source = self.world.get_card(request.source)
        target = self.world.get_card(request.target)
        self.world._assert_valid_relationship(source.type, target.type, request.relationship)
        self.world._assert_valid_direction(
            source.type, target.type, request.relationship, request.direction
        )
        mounted = False
        if self._is_mount(source.type, target.type, request.relationship):
            mounted = await self._attach_mount_values(
                request.source, request.target, request.relationship
            )
        try:
            edge = self.world.create_edge(request)
        except BaseException:
            if mounted and self.sandbox_backend is not None:
                with suppress(Exception):
                    await self.sandbox_backend.detach_resource(request.target, request.source)
            raise
        if _publish_event:
            await self._publish_edge_change(EventType.EDGE_CREATED, edge)
        return edge

    async def update_edge(self, edge_id: str, request: EdgePatch) -> Edge:
        async with self._node_mutation():
            return await self._update_edge_locked(edge_id, request)

    async def _update_edge_locked(self, edge_id: str, request: EdgePatch) -> Edge:
        old = self.world.get_edge(edge_id)
        previously_affected = self._affected_agents(old)
        source = self.world.get_card(old.source)
        target = self.world.get_card(old.target)
        relationship = request.relationship or old.relationship
        direction = request.direction or old.direction
        self.world._assert_valid_relationship(source.type, target.type, relationship)
        self.world._assert_valid_direction(source.type, target.type, relationship, direction)
        if self._is_mount(source.type, target.type, relationship):
            await self._attach_mount_values(old.source, old.target, relationship)
        try:
            edge = self.world.update_edge(edge_id, request)
        except BaseException:
            if self._is_mount(source.type, target.type, old.relationship):
                with suppress(Exception):
                    await self._attach_mount_values(old.source, old.target, old.relationship)
            raise
        await self._publish_edge_change(
            EventType.EDGE_UPDATED,
            edge,
            affected_agents=sorted(
                set(previously_affected) | set(self._affected_agents(edge))
            ),
        )
        return edge

    async def delete_edge(
        self, edge_id: str, *, _publish_event: bool = True
    ) -> Edge:
        async with self._node_mutation():
            return await self._delete_edge_locked(
                edge_id, _publish_event=_publish_event
            )

    async def _delete_edge_locked(
        self, edge_id: str, *, _publish_event: bool = True
    ) -> Edge:
        edge = self.world.get_edge(edge_id)
        affected = self._affected_agents(edge)
        source = self.world.get_card(edge.source)
        target = self.world.get_card(edge.target)
        detached = False
        if self._is_mount(source.type, target.type, edge.relationship):
            detached = await self._detach_mount(edge, ignore_missing=True)
        try:
            edge = self.world.delete_edge(edge_id)
        except BaseException:
            if detached:
                with suppress(Exception):
                    await self._attach_mount_values(
                        edge.source, edge.target, edge.relationship
                    )
            raise
        if _publish_event:
            await self._publish_edge_change(
                EventType.EDGE_DELETED, edge, affected_agents=affected
            )
        return edge

    async def replace_text(
        self,
        card_id: str,
        request: TextReplace,
        *,
        agent_id: str | None = None,
    ) -> TextDocument:
        async with self._node_mutation():
            return await self._replace_text_locked(
                card_id, request, agent_id=agent_id
            )

    async def _replace_text_locked(
        self,
        card_id: str,
        request: TextReplace,
        *,
        agent_id: str | None = None,
    ) -> TextDocument:
        if agent_id is None:
            document = self.resources.replace_text(
                card_id, request.content, expected_revision=request.expected_revision
            )
        else:
            document = self.capabilities.replace_text(
                agent_id,
                card_id,
                request.content,
                expected_revision=request.expected_revision,
            )
        await self._publish_resource_modified(document, agent_id=agent_id, operation="replace")
        return document

    async def import_image(self, card_id: str, request: ImageImport) -> ResourceRecord:
        async with self._node_mutation():
            return await self._import_image_locked(card_id, request)

    async def _import_image_locked(
        self, card_id: str, request: ImageImport
    ) -> ResourceRecord:
        record = self.resources.create_image(
            card_id, request.filename, request.media_type, request.data_base64
        )
        for edge in self.world.list_edges_from(card_id):
            if edge.relationship == Relationship.MOUNT_READ_ONLY:
                await self._attach_mount(edge)
        await self.events.publish(
            EventType.RESOURCE_MODIFIED,
            node_id=card_id,
            resource_id=card_id,
            payload={
                "operation": "import",
                "revision": record.revision,
                "size_bytes": record.size_bytes,
                "media_type": record.media_type,
                "width": record.width,
                "height": record.height,
            },
        )
        return record

    async def patch_text(
        self,
        card_id: str,
        request: TextPatch,
        *,
        agent_id: str | None = None,
    ) -> TextDocument:
        async with self._node_mutation():
            return await self._patch_text_locked(
                card_id, request, agent_id=agent_id
            )

    async def _patch_text_locked(
        self,
        card_id: str,
        request: TextPatch,
        *,
        agent_id: str | None = None,
    ) -> TextDocument:
        if agent_id is None:
            document = self.resources.patch_text(
                card_id, request.edits, expected_revision=request.expected_revision
            )
        else:
            document = self.capabilities.patch_text(
                agent_id,
                card_id,
                request.edits,
                expected_revision=request.expected_revision,
            )
        await self._publish_resource_modified(document, agent_id=agent_id, operation="patch")
        return document

    async def run_agent(self, agent_id: str, prompt: str) -> dict[str, Any]:
        self._require_card_type(agent_id, CardType.AGENT)
        run = await self._require_run_manager().start_run(agent_id, prompt)
        return {"accepted": True, "agent_id": agent_id, "run_id": run.run_id}

    def conversation_summary(self, conversation_id: str) -> ConversationSummary:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        sessions = self.conversations.list_sessions(conversation_id)
        connected_ids = {
            edge.source
            for edge in self.world.list_edges_to(conversation_id)
            if edge.relationship == Relationship.PARTICIPATE
        }
        agent_ids = connected_ids | {
            agent_id for session in sessions for agent_id in session.participant_ids
        }
        agents: list[ConversationAgent] = []
        for agent_id in sorted(agent_ids):
            card = self.world.maybe_get_card(agent_id)
            if card is None or card.type != CardType.AGENT:
                continue
            agents.append(ConversationAgent(
                id=card.id,
                name=card.name,
                status=card.status,
                model=str(card.config.get("model", "Default model")),
                connected=card.id in connected_ids,
            ))
        return ConversationSummary(
            conversation_id=conversation_id,
            sessions=sessions,
            agents=agents,
        )

    def list_agent_conversation_sessions(
        self, agent_id: str
    ) -> list[ConversationSession]:
        self._require_card_type(agent_id, CardType.AGENT)
        return self.conversations.list_agent_sessions(agent_id)

    async def create_conversation_session(
        self, conversation_id: str, request: ConversationSessionCreate
    ) -> ConversationSession:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        participants = list(dict.fromkeys(request.participant_ids))
        for agent_id in participants:
            self._require_conversation_connection(agent_id, conversation_id)
        session = self.conversations.create_session(
            conversation_id,
            request.model_copy(update={"participant_ids": participants}),
        )
        self.state.ensure_scope("session", session.id, schema_id="core.session")
        await self.events.publish(
            EventType.CONVERSATION_SESSION_CREATED,
            node_id=conversation_id,
            conversation_id=conversation_id,
            session_id=session.id,
            payload={"session": session.model_dump(mode="json")},
        )
        return session

    async def add_conversation_session_participants(
        self,
        conversation_id: str,
        session_id: str,
        request: ConversationParticipantsAdd,
    ) -> ConversationSession:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        session = self.conversations.get_session(conversation_id, session_id)
        additions = list(dict.fromkeys(request.participant_ids))
        combined = list(dict.fromkeys([*session.participant_ids, *additions]))
        if len(combined) > 24:
            raise ConversationValidationError(
                "conversation sessions support at most 24 participants"
            )
        for agent_id in additions:
            self._require_conversation_connection(agent_id, conversation_id)
        updated = self.conversations.add_participants(
            conversation_id, session_id, additions
        )
        await self.events.publish(
            EventType.CONVERSATION_SESSION_UPDATED,
            node_id=conversation_id,
            conversation_id=conversation_id,
            session_id=session_id,
            payload={"session": updated.model_dump(mode="json")},
        )
        return updated

    async def remove_conversation_session_participant(
        self, conversation_id: str, session_id: str, agent_id: str
    ) -> ConversationSession:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        updated = self.conversations.remove_participant(
            conversation_id, session_id, agent_id
        )
        await self.events.publish(
            EventType.CONVERSATION_SESSION_UPDATED,
            node_id=conversation_id,
            conversation_id=conversation_id,
            session_id=session_id,
            payload={"session": updated.model_dump(mode="json")},
        )
        return updated

    async def delete_conversation_session(
        self, conversation_id: str, session_id: str
    ) -> None:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        session = self.conversations.get_session(conversation_id, session_id)
        if session.title == "General":
            raise ConversationValidationError("the default General session cannot be deleted")
        self.conversations.delete_session(conversation_id, session_id)
        self.state.delete_scope("session", session_id)
        await self.events.publish(
            EventType.CONVERSATION_SESSION_DELETED,
            node_id=conversation_id,
            conversation_id=conversation_id,
            session_id=session_id,
            payload={"session_id": session_id},
        )

    def list_conversation_messages(
        self, conversation_id: str, session_id: str, *, limit: int = 200
    ) -> list[ConversationMessage]:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        return self.conversations.list_messages(
            conversation_id, session_id, limit=limit
        )

    async def post_conversation_message(
        self, conversation_id: str, session_id: str, request: ConversationPost
    ) -> ConversationPostResult:
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        session = self.conversations.get_session(conversation_id, session_id)
        mentions = list(dict.fromkeys(request.mention_agent_ids))
        for agent_id in mentions:
            self._require_session_participant(session, agent_id)
            self._require_conversation_connection(agent_id, conversation_id)
        manager = self._require_run_manager()
        for agent_id in mentions:
            manager.assert_can_start(agent_id)
        message = self.conversations.add_message(
            conversation_id,
            session_id,
            sender_kind="user",
            sender_id=None,
            sender_name="You",
            content=request.content,
            mention_agent_ids=mentions,
        )
        await self._publish_conversation_message(message)
        accepted: list[str] = []
        for agent_id in mentions:
            prompt = self._conversation_prompt(
                conversation_id, session, agent_id, request.content
            )
            run = await self._require_run_manager().start_run(
                agent_id,
                prompt,
                caller_kind="conversation",
                caller_id=conversation_id,
                context_id=session_id,
            )
            task = asyncio.create_task(
                self._persist_conversation_run(
                    agent_id, run.run_id, conversation_id, session_id
                )
            )
            task.add_done_callback(
                lambda completed: (
                    completed.exception() if not completed.cancelled() else None
                )
            )
            accepted.append(agent_id)
        return ConversationPostResult(
            message=message, accepted_agent_ids=accepted
        )

    async def request_conversation_turn(
        self,
        source_agent_id: str,
        conversation_id: str,
        session_id: str,
        target_agent_id: str,
        message: str,
    ) -> dict[str, str]:
        depth = _conversation_turn_depth.get()
        if depth >= _MAX_CONVERSATION_TURN_DEPTH:
            raise ConversationValidationError(
                "conversation agent handoff limit reached"
            )
        session = self.conversations.get_session(conversation_id, session_id)
        self._require_session_participant(session, source_agent_id)
        self._require_session_participant(session, target_agent_id)
        self._require_conversation_connection(source_agent_id, conversation_id)
        self._require_conversation_connection(target_agent_id, conversation_id)
        if source_agent_id == target_agent_id:
            source = self._require_card_type(source_agent_id, CardType.AGENT)
            return {
                "agent_id": source.id,
                "agent_name": source.name,
                "response": (
                    "You already have the current turn. Reply directly instead of "
                    "requesting another turn from yourself."
                ),
            }
        source = self._require_card_type(source_agent_id, CardType.AGENT)
        target = self._require_card_type(target_agent_id, CardType.AGENT)
        request_message = self.conversations.add_message(
            conversation_id,
            session_id,
            sender_kind="agent",
            sender_id=source.id,
            sender_name=source.name,
            content=message.strip(),
            mention_agent_ids=[target_agent_id],
        )
        await self._publish_conversation_message(request_message)
        manager = self._require_run_manager()
        if manager.is_agent_in_lineage(target_agent_id):
            return {
                "agent_id": target.id,
                "agent_name": target.name,
                "response": (
                    f"{target.name} is already active earlier in this conversation turn. "
                    "Your message was delivered to the shared session; finish your current "
                    "response without requesting the same Agent again."
                ),
            }
        prompt = self._conversation_prompt(
            conversation_id, session, target_agent_id, message
        )
        token = _conversation_turn_depth.set(depth + 1)
        try:
            run = await manager.start_run(
                target_agent_id,
                prompt,
                caller_kind="agent",
                caller_id=source_agent_id,
                context_id=session_id,
            )
            await self._await_synchronous_turn(manager, run.run_id, target.name)
            final_text = manager.final_text(run.run_id)
        finally:
            _conversation_turn_depth.reset(token)
        response = self.conversations.add_message(
            conversation_id,
            session_id,
            sender_kind="agent",
            sender_id=target.id,
            sender_name=target.name,
            content=final_text or "No response was produced.",
            run_id=run.run_id,
        )
        await self._publish_conversation_message(response)
        return {
            "agent_id": target.id,
            "agent_name": target.name,
            "response": final_text,
        }

    async def _await_synchronous_turn(
        self, manager: RunManager, run_id: str, target_name: str
    ) -> RunRecord:
        """Wait for a delegated Run without blocking forever on a suspension.

        Synchronous turns (conversation handoffs, agent-to-agent messages)
        cannot span an external suspension: if the provider turn ends without a
        terminal status, the delegated Run is cancelled and a clear error is
        raised to the caller instead of hanging indefinitely.
        """

        record = await manager.wait_execution(run_id)
        if record.status not in TERMINAL_RUN_STATUSES:
            await manager.cancel_run(run_id)
            raise RuntimeUnavailableError(
                f"agent {target_name!r} suspended its run before completing the "
                "requested turn; the delegated run was cancelled"
            )
        self._require_successful_run(record)
        return record

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        self._require_card_type(agent_id, CardType.AGENT)
        await self._require_run_manager().cancel_agent_runs(agent_id)
        return {"agent_id": agent_id, "status": "idle"}

    async def get_agent(self, agent_id: str) -> Any:
        self._require_card_type(agent_id, CardType.AGENT)
        return await self._require_run_manager().get_agent(agent_id)

    async def communicate_with_agent(
        self, source_agent_id: str, target_agent_id: str, message: str
    ) -> dict[str, str]:
        source = self._require_card_type(source_agent_id, CardType.AGENT)
        target = self._require_card_type(target_agent_id, CardType.AGENT)
        self.capabilities.require_agent_communicate(source_agent_id, target_agent_id)
        prompt = f"Message from {source.name}:\n\n{message.strip()}"
        manager = self._require_run_manager()
        if manager.is_agent_in_lineage(target_agent_id):
            return {
                "agent_id": target.id,
                "agent_name": target.name,
                "response": (
                    f"{target.name} is already active earlier in this request. "
                    "Your message was not sent as a nested synchronous call; "
                    "complete the current response instead."
                ),
            }
        run = await manager.start_run(
            target_agent_id,
            prompt,
            caller_kind="agent",
            caller_id=source_agent_id,
        )
        await self._await_synchronous_turn(manager, run.run_id, target.name)
        final_text = manager.final_text(run.run_id)
        return {
            "agent_id": target.id,
            "agent_name": target.name,
            "response": final_text,
        }

    async def configure_llm_connection(
        self, *, base_url: str | None, api_key: str | None
    ) -> dict[str, bool]:
        runtime = self._require_run_manager().default_provider()
        if not isinstance(runtime, GoogleAdkAgentRuntime):
            raise RuntimeUnavailableError("ADK agent runtime is not configured")
        runtime.configure_litellm_connection(
            api_base=base_url,
            api_key=api_key,
        )
        return {"configured": True}

    async def start_sandbox(self, sandbox_id: str) -> Any:
        async with self._node_mutation():
            return await self._start_sandbox_locked(sandbox_id)

    async def _start_sandbox_locked(self, sandbox_id: str) -> Any:
        self._require_card_type(sandbox_id, CardType.SANDBOX)
        backend = self._require_sandbox_backend()
        await self._ensure_sandbox(sandbox_id)
        for edge in self.world.list_edges_to(sandbox_id):
            if edge.relationship in {
                Relationship.MOUNT_READ_ONLY,
                Relationship.MOUNT_READ_WRITE,
            }:
                await self._attach_mount(edge)
        info = await backend.start(sandbox_id)
        self.world.update_card(sandbox_id, CardPatch(status=info.state.value))
        return info

    async def stop_sandbox(self, sandbox_id: str) -> Any:
        async with self._node_mutation():
            return await self._stop_sandbox_locked(sandbox_id)

    async def _stop_sandbox_locked(self, sandbox_id: str) -> Any:
        self._require_card_type(sandbox_id, CardType.SANDBOX)
        backend = self._require_sandbox_backend()
        await backend.terminate(sandbox_id)
        info = await backend.get(sandbox_id)
        self.world.update_card(sandbox_id, CardPatch(status=info.state.value))
        return info

    async def execute_sandbox(
        self,
        sandbox_id: str,
        argv: list[str],
        *,
        timeout_seconds: float | None = None,
        agent_id: str | None = None,
    ) -> CommandResult:
        async with self._portable_state_gate.execution():
            # Validate graph authority against a complete formation, but do
            # not hold the graph barrier while an arbitrary command runs.
            async with self._node_mutation():
                self._require_card_type(sandbox_id, CardType.SANDBOX)
                if agent_id is not None:
                    self.capabilities.require_sandbox_execute(agent_id, sandbox_id)
            backend = self._require_sandbox_backend()
            command_finished = asyncio.Event()

            async def execute_and_refresh() -> CommandResult:
                try:
                    return await backend.execute(
                        sandbox_id, argv, timeout_seconds=timeout_seconds
                    )
                finally:
                    command_finished.set()
                    await self._refresh_sandbox_write_mounts(
                        sandbox_id, agent_id=agent_id
                    )

            execution_task = asyncio.create_task(execute_and_refresh())
            try:
                return await asyncio.shield(execution_task)
            except asyncio.CancelledError:
                async def stop_and_finish() -> None:
                    if not command_finished.is_set():
                        try:
                            await backend.terminate(sandbox_id)
                        except BaseException as error:
                            logger.error(
                                "failed to terminate cancelled sandbox command %s",
                                sandbox_id,
                                exc_info=(type(error), error, error.__traceback__),
                            )
                    try:
                        await asyncio.shield(execution_task)
                    except BaseException:
                        # The caller is already being cancelled. The important
                        # invariant is that native execution and resource
                        # refresh have both reached a terminal point before the
                        # portable-state lease is released.
                        pass

                await self._complete_committed(stop_and_finish())
                raise

    async def _refresh_sandbox_write_mounts(
        self, sandbox_id: str, *, agent_id: str | None
    ) -> None:
        async with self._node_mutation():
            for edge in self.world.list_edges_to(sandbox_id):
                source = self.world.get_card(edge.source)
                if (
                    source.type == CardType.TEXT
                    and edge.relationship == Relationship.MOUNT_READ_WRITE
                ):
                    document = self.resources.refresh_text_if_changed(
                        source.id, actor_id=sandbox_id
                    )
                    if document is not None:
                        await self._publish_resource_modified(
                            document, agent_id=agent_id, operation="sandbox"
                        )

    async def get_sandbox(self, sandbox_id: str) -> Any:
        self._require_card_type(sandbox_id, CardType.SANDBOX)
        backend = self._require_sandbox_backend()
        return await backend.get(sandbox_id)

    async def publish_sandbox_event(self, event: SandboxEvent) -> None:
        transaction = self._sandbox_event_transaction.get()
        if transaction is not None:
            if transaction.state in {"open", "committing"}:
                transaction.events.append(event)
                return
            if transaction.state == "discarded":
                return
        await self._emit_sandbox_event(event)

    async def _emit_sandbox_event(self, event: SandboxEvent) -> None:
        await self.events.publish(
            _SANDBOX_EVENT_TYPES[event.type],
            node_id=event.sandbox_id,
            sandbox_id=event.sandbox_id,
            resource_id=(
                str(event.payload["resource_id"])
                if "resource_id" in event.payload
                else None
            ),
            payload=dict(event.payload),
        )

    def _emit_sandbox_event_nowait(self, event: SandboxEvent) -> None:
        self.events.publish_event_nowait(RuntimeEvent(
            type=_SANDBOX_EVENT_TYPES[event.type],
            node_id=event.sandbox_id,
            sandbox_id=event.sandbox_id,
            resource_id=(
                str(event.payload["resource_id"])
                if "resource_id" in event.payload
                else None
            ),
            payload=dict(event.payload),
        ))

    async def _persist_conversation_run(
        self,
        agent_id: str,
        run_id: str,
        conversation_id: str,
        session_id: str,
    ) -> None:
        """Persist the durable conversation outcome of an Agent Run.

        Every Run started from a conversation ends with a visible transcript
        entry: a normal agent message on success, or a system notice for
        failures, cancellations, interruptions, suspensions, and empty
        responses. Nothing is dropped silently.
        """

        try:
            manager = self._require_run_manager()
            record = await manager.wait_execution(run_id)
            if record.status not in TERMINAL_RUN_STATUSES:
                # The provider turn ended without a durable result. Surface the
                # wait state instead of leaving the transcript stalled, then
                # keep following the Run until it terminates.
                suspension = manager.get_suspension(run_id)
                reason = suspension.reason if suspension is not None else None
                name = self._conversation_agent_name(agent_id)
                await self._persist_conversation_outcome_notice(
                    run_id,
                    conversation_id,
                    session_id,
                    f"{name} paused this response to wait on external work"
                    + (f" ({reason})." if reason else "."),
                )
                record = await manager.wait_terminal(run_id)
            final_text = manager.final_text(run_id)
            if record.status is RunStatus.SUCCEEDED and final_text:
                if self._can_agent_post_to_conversation_session(
                    agent_id, conversation_id, session_id
                ):
                    agent = self.world.get_card(agent_id)
                    message = self.conversations.add_message(
                        conversation_id,
                        session_id,
                        sender_kind="agent",
                        sender_id=agent.id,
                        sender_name=agent.name,
                        content=final_text,
                        run_id=run_id,
                    )
                    await self._publish_conversation_message(message)
                return
            name = self._conversation_agent_name(agent_id)
            if record.status is RunStatus.SUCCEEDED:
                notice = f"{name} finished without producing a response."
            elif record.status is RunStatus.FAILED:
                detail = record.error or "the runtime reported no error detail"
                notice = f"{name} could not respond: {detail}"
            elif record.status is RunStatus.CANCELLED:
                notice = f"{name}'s response was stopped before completion."
            else:
                notice = f"{name}'s response was interrupted by a backend restart."
            await self._persist_conversation_outcome_notice(
                run_id, conversation_id, session_id, notice
            )
        except Exception:
            logger.exception(
                "failed to persist conversation outcome for run %s in %s/%s",
                run_id,
                conversation_id,
                session_id,
            )

    def _conversation_agent_name(self, agent_id: str) -> str:
        agent = self.world.maybe_get_card(agent_id)
        return agent.name if agent is not None else "The agent"

    async def _persist_conversation_outcome_notice(
        self,
        run_id: str,
        conversation_id: str,
        session_id: str,
        content: str,
    ) -> None:
        try:
            self.conversations.get_session(conversation_id, session_id)
        except NotFoundError:
            return
        message = self.conversations.add_message(
            conversation_id,
            session_id,
            sender_kind="system",
            sender_id=None,
            sender_name="System",
            content=content,
            run_id=run_id,
        )
        await self._publish_conversation_message(message)

    async def _publish_conversation_message(
        self, message: ConversationMessage
    ) -> None:
        await self.events.publish(
            EventType.CONVERSATION_MESSAGE,
            node_id=message.conversation_id,
            agent_id=(message.sender_id if message.sender_kind == "agent" else None),
            conversation_id=message.conversation_id,
            session_id=message.session_id,
            payload={"message": message.model_dump(mode="json")},
        )

    def _require_conversation_connection(
        self, agent_id: str, conversation_id: str
    ) -> None:
        self._require_card_type(agent_id, CardType.AGENT)
        self._require_card_type(conversation_id, CardType.CONVERSATION)
        edge = self.world.find_edge(agent_id, conversation_id)
        if edge is None or edge.relationship != Relationship.PARTICIPATE:
            raise PermissionDeniedError(
                f"agent {agent_id!r} is not connected to conversation {conversation_id!r}"
            )

    def _can_agent_post_to_conversation_session(
        self, agent_id: str, conversation_id: str, session_id: str
    ) -> bool:
        try:
            session = self.conversations.get_session(conversation_id, session_id)
            self._require_session_participant(session, agent_id)
            self._require_conversation_connection(agent_id, conversation_id)
        except (NotFoundError, PermissionDeniedError):
            return False
        return True

    @staticmethod
    def _require_session_participant(
        session: ConversationSession, agent_id: str
    ) -> None:
        if agent_id not in session.participant_ids:
            raise PermissionDeniedError(
                f"agent {agent_id!r} is not a participant in session {session.id!r}"
            )

    def _conversation_prompt(
        self,
        conversation_id: str,
        session: ConversationSession,
        target_agent_id: str,
        latest_message: str,
    ) -> str:
        participants = [
            self.world.get_card(agent_id)
            for agent_id in session.participant_ids
            if self.world.maybe_get_card(agent_id) is not None
        ]
        transcript = self.conversations.list_messages(
            conversation_id, session.id, limit=40
        )
        lines = "\n".join(
            f"{item.sender_name}: {item.content}" for item in transcript
        )
        roster = ", ".join(f"{item.name} ({item.id})" for item in participants)
        current = self.world.get_card(target_agent_id)
        eligible_targets = ", ".join(
            f"{item.name} ({item.id})"
            for item in participants
            if item.id != target_agent_id
        )
        return (
            "You are speaking inside a shared Open Agent World conversation.\n"
            f"Conversation id: {conversation_id}\n"
            f"Session id: {session.id}\n"
            f"Participants: {roster or 'none'}\n"
            f"Current speaker: {current.name} ({current.id})\n"
            "Eligible request_turn targets (never use the current speaker id): "
            f"{eligible_targets or 'none; do not call request_turn'}\n"
            "Use request_turn only when an eligible participant must respond; otherwise "
            "answer directly. Do not simulate another participant's answer.\n\n"
            f"Recent transcript:\n{lines}\n\n"
            f"Respond as {current.name} to the latest message: "
            f"{latest_message.strip()}"
        )

    async def _publish_resource_modified(
        self, document: TextDocument, *, agent_id: str | None, operation: str
    ) -> None:
        await self.events.publish(
            EventType.RESOURCE_MODIFIED,
            node_id=document.card_id,
            resource_id=document.card_id,
            agent_id=agent_id,
            payload={
                "operation": operation,
                "revision": document.revision,
                "size_bytes": document.size_bytes,
            },
        )

    async def _publish_edge_change(
        self,
        event_type: EventType,
        edge: Edge,
        *,
        affected_agents: list[str] | None = None,
    ) -> None:
        affected_agents = (
            self._affected_agents(edge) if affected_agents is None else affected_agents
        )
        payload = {
            "edge": edge.model_dump(mode="json"),
            "affected_agent_ids": affected_agents,
        }
        await self.events.publish(event_type, node_id=edge.source, payload=payload)
        await self.events.publish(
            EventType.PERMISSION_CHANGED, node_id=edge.source, payload=payload
        )

    def _publish_edge_change_nowait(
        self,
        event_type: EventType,
        edge: Edge,
        *,
        affected_agents: list[str] | None = None,
    ) -> None:
        affected_agents = (
            self._affected_agents(edge) if affected_agents is None else affected_agents
        )
        payload = {
            "edge": edge.model_dump(mode="json"),
            "affected_agent_ids": affected_agents,
        }
        self.events.publish_event_nowait(RuntimeEvent(
            type=event_type,
            node_id=edge.source,
            payload=payload,
        ))
        self.events.publish_event_nowait(RuntimeEvent(
            type=EventType.PERMISSION_CHANGED,
            node_id=edge.source,
            payload=payload,
        ))

    def _affected_agents(self, edge: Edge) -> list[str]:
        source = self.world.maybe_get_card(edge.source)
        if source is not None and source.type == CardType.AGENT:
            target = self.world.maybe_get_card(edge.target)
            if (
                edge.direction is EdgeDirection.BIDIRECTIONAL
                and target is not None
                and target.type == CardType.AGENT
            ):
                return sorted([source.id, target.id])
            return [source.id]
        target = self.world.maybe_get_card(edge.target)
        if target is not None and target.type == CardType.SANDBOX:
            return sorted(
                candidate.source
                for candidate in self.world.list_edges_to(target.id)
                if candidate.relationship == Relationship.EXECUTE
            )
        return []

    async def _attach_mount(self, edge: Edge) -> bool:
        return await self._attach_mount_values(edge.source, edge.target, edge.relationship)

    async def _attach_mount_values(
        self, resource_id: str, sandbox_id: str, relationship: Relationship
    ) -> bool:
        if self.sandbox_backend is None:
            return False
        record = self.resources.maybe_get_record(resource_id)
        if record is None:
            return False
        await self._ensure_sandbox(sandbox_id)
        _, source = self.resources.read_bytes(resource_id)
        access = (
            ResourceAccess.READ_WRITE
            if relationship == Relationship.MOUNT_READ_WRITE
            else ResourceAccess.READ_ONLY
        )
        relative = str(PureWindowsPath("resources", record.filename))
        await self.sandbox_backend.attach_resource(
            sandbox_id, resource_id, source, relative, access
        )
        return True

    async def _detach_mount(self, edge: Edge, *, ignore_missing: bool) -> bool:
        if self.sandbox_backend is None:
            return False
        try:
            await self.sandbox_backend.detach_resource(edge.target, edge.source)
            return True
        except SandboxNotFoundError:
            if not ignore_missing:
                raise
            return False

    async def _ensure_sandbox(self, sandbox_id: str) -> None:
        backend = self._require_sandbox_backend()
        try:
            await backend.get(sandbox_id)
        except SandboxNotFoundError:
            await backend.create(sandbox_id)

    def _require_sandbox_backend(self) -> SandboxBackend:
        if self.sandbox_backend is None:
            raise RuntimeUnavailableError(
                "the native Windows sandbox backend is unavailable"
            )
        return self.sandbox_backend

    def _require_card_type(self, card_id: str, expected: str) -> Card:
        self._assert_no_live_pending_deletions()
        card = self.world.get_card(card_id)
        if card.type != expected:
            raise NotFoundError(f"{expected} card {card_id!r} does not exist")
        return card

    @staticmethod
    def _is_mount(
        source_type: str, target_type: str, relationship: str
    ) -> bool:
        return (
            source_type in {CardType.TEXT, CardType.IMAGE}
            and target_type == CardType.SANDBOX
            and relationship
            in {Relationship.MOUNT_READ_ONLY, Relationship.MOUNT_READ_WRITE}
        )

    def _node_lifecycle_context(self) -> NodeLifecycleContext:
        manager = self._require_run_manager()
        return NodeLifecycleContext(
            nodes=_LifecycleNodes(self.world),
            resources=_LifecycleResources(self.resources),
            conversations=_LifecycleConversations(self.conversations, self.state),
            agents=_LifecycleAgents(manager),
            sandboxes=(
                _LifecycleSandboxes(self.sandbox_backend, self.resources)
                if self.sandbox_backend is not None
                else None
            ),
        )

    def _recovery_node_lifecycle_context(
        self,
        card: Card,
        edges: tuple[Edge, ...],
        resource: ResourceRecord | None,
    ) -> NodeLifecycleContext:
        manager = self._require_run_manager()
        return NodeLifecycleContext(
            nodes=_RecoveryLifecycleNodes(edges),
            resources=_RecoveryLifecycleResources(self.resources, resource),
            conversations=_LifecycleConversations(self.conversations, self.state),
            agents=_RecoveryLifecycleAgents(manager, card),
            sandboxes=(
                _LifecycleSandboxes(self.sandbox_backend, self.resources)
                if self.sandbox_backend is not None
                else None
            ),
        )

    def _node_template_capture_context(self) -> NodeTemplateCaptureContext:
        return NodeTemplateCaptureContext(
            resources=_TemplateCaptureResources(self.resources)
        )

    def _node_template_restore_context(self) -> NodeTemplateRestoreContext:
        return NodeTemplateRestoreContext(
            resources=_TemplateRestoreResources(self.resources)
        )

    def _require_run_manager(self) -> RunManager:
        if self.run_manager is None:  # pragma: no cover - construction invariant
            raise RuntimeError("RunManager has not been initialized")
        return self.run_manager

    @staticmethod
    def _require_successful_run(record: RunRecord) -> None:
        if record.status is RunStatus.SUCCEEDED:
            return
        detail = f": {record.error}" if record.error else ""
        raise RuntimeUnavailableError(
            f"run {record.run_id!r} ended as {record.status.value}{detail}"
        )

    def install_runtime_provider(
        self,
        provider_id: str,
        provider: RuntimeProvider,
        *,
        default: bool = False,
    ) -> None:
        manager = self._require_run_manager()
        manager.install_provider(provider_id, provider)
        if default:
            manager.default_runtime_provider_id = provider_id

    def close(self) -> None:
        self.database.close()


def create_services(
    settings: Settings,
    *,
    runtime_providers: Mapping[str, RuntimeProvider] | None = None,
    default_runtime_provider_id: str | None = None,
    sandbox_backend: SandboxBackend | None = None,
    plugins: PluginRegistry | None = None,
) -> ApplicationServices:
    settings.data_root.mkdir(parents=True, exist_ok=True)
    for directory in ("projects", "assets", "sandboxes", "database", "logs"):
        (settings.data_root / directory).mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_path)
    plugin_registry = plugins or load_plugin_registry()
    world = WorldStore(database, plugin_registry, chunk_size=settings.chunk_size)
    try:
        world.assert_plugin_availability()
    except BaseException:
        database.close()
        raise
    resources = ManagedResourceStore(database, settings.data_root)
    conversations = ConversationStore(database)
    capabilities = CapabilityBroker(world, resources, plugin_registry)
    events = EventHub(queue_size=settings.event_queue_size)

    state_event_types = {
        StateMutationKind.CREATED: EventType.STATE_CREATED,
        StateMutationKind.UPDATED: EventType.STATE_UPDATED,
        StateMutationKind.DELETED: EventType.STATE_DELETED,
    }

    def publish_state_mutation(mutation: StateMutation) -> None:
        events.publish_event_nowait(RuntimeEvent(
            type=state_event_types[mutation.kind],
            node_id=(
                mutation.scope.owner_id
                if mutation.scope.scope_kind == "agent"
                else None
            ),
            agent_id=mutation.actor_id,
            run_id=mutation.run_id,
            payload={
                "scope_id": mutation.scope.scope_id,
                "scope_kind": mutation.scope.scope_kind,
                "owner_id": mutation.scope.owner_id,
                "key": mutation.key,
                "revision": mutation.revision,
                **({"actor_id": mutation.actor_id} if mutation.actor_id else {}),
                **({"run_id": mutation.run_id} if mutation.run_id else {}),
            },
        ))

    state = StateStore(database, plugin_registry, event_sink=publish_state_mutation)
    state.ensure_scope("world", "default", schema_id="core.world")
    legions = LegionStore(database)
    services = ApplicationServices(
        settings=settings,
        database=database,
        world=world,
        resources=resources,
        capabilities=capabilities,
        events=events,
        plugins=plugin_registry,
        conversations=conversations,
        state=state,
        legions=legions,
        sandbox_backend=sandbox_backend,
    )
    from backend.capabilities.provider import WorldAgentCapabilityProvider

    provider = WorldAgentCapabilityProvider(services)
    services.run_manager = RunManager(
        store=RunStore(database),
        world=world,
        events=events,
        plugins=plugin_registry,
        capability_provider=provider,
        state=state,
        default_runtime_provider_id=(
            default_runtime_provider_id
            if default_runtime_provider_id is not None
            else settings.agent_runtime
        ),
        provider_options={"google.adk": {"app_name": "open-agent-world"}},
        inactivity_timeout_seconds=settings.run_inactivity_timeout_seconds,
    )
    for provider_id, runtime_provider in (runtime_providers or {}).items():
        services.install_runtime_provider(provider_id, runtime_provider)
    if services.sandbox_backend is None and settings.sandbox_runtime == "windows":
        services.sandbox_backend = WindowsSandboxBackend(
            settings.data_root, event_sink=services.publish_sandbox_event
        )
    return services
