from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any

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
    ConversationValidationError,
    NotFoundError,
    PermissionDeniedError,
    RuntimeUnavailableError,
)
from backend.events.hub import EventHub
from backend.events.models import EventType, RuntimeEvent
from backend.persistence.database import Database
from backend.plugins import NodeLifecycleContext, PluginRegistry, load_plugin_registry
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


@dataclass(frozen=True, slots=True)
class _LifecycleNodes:
    world: WorldStore

    def update_status(self, node_id: str, status: str) -> Card:
        return self.world.update_card(node_id, CardPatch(status=status))

    def list_edges_from(self, node_id: str) -> list[Edge]:
        return self.world.list_edges_from(node_id)


@dataclass(frozen=True, slots=True)
class _LifecycleAgents:
    manager: RunManager

    async def create(self, node: Card) -> None:
        await self.manager.register_agent(node)

    async def update(self, node: Card) -> None:
        await self.manager.update_agent(node)

    async def delete(self, node_id: str, *, missing_ok: bool = False) -> None:
        try:
            await self.manager.delete_agent(node_id, missing_ok=missing_ok)
        except AgentNotFoundError:
            if not missing_ok:
                raise

    async def stop(self, node_id: str) -> None:
        await self.manager.cancel_agent_runs(node_id)


@dataclass(frozen=True, slots=True)
class _LifecycleSandboxes:
    backend: SandboxBackend

    async def ensure(self, node_id: str) -> str:
        try:
            await self.backend.get(node_id)
        except SandboxNotFoundError:
            await self.backend.create(node_id)
        return (await self.backend.get(node_id)).state.value

    async def create(self, node_id: str) -> None:
        await self.backend.create(node_id)

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
    run_manager: RunManager | None = None
    sandbox_backend: SandboxBackend | None = None

    async def startup(self) -> None:
        manager = self._require_run_manager()
        await manager.startup()
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
        card = self.world.create_card(request)
        lifecycle = self.plugins.node_type(card.type).lifecycle
        context = self._node_lifecycle_context()
        try:
            if lifecycle is not None:
                await lifecycle.on_create(context, card, request)
        except BaseException as error:
            cleanup_errors: list[BaseException] = []
            if lifecycle is not None:
                try:
                    await lifecycle.on_create_rollback(context, card, request, error)
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            try:
                self.world.delete_card(card.id)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            for cleanup_error in cleanup_errors:
                error.add_note(
                    "node creation rollback also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise
        card = self.enrich_card(self.world.get_card(card.id))
        await self.events.publish(
            EventType.CARD_CREATED,
            node_id=card.id,
            payload={"node": card.model_dump(mode="json")},
        )
        return card

    def get_card(self, card_id: str) -> Card:
        return self.enrich_card(self.world.get_card(card_id))

    async def update_card(self, card_id: str, request: CardPatch) -> Card:
        current = self.world.get_card(card_id)
        lifecycle = self.plugins.node_type(current.type).lifecycle
        if lifecycle is not None:
            await lifecycle.on_update(self._node_lifecycle_context(), current, request)
        card = self.enrich_card(self.world.update_card(card_id, request))
        await self.events.publish(
            EventType.CARD_UPDATED,
            node_id=card.id,
            payload={"node": card.model_dump(mode="json")},
        )
        return card

    async def delete_card(self, card_id: str) -> Card:
        card = self.world.get_card(card_id)
        attached_edges = {
            edge.id: edge
            for edge in (
                *self.world.list_edges_from(card_id),
                *self.world.list_edges_to(card_id),
            )
        }
        affected = {edge.id: self._affected_agents(edge) for edge in attached_edges.values()}
        lifecycle = self.plugins.node_type(card.type).lifecycle
        if lifecycle is not None:
            await lifecycle.on_delete(self._node_lifecycle_context(), card)

        deleted = self.world.delete_card(card_id)
        for edge in attached_edges.values():
            await self._publish_edge_change(
                EventType.EDGE_DELETED, edge, affected_agents=affected[edge.id]
            )
        await self.events.publish(
            EventType.CARD_DELETED,
            node_id=deleted.id,
            payload={"node": deleted.model_dump(mode="json")},
        )
        return deleted

    def snapshot(self, chunks: list[tuple[int, int]] | None = None) -> WorldSnapshot:
        cards = self.world.list_cards(chunks)
        enriched = [self.enrich_card(card) for card in cards]
        card_ids = [card.id for card in cards] if chunks is not None else None
        edges = self.world.list_edges(card_ids)
        loaded_chunks = (
            sorted(set(chunks)) if chunks is not None else sorted({card.chunk for card in cards})
        )
        return WorldSnapshot(
            nodes=enriched,
            edges=edges,
            chunks=loaded_chunks,
            chunk_size=self.world.chunk_size,
        )

    async def create_edge(self, request: EdgeCreate) -> Edge:
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
        await self._publish_edge_change(EventType.EDGE_CREATED, edge)
        return edge

    async def update_edge(self, edge_id: str, request: EdgePatch) -> Edge:
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

    async def delete_edge(self, edge_id: str) -> Edge:
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
        self._require_card_type(sandbox_id, CardType.SANDBOX)
        if agent_id is not None:
            self.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        backend = self._require_sandbox_backend()
        result = await backend.execute(
            sandbox_id, argv, timeout_seconds=timeout_seconds
        )
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
        return result

    async def get_sandbox(self, sandbox_id: str) -> Any:
        self._require_card_type(sandbox_id, CardType.SANDBOX)
        backend = self._require_sandbox_backend()
        return await backend.get(sandbox_id)

    async def publish_sandbox_event(self, event: SandboxEvent) -> None:
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
                _LifecycleSandboxes(self.sandbox_backend)
                if self.sandbox_backend is not None
                else None
            ),
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
