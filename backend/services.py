from __future__ import annotations

import asyncio
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any

from backend.agents import AgentConfig as RuntimeAgentConfig
from backend.agents import AgentEvent, AgentRuntime, GoogleAdkAgentRuntime, create_agent_runtime
from backend.capabilities.broker import CapabilityBroker
from backend.config import Settings
from backend.conversations import (
    ConversationAgent,
    ConversationMessage,
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
from backend.events.models import EventType
from backend.persistence.database import Database
from backend.plugins import PluginRegistry, load_plugin_registry
from backend.resources.manager import ManagedResourceStore
from backend.resources.models import (
    ImageImport,
    ResourceRecord,
    TextDocument,
    TextPatch,
    TextReplace,
)
from backend.sandbox import (
    CommandResult,
    ResourceAccess,
    SandboxBackend,
    SandboxEvent,
    SandboxEventType,
    SandboxNotFoundError,
    WindowsSandboxBackend,
)
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
    agent_runtime: AgentRuntime | None = None
    sandbox_backend: SandboxBackend | None = None
    _agent_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    async def startup(self) -> None:
        for card in self.world.list_cards():
            if card.type == CardType.AGENT:
                if self.agent_runtime is not None:
                    await self.agent_runtime.create_agent(self._runtime_agent_config(card))
                if card.status != "idle":
                    self.world.update_card(card.id, CardPatch(status="idle"))
            elif card.type == CardType.SANDBOX and self.sandbox_backend is not None:
                await self._ensure_sandbox(card.id)
                info = await self.sandbox_backend.get(card.id)
                if card.status != info.state.value:
                    self.world.update_card(card.id, CardPatch(status=info.state.value))

    async def shutdown(self) -> None:
        if self.agent_runtime is not None:
            for card in self.world.list_cards():
                if card.type == CardType.AGENT:
                    with suppress(Exception):
                        await self.agent_runtime.stop(card.id)
        tasks = tuple(self._agent_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._agent_tasks.clear()
        if self.sandbox_backend is not None:
            for card in self.world.list_cards():
                if card.type == CardType.SANDBOX:
                    with suppress(SandboxNotFoundError):
                        await self.sandbox_backend.terminate(card.id)

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
        runtime_created = False
        sandbox_created = False
        try:
            if card.type == CardType.TEXT:
                filename = str(card.config.get("filename", "untitled.txt"))
                initial_content = request.content
                if initial_content is None:
                    configured_content = request.config.get("content", "")
                    initial_content = (
                        configured_content if isinstance(configured_content, str) else ""
                    )
                self.resources.create_text(card.id, filename, initial_content)
            elif card.type == CardType.IMAGE and request.data_base64 is not None:
                filename = str(card.config.get("filename", "image.png"))
                self.resources.create_image(
                    card.id, filename, request.media_type or "", request.data_base64
                )
            elif card.type == CardType.AGENT and self.agent_runtime is not None:
                await self.agent_runtime.create_agent(self._runtime_agent_config(card))
                runtime_created = True
            elif card.type == CardType.CONVERSATION:
                self.conversations.create_session(
                    card.id, ConversationSessionCreate(title="General")
                )
            elif card.type == CardType.SANDBOX and self.sandbox_backend is not None:
                await self.sandbox_backend.create(card.id)
                sandbox_created = True
        except BaseException:
            if runtime_created and self.agent_runtime is not None:
                await self.agent_runtime.delete_agent(card.id)
            if sandbox_created and self.sandbox_backend is not None:
                await self.sandbox_backend.destroy(card.id)
            record = self.resources.maybe_get_record(card.id)
            self.world.delete_card(card.id)
            if record is not None:
                self.resources.remove_file(record)
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
        if (
            current.type == CardType.AGENT
            and self.agent_runtime is not None
            and (request.name is not None or request.config is not None)
        ):
            merged = current.model_copy(
                update={
                    "name": request.name or current.name,
                    "config": {**current.config, **(request.config or {})},
                }
            )
            await self.agent_runtime.update_agent(self._runtime_agent_config(merged))
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
        record = self.resources.maybe_get_record(card_id)

        if card.type in {CardType.TEXT, CardType.IMAGE} and self.sandbox_backend is not None:
            for edge in self.world.list_edges_from(card_id):
                if edge.relationship in {
                    Relationship.MOUNT_READ_ONLY,
                    Relationship.MOUNT_READ_WRITE,
                }:
                    await self._detach_mount(edge, ignore_missing=True)
        if card.type == CardType.AGENT and self.agent_runtime is not None:
            await self.agent_runtime.delete_agent(card.id)
        if card.type == CardType.SANDBOX and self.sandbox_backend is not None:
            try:
                await self.sandbox_backend.destroy(card.id)
            except SandboxNotFoundError:
                pass

        deleted = self.world.delete_card(card_id)
        if record is not None:
            self.resources.remove_file(record)
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
        if self.agent_runtime is None:
            raise RuntimeUnavailableError(
                "agent runtime is not configured; set OPEN_AGENT_WORLD_AGENT_RUNTIME explicitly"
            )
        existing = self._agent_tasks.get(agent_id)
        if existing is not None and not existing.done():
            raise RuntimeUnavailableError(f"agent {agent_id!r} already has an active run")
        stream = self.agent_runtime.run(agent_id, prompt)
        first = await anext(stream)
        await self._publish_agent_event(first)
        task = asyncio.create_task(self._consume_agent_events(agent_id, stream))
        self._agent_tasks[agent_id] = task
        return {"accepted": True, "agent_id": agent_id, "run_id": first.run_id}

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
        await self.events.publish(
            EventType.CONVERSATION_SESSION_CREATED,
            node_id=conversation_id,
            conversation_id=conversation_id,
            session_id=session.id,
            payload={"session": session.model_dump(mode="json")},
        )
        return session

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
            active = self._agent_tasks.get(agent_id)
            if active is not None and not active.done():
                raise RuntimeUnavailableError(
                    f"agent {agent_id!r} already has an active run"
                )
        if mentions and self.agent_runtime is None:
            raise RuntimeUnavailableError("agent runtime is not configured")
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
            assert self.agent_runtime is not None
            stream = self.agent_runtime.run(
                agent_id, prompt, context_id=session_id
            )
            first = await anext(stream)
            await self._publish_agent_event(
                first,
                conversation_id=conversation_id,
                session_id=session_id,
            )
            task = asyncio.create_task(
                self._consume_conversation_events(
                    agent_id, stream, conversation_id, session_id
                )
            )
            self._agent_tasks[agent_id] = task
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
        active = self._agent_tasks.get(target_agent_id)
        if active is not None and not active.done():
            raise RuntimeUnavailableError(
                f"agent {target_agent_id!r} already has an active run"
            )
        if self.agent_runtime is None:
            raise RuntimeUnavailableError("agent runtime is not configured")

        source = self._require_card_type(source_agent_id, CardType.AGENT)
        request_message = self.conversations.add_message(
            conversation_id,
            session_id,
            sender_kind="agent",
            sender_id=source.id,
            sender_name=source.name,
            content=f"Requested @{self.world.get_card(target_agent_id).name}: {message.strip()}",
            mention_agent_ids=[target_agent_id],
        )
        await self._publish_conversation_message(request_message)
        prompt = self._conversation_prompt(
            conversation_id, session, target_agent_id, message
        )
        token = _conversation_turn_depth.set(depth + 1)
        current = asyncio.current_task()
        if current is not None:
            self._agent_tasks[target_agent_id] = current
        final_text = ""
        run_id: str | None = None
        try:
            async for event in self.agent_runtime.run(
                target_agent_id, prompt, context_id=session_id
            ):
                run_id = event.run_id
                await self._publish_agent_event(
                    event,
                    conversation_id=conversation_id,
                    session_id=session_id,
                )
                if event.type.value == "agent_completed":
                    final_text = str(event.payload.get("text", ""))
        finally:
            _conversation_turn_depth.reset(token)
            if self._agent_tasks.get(target_agent_id) is current:
                self._agent_tasks.pop(target_agent_id, None)
        target = self.world.get_card(target_agent_id)
        response = self.conversations.add_message(
            conversation_id,
            session_id,
            sender_kind="agent",
            sender_id=target.id,
            sender_name=target.name,
            content=final_text or "No response was produced.",
            run_id=run_id,
        )
        await self._publish_conversation_message(response)
        return {
            "agent_id": target.id,
            "agent_name": target.name,
            "response": final_text,
        }

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        self._require_card_type(agent_id, CardType.AGENT)
        if self.agent_runtime is None:
            raise RuntimeUnavailableError("agent runtime is not configured")
        await self.agent_runtime.stop(agent_id)
        task = self._agent_tasks.get(agent_id)
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
        return {"agent_id": agent_id, "status": "idle"}

    async def get_agent(self, agent_id: str) -> Any:
        self._require_card_type(agent_id, CardType.AGENT)
        if self.agent_runtime is None:
            raise RuntimeUnavailableError("agent runtime is not configured")
        return await self.agent_runtime.get_agent(agent_id)

    async def communicate_with_agent(
        self, source_agent_id: str, target_agent_id: str, message: str
    ) -> dict[str, str]:
        source = self._require_card_type(source_agent_id, CardType.AGENT)
        target = self._require_card_type(target_agent_id, CardType.AGENT)
        self.capabilities.require_agent_communicate(source_agent_id, target_agent_id)
        if self.agent_runtime is None:
            raise RuntimeUnavailableError("agent runtime is not configured")
        prompt = f"Message from {source.name}:\n\n{message.strip()}"
        final_text = ""
        async for event in self.agent_runtime.run(target_agent_id, prompt):
            await self._publish_agent_event(event)
            text = event.payload.get("text")
            if isinstance(text, str):
                final_text = text
        return {
            "agent_id": target.id,
            "agent_name": target.name,
            "response": final_text,
        }

    async def configure_llm_connection(
        self, *, base_url: str | None, api_key: str | None
    ) -> dict[str, bool]:
        if not isinstance(self.agent_runtime, GoogleAdkAgentRuntime):
            raise RuntimeUnavailableError("ADK agent runtime is not configured")
        self.agent_runtime.configure_litellm_connection(
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

    async def _consume_agent_events(self, agent_id: str, stream: Any) -> None:
        try:
            async for event in stream:
                await self._publish_agent_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.events.publish(
                EventType.RUNTIME_ERROR,
                node_id=agent_id,
                agent_id=agent_id,
                payload={"error": str(exc)},
            )
        finally:
            self._agent_tasks.pop(agent_id, None)

    async def _consume_conversation_events(
        self,
        agent_id: str,
        stream: Any,
        conversation_id: str,
        session_id: str,
    ) -> None:
        final_text = ""
        run_id: str | None = None
        try:
            async for event in stream:
                run_id = event.run_id
                await self._publish_agent_event(
                    event,
                    conversation_id=conversation_id,
                    session_id=session_id,
                )
                if event.type.value == "agent_completed":
                    final_text = str(event.payload.get("text", ""))
            if final_text:
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.events.publish(
                EventType.RUNTIME_ERROR,
                node_id=conversation_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                session_id=session_id,
                payload={"error": str(exc)},
            )
        finally:
            self._agent_tasks.pop(agent_id, None)

    async def _publish_agent_event(
        self,
        event: AgentEvent,
        *,
        conversation_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        event_type = EventType(event.type.value)
        status = event.payload.get("status")
        if event_type is EventType.AGENT_STATUS_CHANGED and isinstance(status, str):
            self.world.update_card(event.agent_id, CardPatch(status=status))
        await self.events.publish(
            event_type,
            node_id=event.agent_id,
            agent_id=event.agent_id,
            conversation_id=conversation_id,
            session_id=session_id,
            payload={
                "run_id": event.run_id,
                **({"conversation_id": conversation_id} if conversation_id else {}),
                **({"session_id": session_id} if session_id else {}),
                **dict(event.payload),
            },
        )

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

    @staticmethod
    def _runtime_agent_config(card: Card) -> RuntimeAgentConfig:
        return RuntimeAgentConfig(
            agent_id=card.id,
            name=card.name,
            system_instruction=str(card.config.get("system_instruction", "")),
            model=str(card.config.get("model", "gemini-3.7-flash")),
        )

    def close(self) -> None:
        self.database.close()


def create_services(
    settings: Settings,
    *,
    agent_runtime: AgentRuntime | None = None,
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
    services = ApplicationServices(
        settings=settings,
        database=database,
        world=world,
        resources=resources,
        capabilities=capabilities,
        events=events,
        plugins=plugin_registry,
        conversations=conversations,
        agent_runtime=agent_runtime,
        sandbox_backend=sandbox_backend,
    )
    if services.sandbox_backend is None and settings.sandbox_runtime == "windows":
        services.sandbox_backend = WindowsSandboxBackend(
            settings.data_root, event_sink=services.publish_sandbox_event
        )
    if services.agent_runtime is None and settings.agent_runtime is not None:
        from backend.capabilities.provider import WorldAgentCapabilityProvider

        provider = WorldAgentCapabilityProvider(services)
        options = {"app_name": "open-agent-world"} if settings.agent_runtime == "google-adk" else {}
        services.agent_runtime = create_agent_runtime(
            settings.agent_runtime, provider, **options
        )
    return services
