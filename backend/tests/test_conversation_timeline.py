from pathlib import Path
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from backend.agents import MockAgentRuntime, AgentEvent
from backend.agents.models import AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.conversations import ConversationSessionCreate, ConversationPost
from backend.conversations.store import ConversationStore
from backend.main import create_app
from backend.services import create_services
from backend.world.models import CardCreate, EdgeCreate
from backend.tests.test_conversations import _create
from backend.tests.conversation_helpers import wait_for_conversation_delivery


def test_interrupted_stream_recovery_metadata_survives_timeline_summary():
    row = {
        "run_id": "run-1", "agent_id": "agent-1", "status": "failed",
        "started_at": None, "finished_at": None,
        "lifecycle_json": json.dumps({
            "progress": "provider stream interrupted",
            "recovery_kind": "model_stream_interrupted",
            "confirmed_tool_count": 2,
        }),
    }
    summary = ConversationStore._run_summary(row)
    assert summary.recovery_kind == "model_stream_interrupted"
    assert summary.confirmed_tool_count == 2
    assert summary.progress == "provider stream interrupted"


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
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS, {
                "kind": "model_stream_interrupted", "text": "model connection interrupted",
                "completed_tool_count": 1,
            })
            raise RuntimeError("test endpoint failed after output")
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status="succeeded")


@pytest.mark.asyncio
async def test_explicit_empty_completion_does_not_promote_progress_to_final_reply(data_root: Path):
    services = create_services(Settings.for_data_root(data_root))

    class NoReply(MockAgentRuntime):
        async def execute(self, config, context, runtime_input):
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                {'text': 'Preparing the remaining steps.', 'final': False})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                {'text': ''}, run_status='succeeded')

    services.install_runtime_provider('core.mock', NoReply(WorldAgentCapabilityProvider(services)), default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Atlas'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        await services.post_conversation_message(room.id, session.id, ConversationPost(content='Start', mention_agent_ids=[agent.id]))
        await wait_for_conversation_delivery(services, room.id, session.id)
        page = services.conversations.page_messages(room.id, session.id)
        assert [m.sender_kind for m in page.items] == ['user', 'agent', 'system']
        assert page.items[1].content == 'Preparing the remaining steps.'
        assert 'finished without producing a response' in page.items[-1].content
        run_id = page.items[-1].run_id
        assert page.run_summaries[run_id].final_message_id is None
        assert services.run_manager.final_text(run_id) == ''
    finally:
        await services.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [2, 28])
async def test_public_activity_checkpoint_restores_boundaries_and_reports_truncation(data_root: Path, count: int):
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)

    class PublicActivity(MockAgentRuntime):
        async def execute(self, config, context, runtime_input):
            for index in range(count):
                for kind in (AgentEventType.TOOL_STARTED, AgentEventType.TOOL_COMPLETED):
                    yield AgentEvent(context.agent_id, context.run_id, kind,
                        {'name': 'oaw_invoke_tool', 'call_id': f'call-{index}',
                         **({'arguments': {'capability_id': 'operation:read_file', 'arguments': {'path': str(index)}}}
                            if kind is AgentEventType.TOOL_STARTED else {'response': {'content': f'file-{index}'}})})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS,
                    {'kind': 'reasoning_summary', 'text': f'Checked file {index}', 'provider_message_id': f'progress-{index}'})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS,
                {'kind': 'raw_reasoning', 'text': 'private fixture content'})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                {'text': 'Draft reply', 'provider_message_id': 'answer'})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                {'text': 'Done'}, run_status='succeeded')

    services.install_runtime_provider('core.mock', PublicActivity(WorldAgentCapabilityProvider(services)), default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Atlas'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        await services.post_conversation_message(room.id, session.id, ConversationPost(content='Start', mention_agent_ids=[agent.id]))
        await wait_for_conversation_delivery(services, room.id, session.id)
        page = services.conversations.page_messages(room.id, session.id)
        summary = page.run_summaries[page.items[-1].run_id]
        assert summary.tool_count == count
        assert summary.final_message_id == page.items[-1].id
        assert len(summary.tool_trace) == min(count * 3, 50)
        assert summary.tool_trace_truncated is (count * 3 > 50)
        assert summary.tool_trace[-1]['text'] == f'Checked file {count - 1}'
        completion = summary.tool_trace[-2]
        assert completion['tool_name'] == 'read_file'
        assert completion['capability_id'] == 'operation:read_file'
        assert 'response' not in completion and 'arguments' not in completion
        from backend.runs.history import RunActivityHistory
        archive = RunActivityHistory(services.database, summary.run_id)
        assert archive.get(completion['activity_id'])['response'] == {'content': f'file-{count - 1}'}
        assert archive.page(limit=100)['total'] == count * 2
        assert not archive.page(limit=100)['incomplete']
        assert all(item.get('kind') != 'raw_reasoning' for item in summary.tool_trace)
        assert all(item['id'] and item['timestamp'] for item in summary.tool_trace)
        assert [item.content for item in page.items] == ['Start', 'Draft reply', 'Done']
    finally:
        await services.shutdown()
        services.close()
    restored = create_services(settings)
    try:
        assert restored.conversations.page_messages(room.id, session.id).run_summaries[summary.run_id] == summary
        assert RunActivityHistory(restored.database, summary.run_id).page(limit=100)['total'] == count * 2
    finally:
        restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_provider_messages_and_terminal_history_are_durable(data_root: Path, fail: bool):
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
        await wait_for_conversation_delivery(services, room.id, session.id)
        history = services.conversations.list_messages(room.id, session.id)
        page = services.conversations.page_messages(room.id, session.id)
        timeline = page.items
        assert timeline[0].content == 'Start'
        assert len(timeline) == (5 if fail else 4)
        assert [m.content for m in timeline[1:4]] == ['Checking', 'Checking', 'Done']
        assert all(m.is_final for m in timeline)
        assert not any(m.kind.startswith('tool_') for m in timeline)
        assert history[-1].id == timeline[-1].id
        if fail:
            assert timeline[-1].sender_kind == 'system' and 'could not respond' in timeline[-1].content
            summary = page.run_summaries[timeline[-1].run_id]
            assert summary.recovery_kind == 'model_stream_interrupted'
            assert summary.confirmed_tool_count == 1
            assert page.run_summaries[timeline[-1].run_id].final_message_id is None
        else:
            assert timeline[-1].sender_kind == 'agent' and timeline[-1].content == 'Done'
            summary = page.run_summaries[timeline[-1].run_id]
            assert summary.final_message_id == timeline[-1].id
            assert summary.tool_count == 1
            assert [item['name'] for item in summary.tool_trace] == ['read', 'read']
        with services.database.locked() as db:
            # Text remains chronological; tool details stay in the Run trace.
            assert db.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE session_id=?",
                (session.id,),
            ).fetchone()[0] == len(timeline)
        ids = [m.id for m in timeline]
    finally:
        await services.shutdown()
        services.close()
    restored = create_services(settings)
    try:
        restored_page = restored.conversations.page_messages(room.id, session.id)
        assert [m.id for m in restored_page.items] == ids
        if not fail:
            assert restored_page.run_summaries[summary.run_id].final_message_id == ids[-1]
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
