"""Private delegation and shared conversation handoffs have different inputs."""
from types import SimpleNamespace

import pytest

from backend.agents import AgentEvent, AgentEventType, MockAgentRuntime
from backend.agents.context import ContextBudget, ManagedContext, encoded
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.conversations import ConversationPost, ConversationSessionCreate
from backend.runs import RunStatus
from backend.services import create_services
from backend.tests.conversation_helpers import wait_for_conversation_delivery
from backend.world.models import CardCreate, EdgeCreate


class DelegationRuntime(MockAgentRuntime):
    def __init__(self, services, *, shared):
        self.provider = WorldAgentCapabilityProvider(services)
        super().__init__(self.provider)
        self.services, self.shared = services, shared
        self.observed = {}

    async def execute(self, config, context, runtime_input):
        if context.agent_id == self.source.id:
            if self.shared:
                await self.services.request_conversation_turn(
                    self.source.id, self.room.id, self.session.id,
                    self.helper.id, "Inspect only the assigned sample")
            else:
                await self.services.communicate_with_agent(
                    self.source.id, self.helper.id, "Inspect only the assigned sample")
        else:
            managed = ManagedContext(
                self.services.contexts, context.agent_id, context.context_id,
                context.run_id, SimpleNamespace(model="test-model"), runtime_input.prompt,
                budget=ContextBudget(8192, 1024))
            self.observed = {
                "context": context,
                "model_input": encoded(managed.rendered()),
                "pending": await self.provider.pending_conversation_input(context.agent_id, context.run_id),
                "files": await self.provider.conversation_attachments(context.agent_id, context.run_id),
                "input_scope": self.provider._conversation_input_scope(context.agent_id, context.run_id),
            }
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                         {"text": "Done", "final": True})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                         {"text": "Done"}, run_status=RunStatus.SUCCEEDED)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True], ids=["private-message", "shared-handoff"])
async def test_delegation_preserves_the_selected_conversation_boundary(data_root, shared):
    services = create_services(Settings.for_data_root(data_root))
    runtime = DelegationRuntime(services, shared=shared)
    services.install_runtime_provider("core.mock", runtime, default=True)
    try:
        runtime.source = await services.create_card(CardCreate(type="agent", name="Source"))
        runtime.helper = await services.create_card(CardCreate(type="agent", name="Helper"))
        runtime.room = await services.create_card(CardCreate(type="conversation", name="Private room"))
        await services.create_edge(EdgeCreate(source=runtime.source.id, target=runtime.room.id,
                                               relationship="participate"))
        await services.create_edge(EdgeCreate(source=runtime.source.id, target=runtime.helper.id,
                                               relationship="communicate"))
        participants = [runtime.source.id]
        if shared:
            participants.append(runtime.helper.id)
            await services.create_edge(EdgeCreate(source=runtime.helper.id, target=runtime.room.id,
                                                   relationship="participate"))
        runtime.session = await services.create_conversation_session(runtime.room.id,
            ConversationSessionCreate(title="Research", participant_ids=participants))
        services.conversations.add_message(runtime.room.id, runtime.session.id,
            sender_kind="user", sender_id=None, sender_name="You", content="Parent-only secret: cobalt-731")
        await services.post_conversation_message(runtime.room.id, runtime.session.id,
            ConversationPost(content="Ask the helper", mention_agent_ids=[runtime.source.id]))
        await wait_for_conversation_delivery(services, runtime.room.id, runtime.session.id)

        assert runtime.observed, [item.model_dump() for item in services.run_manager.list_runs()]
        child = runtime.observed["context"]
        child_run = services.run_manager.get_run(child.run_id)
        parent_run = services.run_manager.get_run(child.parent_run_id)
        assert child.root_run_id == parent_run.root_run_id
        assert child_run.lifecycle["cancellation_policy"] == "dependent"
        assert services.run_manager._conversation_scope(child_run) == (runtime.room.id, runtime.session.id)
        assert "Inspect only the assigned sample" in runtime.observed["model_input"]
        assert set(services.conversations.get_session(runtime.room.id, runtime.session.id).participant_ids) == set(participants)
        if shared:
            assert child.context_id == runtime.session.id
            assert "cobalt-731" in runtime.observed["model_input"]
            assert runtime.observed["input_scope"] == (runtime.room.id, runtime.session.id)
        else:
            assert child.context_id.startswith("communicate:")
            assert "cobalt-731" not in runtime.observed["model_input"]
            assert runtime.observed["pending"] == runtime.observed["files"] == []
            assert runtime.observed["input_scope"] is None
            assert not services.contexts.load(runtime.helper.id, runtime.session.id).initialized
    finally:
        services.close()
