import pytest

from backend.agents import MockAgentRuntime, AgentEvent, AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.conversations import ConversationSessionCreate, ConversationPost
from backend.services import create_services
from backend.tests.conversation_helpers import wait_for_conversation_delivery
from backend.world.models import CardCreate, EdgeCreate


class StreamingRuntime(MockAgentRuntime):
    async def execute(self, config, context, runtime_input):
        for key, text in [('first', 'Checking'), ('first', 'Checking resources'),
                          ('first', 'Checking resources.'), ('first', 'Checking resources.'),
                          ('second', 'Checking resources.')]:
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                             {'text': text, 'provider_message_id': key})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status='succeeded')


@pytest.mark.asyncio
async def test_stream_snapshots_produce_one_durable_final_message_per_run(data_root):
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider('core.mock', StreamingRuntime(WorldAgentCapabilityProvider(services)), default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Writer'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        for turn in range(2):
            await services.post_conversation_message(room.id, session.id, ConversationPost(content=f'Start {turn}', mention_agent_ids=[agent.id]))
            await wait_for_conversation_delivery(services, room.id, session.id)
            timeline = services.conversations.page_messages(room.id, session.id).items
            # Provider snapshots, even with different provider message IDs,
            # stay in live Run state; each turn persists only its final answer.
            assert len(timeline) == (turn + 1) * 2
            assert timeline[-1].sender_kind == 'agent'
            assert timeline[-1].content == 'Checking resources.'
            assert timeline[-1].is_final
        assert len({m.id for m in timeline}) == 4
        assert timeline[1].run_id != timeline[3].run_id
        assert [m.sequence for m in timeline] == list(range(1, 5))
        ids = [m.id for m in timeline]
    finally:
        await services.shutdown()
        services.close()
    restored = create_services(settings)
    try:
        assert [m.id for m in restored.conversations.page_messages(room.id, session.id).items] == ids
    finally:
        restored.close()
