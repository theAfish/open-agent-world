"""Continuations preserve native task identity, scope and dispatch facts."""
from dataclasses import replace

from fastapi.testclient import TestClient

from backend.agents import MockAgentRuntime
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.conversations.continuation import original_task
from backend.main import create_app
from backend.runs.models import RunStatus
from backend.services import create_services
from backend.tests.conversation_helpers import wait_for_conversation_delivery
from backend.tests.test_conversations import _create, _connect


class CapturingRuntime(MockAgentRuntime):
    def __init__(self, provider):
        super().__init__(provider)
        self.inputs = []
        self.contexts = []

    async def execute(self, config, context, runtime_input):
        self.inputs.append(runtime_input)
        self.contexts.append(context)
        if len(self.inputs) == 1:
            raise RuntimeError("fixture model response ended before dispatch")
        async for event in super().execute(config, context, runtime_input):
            yield event


def test_explicit_continuation_retains_task_and_uses_native_recovery_facts(data_root):
    settings = replace(Settings.for_data_root(data_root), agent_runtime="core.mock")
    services = create_services(settings)
    runtime = CapturingRuntime(WorldAgentCapabilityProvider(services))
    services.install_runtime_provider("core.mock", runtime, default=True)
    with TestClient(create_app(settings, services=services)) as client:
        agent = _create(client, "agent", "Atlas")
        room = _create(client, "conversation", "Room")
        _connect(client, agent['id'], room['id'])
        session = client.post(f"/api/conversations/{room['id']}/sessions", json={
            "participant_ids": [agent['id']]}).json()
        base = f"/api/conversations/{room['id']}/sessions/{session['id']}"
        task = "Build the requested structure with dimensions 10 x 20 and keep its open ends."
        def post(content, **extra):
            return client.post(base + '/messages', json={"content": content,
                "mention_agent_ids": [agent['id']], **extra})
        assert post(task).status_code == 202
        client.portal.call(wait_for_conversation_delivery, services, room['id'], session['id'])
        source = services.run_manager.list_runs(agent_id=agent['id'])[0]
        assert source.status == RunStatus.FAILED
        assert runtime.inputs[0].task_prompt == task
        resumed = post('Continue remaining work.', continuation_run_id=source.run_id)
        assert resumed.status_code == 202, resumed.text
        assert resumed.json()['message']['continuation_run_id'] == source.run_id
        client.portal.call(wait_for_conversation_delivery, services, room['id'], session['id'])
        assert runtime.inputs[1].task_prompt == task
        assert runtime.contexts[1].extensions['continuation_run_id'] == source.run_id
        continued = services.run_manager.get_run(runtime.contexts[1].run_id)
        assert continued.lifecycle['continuation_run_id'] == source.run_id
        assert task in runtime.inputs[1].prompt and 'Continue remaining work.' in runtime.inputs[1].prompt
        assert '"native_tool_receipt_count": 0' in runtime.inputs[1].prompt
        assert '"recovery_classification": "read_only"' in runtime.inputs[1].prompt
        assert source.run_id in runtime.inputs[1].prompt
        assert post('An unrelated new task').status_code == 202
        client.portal.call(wait_for_conversation_delivery, services, room['id'], session['id'])
        assert runtime.inputs[2].task_prompt == 'An unrelated new task'
        assert runtime.contexts[2].extensions['continuation_run_id'] is None
        # Old Runs recover from delivered user messages, not the enriched input.
        scope = services.state.get_scope('run', source.run_id)
        services.state.delete(scope, 'task_prompt')
        assert original_task(services, source.run_id, room['id'], session['id']) == task
        other = client.post(f"/api/conversations/{room['id']}/sessions", json={"participant_ids": [agent['id']]}).json()
        denied = client.post(f"/api/conversations/{room['id']}/sessions/{other['id']}/messages", json={
            "content": "Continue", "mention_agent_ids": [agent['id']], "continuation_run_id": source.run_id})
        assert denied.status_code == 422
        # A successful Run cannot be relabelled as a failed continuation.
        successful = next(r for r in services.run_manager.list_runs(agent_id=agent['id']) if r.status == RunStatus.SUCCEEDED)
        assert post('Continue', continuation_run_id=successful.run_id).status_code == 422
        assert post('Continue', continuation_run_id=source.run_id, mention_agent_ids=[]).status_code == 422
        messages = client.get(base + '/messages').json()
        assert any(m.get('continuation_run_id') == source.run_id for m in messages)

    restored = create_services(settings)
    with TestClient(create_app(settings, services=restored)) as client:
        messages = client.get(base + '/messages').json()
        assert any(m.get('continuation_run_id') == source.run_id for m in messages)
        assert original_task(restored, source.run_id, room['id'], session['id']) == task
