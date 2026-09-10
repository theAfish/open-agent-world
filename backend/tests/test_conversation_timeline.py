from pathlib import Path
import asyncio

import pytest
from fastapi.testclient import TestClient
from backend.agents import MockAgentRuntime, AgentEvent
from backend.agents.models import AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.conversations import ConversationSessionCreate, ConversationPost
from backend.main import create_app
from backend.services import create_services
from backend.world.models import CardCreate, EdgeCreate
from backend.tests.test_conversations import _create


def test_groups_sessions_naming_and_bidirectional_pages_survive_restart(data_root: Path):
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        room = _create(client, "conversation", "Room")
        base = f"/api/conversations/{room['id']}"
        first = client.post(base + "/sessions", json={"group_title": "Research"}).json()
        second = client.post(base + "/sessions", json={"group_id": first['group_id']}).json()
        assert first['group_id'] == second['group_id'] and first['id'] != second['id']
        prefix = base + f"/sessions/{first['id']}"
        for number in range(163):
            assert client.post(prefix + '/messages', json={"content": f"Topic {number}"}).status_code == 202
        sessions = client.get(base).json()['sessions']
        assert next(s for s in sessions if s['id'] == first['id'])['title'] == 'Topic 0'
        assert client.patch(prefix, json={"title": "Review"}).status_code == 200
        assert client.patch(prefix, json={"title": "   "}).status_code == 422
        latest = client.get(prefix + '/timeline').json()
        assert len(latest['items']) == 50 and latest['has_before'] and not latest['has_after']
        ids = [m['id'] for m in latest['items']]
        cursor = latest['items'][0]['sequence']
        older = client.get(prefix + f'/timeline?before={cursor}').json()
        assert older['has_after'] and not set(ids).intersection(m['id'] for m in older['items'])
        newer = client.get(prefix + f"/timeline?after={older['items'][-1]['sequence']}").json()
        assert [m['id'] for m in newer['items']] == ids
        assert client.get(prefix + '/timeline?before=2&after=1').status_code == 422
        # Scoped group and cursor lookups cannot read another conversation.
        other = _create(client, "conversation", "Other")
        assert client.post(f"/api/conversations/{other['id']}/sessions", json={"group_id": first['group_id']}).status_code == 404
        assert client.get(f"/api/conversations/{other['id']}/sessions/{first['id']}/timeline").status_code == 404
    with TestClient(create_app(settings)) as client:
        assert [m['id'] for m in client.get(prefix + '/timeline').json()['items']] == ids
        restored = client.get(base).json()['sessions']
        assert next(s for s in restored if s['id'] == first['id'])['title'] == 'Review'
        assert next(s for s in restored if s['id'] == second['id'])['group_id'] == first['group_id']


class TranscriptRuntime(MockAgentRuntime):
    fail = False

    async def execute(self, config, context, runtime_input):
        for kind, payload in [
            (AgentEventType.MESSAGE, {"text": "Checking"}),
            (AgentEventType.TOOL_STARTED, {"name": "read", "arguments": {"path": "notes"}}),
            (AgentEventType.TOOL_COMPLETED, {"name": "read", "response": {"content": "notes"}}),
            (AgentEventType.MESSAGE, {"text": "Checking"}),
            (AgentEventType.MESSAGE, {"text": "Done"}),
        ]:
            yield AgentEvent(context.agent_id, context.run_id, kind, payload)
        if self.fail:
            raise RuntimeError("test endpoint failed after output")
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status="succeeded")


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_provider_timeline_is_durable_without_subscribers_and_final_identity_is_stable(data_root: Path, fail: bool):
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    runtime = TranscriptRuntime(WorldAgentCapabilityProvider(services))
    runtime.fail = fail
    services.install_runtime_provider('core.mock', runtime, default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Atlas'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        await services.post_conversation_message(room.id, session.id, ConversationPost(content='Start', mention_agent_ids=[agent.id]))
        for _ in range(100):
            await asyncio.sleep(0.01)
            history = services.conversations.list_messages(room.id, session.id)
            if len(history) == 2:
                break
        timeline = services.conversations.page_messages(room.id, session.id).items
        assert [m.content.split('\n')[0] for m in timeline[:6]] == ['Start', 'Checking', 'Using read', 'Finished read', 'Checking', 'Done']
        assert [m.sequence for m in timeline] == list(range(1, len(timeline) + 1))
        assert history[-1].id == timeline[-1].id
        if fail:
            assert timeline[-1].sender_kind == 'system' and 'failed' in timeline[-1].content
            assert not timeline[5].is_final
        else:
            assert len(timeline) == 6
        assert sum(m.is_final for m in timeline) == 2
        ids = [m.id for m in timeline]
    finally:
        await services.shutdown()
        services.close()
    restored = create_services(settings)
    try:
        assert [m.id for m in restored.conversations.page_messages(room.id, session.id).items] == ids
    finally:
        restored.close()


def test_legacy_schema_migrates_without_replacing_ids_or_history(data_root: Path):
    import sqlite3
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        room = _create(client, "conversation", "Legacy")
        base = f"/api/conversations/{room['id']}"
        session = client.get(base).json()['sessions'][0]
        prefix = base + f"/sessions/{session['id']}"
        saved = client.post(prefix + '/messages', json={'content': 'Legacy note'}).json()['message']
    with sqlite3.connect(settings.database_path) as db:
        db.execute('DROP INDEX conversation_message_sequence_idx')
        db.execute('DROP INDEX conversation_session_group_idx')
        for column in ('sequence', 'kind', 'is_final'):
            db.execute(f'ALTER TABLE conversation_messages DROP COLUMN {column}')
        for column in ('group_id', 'auto_title', 'is_default'):
            db.execute(f'ALTER TABLE conversation_sessions DROP COLUMN {column}')
        db.execute('DROP TABLE conversation_groups')
    with TestClient(create_app(settings)) as client:
        restored = client.get(base).json()['sessions'][0]
        assert restored['id'] == session['id'] and restored['group_id'] == session['id']
        page = client.get(prefix + '/timeline').json()
        assert page['items'][0]['id'] == saved['id'] and page['items'][0]['sequence'] == 1
        assert client.patch(prefix, json={'title': 'Renamed default'}).status_code == 200
        assert client.delete(prefix).status_code == 422
        custom = client.post(base + '/sessions', json={'title': 'General'}).json()
        assert client.delete(base + f"/sessions/{custom['id']}").status_code == 204


def test_client_message_id_is_preserved_and_cannot_replace_an_existing_message(client):
    from uuid import uuid4
    room = _create(client, 'conversation', 'Immediate sends')
    base = f"/api/conversations/{room['id']}"
    session = client.get(base).json()['sessions'][0]
    prefix = base + f"/sessions/{session['id']}"
    message_id = str(uuid4())
    sent = client.post(prefix + '/messages', json={'content': 'Hello', 'message_id': message_id})
    assert sent.status_code == 202 and sent.json()['message']['id'] == message_id
    assert client.get(prefix + '/timeline').json()['items'][0]['id'] == message_id
    repeated = client.post(prefix + '/messages', json={'content': 'Replacement', 'message_id': message_id})
    assert repeated.status_code == 409
    assert client.get(prefix + '/timeline').json()['items'][0]['content'] == 'Hello'
    assert client.post(prefix + '/messages', json={'content': 'Bad ID', 'message_id': 'not-a-uuid'}).status_code == 422
