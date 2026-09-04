from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.agents import MockAgentRuntime
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.main import create_app
from backend.runs import InvocationCaller, InvocationContext
from backend.runs.manager import _current_invocation
from backend.services import create_services
from backend.conversations import ConversationSessionCreate
from backend.world.models import CardCreate, EdgeCreate


def _create(client: TestClient, card_type: str, name: str) -> dict[str, Any]:
    response = client.post("/api/nodes", json={"type": card_type, "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _connect(client: TestClient, agent_id: str, conversation_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/edges",
        json={
            "source": agent_id,
            "target": conversation_id,
            "relationship": "participate",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_exposes_conversation_field_and_participation(client: TestClient) -> None:
    catalog = client.get("/api/catalog").json()
    conversation = next(item for item in catalog["node_types"] if item["id"] == "conversation")
    participation = next(item for item in catalog["relationships"] if item["id"] == "participate")

    assert conversation["deck_id"] == "fields"
    assert conversation["surfaces"]["workspace"] is True
    assert participation["source_traits"] == ["core.agent"]
    assert participation["target_traits"] == ["core.conversation"]


def test_session_members_must_have_live_conversation_connections(client: TestClient) -> None:
    agent = _create(client, "agent", "Atlas")
    conversation = _create(client, "conversation", "Research room")

    denied = client.post(
        f"/api/conversations/{conversation['id']}/sessions",
        json={"title": "Review", "participant_ids": [agent["id"]]},
    )
    assert denied.status_code == 403

    edge = _connect(client, agent["id"], conversation["id"])
    created = client.post(
        f"/api/conversations/{conversation['id']}/sessions",
        json={"title": "Review", "participant_ids": [agent["id"]]},
    )
    assert created.status_code == 201

    capability = client.get(f"/api/agents/{agent['id']}/capabilities").json()
    assert any(item["kind"] == "conversation.request_turn" for item in capability["capabilities"])

    unavailable = client.post(
        f"/api/conversations/{conversation['id']}/sessions/{created.json()['id']}/messages",
        json={"content": "@Atlas answer", "mention_agent_ids": [agent["id"]]},
    )
    assert unavailable.status_code == 503
    assert client.get(
        f"/api/conversations/{conversation['id']}/sessions/{created.json()['id']}/messages"
    ).json() == []

    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    rejected = client.post(
        f"/api/conversations/{conversation['id']}/sessions/{created.json()['id']}/messages",
        json={"content": "@Atlas answer", "mention_agent_ids": [agent["id"]]},
    )
    assert rejected.status_code == 403
    assert client.get(
        f"/api/conversations/{conversation['id']}/sessions/{created.json()['id']}/messages"
    ).json() == []


def test_conversation_contact_roster_is_not_limited_to_loaded_canvas_chunks(
    client: TestClient,
) -> None:
    agent_response = client.post(
        "/api/nodes",
        json={
            "type": "agent",
            "name": "Far Agent",
            "position": {"x": 5000, "y": 0},
        },
    )
    assert agent_response.status_code == 201
    agent = agent_response.json()
    conversation = _create(client, "conversation", "Local room")
    _connect(client, agent["id"], conversation["id"])

    local_world = client.get("/api/world", params={"chunks": "0:0"}).json()
    assert agent["id"] not in {node["id"] for node in local_world["nodes"]}
    assert [edge["relationship"] for edge in local_world["edges"]] == ["participate"]

    summary = client.get(f"/api/conversations/{conversation['id']}").json()
    assert summary["agents"] == [{
        "id": agent["id"],
        "name": "Far Agent",
        "status": "idle",
        "model": "gemini-3.7-flash",
        "connected": True,
    }]


def test_connected_agents_can_be_added_to_an_existing_session(client: TestClient) -> None:
    atlas = _create(client, "agent", "Atlas")
    river = _create(client, "agent", "River")
    conversation = _create(client, "conversation", "Research room")
    _connect(client, atlas["id"], conversation["id"])
    session = client.post(
        f"/api/conversations/{conversation['id']}/sessions",
        json={"title": "Review", "participant_ids": [atlas["id"]]},
    ).json()

    denied = client.post(
        f"/api/conversations/{conversation['id']}/sessions/{session['id']}/participants",
        json={"participant_ids": [river["id"]]},
    )
    assert denied.status_code == 403

    _connect(client, river["id"], conversation["id"])
    added = client.post(
        f"/api/conversations/{conversation['id']}/sessions/{session['id']}/participants",
        json={"participant_ids": [river["id"]]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["participant_ids"] == [atlas["id"], river["id"]]


def test_group_session_can_kick_members_and_be_dissolved(client: TestClient) -> None:
    atlas = _create(client, "agent", "Atlas")
    river = _create(client, "agent", "River")
    conversation = _create(client, "conversation", "Research room")
    _connect(client, atlas["id"], conversation["id"])
    _connect(client, river["id"], conversation["id"])
    created = client.post(
        f"/api/conversations/{conversation['id']}/sessions",
        json={
            "title": "Review group",
            "participant_ids": [atlas["id"], river["id"]],
        },
    )
    assert created.status_code == 201, created.text
    session = created.json()

    kicked = client.delete(
        f"/api/conversations/{conversation['id']}/sessions/{session['id']}/participants/{river['id']}"
    )
    assert kicked.status_code == 200, kicked.text
    assert kicked.json()["participant_ids"] == [atlas["id"]]

    dissolved = client.delete(
        f"/api/conversations/{conversation['id']}/sessions/{session['id']}"
    )
    assert dissolved.status_code == 204
    summary = client.get(f"/api/conversations/{conversation['id']}").json()
    assert session["id"] not in {item["id"] for item in summary["sessions"]}
    general = next(item for item in summary["sessions"] if item["title"] == "General")
    assert client.delete(
        f"/api/conversations/{conversation['id']}/sessions/{general['id']}"
    ).status_code == 422


def test_addressed_group_message_persists_agent_responses_and_events(data_root: Path) -> None:
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider(
        "core.mock",
        MockAgentRuntime(WorldAgentCapabilityProvider(services)),
        default=True,
    )
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client, client.websocket_connect("/ws/events") as websocket:
            assert websocket.receive_json()["type"] == "connection_ready"
            atlas = _create(client, "agent", "Atlas")
            assert websocket.receive_json()["type"] == "card_created"
            river = _create(client, "agent", "River")
            assert websocket.receive_json()["type"] == "card_created"
            conversation = _create(client, "conversation", "Research room")
            assert websocket.receive_json()["type"] == "card_created"
            _connect(client, atlas["id"], conversation["id"])
            assert websocket.receive_json()["type"] == "edge_created"
            assert websocket.receive_json()["type"] == "permission_changed"
            _connect(client, river["id"], conversation["id"])
            assert websocket.receive_json()["type"] == "edge_created"
            assert websocket.receive_json()["type"] == "permission_changed"

            session_response = client.post(
                f"/api/conversations/{conversation['id']}/sessions",
                json={
                    "title": "Joint review",
                    "participant_ids": [atlas["id"], river["id"]],
                },
            )
            assert session_response.status_code == 201
            session = session_response.json()
            assert websocket.receive_json()["type"] == "conversation_session_created"

            posted = client.post(
                f"/api/conversations/{conversation['id']}/sessions/{session['id']}/messages",
                json={
                    "content": "@Atlas and @River compare the evidence",
                    "mention_agent_ids": [atlas["id"], river["id"]],
                },
            )
            assert posted.status_code == 202, posted.text
            assert set(posted.json()["accepted_agent_ids"]) == {atlas["id"], river["id"]}

            agent_message_ids: set[str] = set()
            started_agent_ids: set[str] = set()
            for _ in range(40):
                event = websocket.receive_json()
                if event["type"] == "agent_started":
                    assert event["conversation_id"] == conversation["id"]
                    assert event["session_id"] == session["id"]
                    started_agent_ids.add(event["agent_id"])
                if event["type"] == "conversation_message" and event.get("agent_id"):
                    agent_message_ids.add(event["agent_id"])
                if (
                    agent_message_ids == {atlas["id"], river["id"]}
                    and started_agent_ids == {atlas["id"], river["id"]}
                ):
                    break
            assert agent_message_ids == {atlas["id"], river["id"]}
            assert started_agent_ids == {atlas["id"], river["id"]}

            messages = client.get(
                f"/api/conversations/{conversation['id']}/sessions/{session['id']}/messages"
            ).json()
            assert [item["sender_kind"] for item in messages] == ["user", "agent", "agent"]
            assert {item["sender_id"] for item in messages[1:]} == {atlas["id"], river["id"]}
            assert all(item["session_id"] == session["id"] for item in messages)
            runs = services._require_run_manager().list_runs()
            assert len(runs) == 2
            assert all(item.caller_kind == "conversation" for item in runs)
            assert all(item.caller_id == conversation["id"] for item in runs)
            assert {item.run_id for item in runs} == {
                item["run_id"] for item in messages[1:]
            }
    finally:
        services.close()


def test_conversation_sessions_and_messages_survive_restart(data_root: Path) -> None:
    settings = Settings.for_data_root(data_root)
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        conversation = _create(first, "conversation", "Durable room")
        general = first.get(f"/api/conversations/{conversation['id']}").json()["sessions"][0]
        posted = first.post(
            f"/api/conversations/{conversation['id']}/sessions/{general['id']}/messages",
            json={"content": "Keep this note", "mention_agent_ids": []},
        )
        assert posted.status_code == 202

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        summary = second.get(f"/api/conversations/{conversation['id']}").json()
        assert [session["id"] for session in summary["sessions"]] == [general["id"]]
        messages = second.get(
            f"/api/conversations/{conversation['id']}/sessions/{general['id']}/messages"
        ).json()
        assert [(item["sender_name"], item["content"]) for item in messages] == [
            ("You", "Keep this note")
        ]


@pytest.mark.asyncio
async def test_agent_can_request_another_participant_turn_with_structured_routing(
    data_root: Path,
) -> None:
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider(
        "core.mock",
        MockAgentRuntime(WorldAgentCapabilityProvider(services)),
        default=True,
    )
    try:
        atlas = await services.create_card(CardCreate(type="agent", name="Atlas"))
        river = await services.create_card(CardCreate(type="agent", name="River"))
        conversation = await services.create_card(
            CardCreate(type="conversation", name="Research room")
        )
        await services.create_edge(EdgeCreate(
            source=atlas.id, target=conversation.id, relationship="participate"
        ))
        await services.create_edge(EdgeCreate(
            source=river.id, target=conversation.id, relationship="participate"
        ))
        session = await services.create_conversation_session(
            conversation.id,
            ConversationSessionCreate(
                title="Handoff", participant_ids=[atlas.id, river.id]
            ),
        )

        response = await services.request_conversation_turn(
            atlas.id,
            conversation.id,
            session.id,
            river.id,
            "Check the evidence",
        )

        assert response["agent_id"] == river.id
        messages = services.list_conversation_messages(conversation.id, session.id)
        assert [(item.sender_id, item.sender_kind) for item in messages] == [
            (atlas.id, "agent"),
            (river.id, "agent"),
        ]
        assert messages[0].mention_agent_ids == [river.id]
        assert response["response"].startswith("Mock response:")
    finally:
        services.close()


@pytest.mark.asyncio
async def test_agent_requesting_own_turn_is_recoverable_and_does_not_recurse(
    data_root: Path,
) -> None:
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider(
        "core.mock",
        MockAgentRuntime(WorldAgentCapabilityProvider(services)),
        default=True,
    )
    try:
        atlas = await services.create_card(CardCreate(type="agent", name="Atlas"))
        conversation = await services.create_card(
            CardCreate(type="conversation", name="Solo room")
        )
        await services.create_edge(EdgeCreate(
            source=atlas.id, target=conversation.id, relationship="participate"
        ))
        session = await services.create_conversation_session(
            conversation.id,
            ConversationSessionCreate(title="Solo", participant_ids=[atlas.id]),
        )

        prompt = services._conversation_prompt(
            conversation.id, session, atlas.id, "Think this through"
        )
        assert f"Current speaker: Atlas ({atlas.id})" in prompt
        assert "Eligible request_turn targets (never use the current speaker id): none" in prompt

        provider = WorldAgentCapabilityProvider(services)
        definitions = await provider.list_tools(atlas.id)
        assert len(definitions) == 1
        assert "Never use your own agent id" in definitions[0].description
        assert [parameter.name for parameter in definitions[0].parameters] == [
            "agent_id", "message"
        ]

        from backend.errors import ResourceValidationError

        with pytest.raises(ResourceValidationError, match="only available during a conversation run"):
            await provider.invoke_tool(
                atlas.id,
                definitions[0].capability_id,
                {"agent_id": atlas.id, "message": "Continue"},
            )

        token = _current_invocation.set(InvocationContext(
            run_id="test-run",
            agent_id=atlas.id,
            parent_run_id=None,
            root_run_id="test-run",
            caller=InvocationCaller("conversation", conversation.id),
            context_id=session.id,
            task_id=None,
            runtime_provider_id="core.mock",
        ))
        try:
            response = await provider.invoke_tool(
                atlas.id,
                definitions[0].capability_id,
                {"agent_id": atlas.id, "message": "Continue"},
            )
        finally:
            _current_invocation.reset(token)

        assert response == {
            "agent_id": atlas.id,
            "agent_name": "Atlas",
            "response": (
                "You already have the current turn. Reply directly instead of "
                "requesting another turn from yourself."
            ),
        }
        assert services.list_conversation_messages(conversation.id, session.id) == []
        assert services._require_run_manager()._runtime_tasks == {}
    finally:
        services.close()


@pytest.mark.asyncio
async def test_agent_handoff_executes_as_a_run_and_persists_the_response(
    data_root: Path,
) -> None:
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider(
        "core.mock",
        MockAgentRuntime(WorldAgentCapabilityProvider(services)),
        default=True,
    )
    try:
        xiaobing = await services.create_card(CardCreate(type="agent", name="xiaobing"))
        atlas = await services.create_card(CardCreate(type="agent", name="Atlas"))
        conversation = await services.create_card(
            CardCreate(type="conversation", name="Research room")
        )
        await services.create_edge(EdgeCreate(
            source=xiaobing.id, target=conversation.id, relationship="participate"
        ))
        await services.create_edge(EdgeCreate(
            source=atlas.id, target=conversation.id, relationship="participate"
        ))
        session = await services.create_conversation_session(
            conversation.id,
            ConversationSessionCreate(
                title="Callback", participant_ids=[xiaobing.id, atlas.id]
            ),
        )
        response = await services.request_conversation_turn(
            atlas.id,
            conversation.id,
            session.id,
            xiaobing.id,
            "小冰你好，请回复确认一下。",
        )

        assert response["agent_id"] == xiaobing.id
        assert response["response"].startswith("Mock response:")
        messages = services.list_conversation_messages(conversation.id, session.id)
        expected_request = ("Atlas", "小冰你好，请回复确认一下。")
        assert (messages[0].sender_name, messages[0].content) == expected_request
        assert [item.sender_name for item in messages] == ["Atlas", "xiaobing"]
        runs = services._require_run_manager().list_runs(agent_id=xiaobing.id)
        assert len(runs) == 1
        assert messages[-1].run_id == runs[0].run_id
    finally:
        services.close()


@pytest.mark.asyncio
async def test_agent_message_to_an_active_ancestor_does_not_start_a_second_run(
    data_root: Path,
) -> None:
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider(
        "core.mock",
        MockAgentRuntime(WorldAgentCapabilityProvider(services)),
        default=True,
    )
    try:
        atlas = await services.create_card(CardCreate(type="agent", name="Atlas"))
        xiaobing = await services.create_card(CardCreate(type="agent", name="xiaobing"))
        await services.create_edge(EdgeCreate(
            source=xiaobing.id, target=atlas.id, relationship="communicate"
        ))
        token = _current_invocation.set(InvocationContext(
            run_id="atlas-run",
            agent_id=atlas.id,
            parent_run_id=None,
            root_run_id="atlas-run",
            caller=InvocationCaller("user"),
            context_id=None,
            task_id=None,
            runtime_provider_id="core.mock",
        ))
        try:
            response = await services.communicate_with_agent(
                xiaobing.id, atlas.id, "What is the session id?"
            )
        finally:
            _current_invocation.reset(token)

        assert response["agent_id"] == atlas.id
        assert "already active earlier" in response["response"]
        assert services._require_run_manager().list_runs(agent_id=atlas.id) == []
    finally:
        services.close()


class _ScriptedProvider:
    """Minimal RuntimeProvider double with a scripted execution outcome."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.configs: dict[str, Any] = {}

    async def create_agent(self, config: Any) -> Any:
        self.configs[config.agent_id] = config

    async def update_agent(self, config: Any) -> Any:
        self.configs[config.agent_id] = config

    async def delete_agent(self, agent_id: str) -> None:
        self.configs.pop(agent_id, None)

    async def execute(self, config: Any, context: Any, runtime_input: Any):
        from backend.agents import AgentEvent, AgentEventType
        from backend.runs import RunStatus

        if self.mode == "failure":
            raise RuntimeError("model endpoint rejected the request")
        if self.mode == "empty":
            yield AgentEvent(
                context.agent_id,
                context.run_id,
                AgentEventType.COMPLETED,
                {"text": ""},
                run_status=RunStatus.SUCCEEDED,
            )
        # "waiting": end the stream without a terminal status.

    async def stop(self, run_id: str) -> None:
        del run_id

    async def get_agent(self, agent_id: str) -> Any:
        raise NotImplementedError


def _scripted_conversation_services(data_root: Path, mode: str):
    from backend.agents import RuntimeProvider
    from backend.plugins import create_builtin_registry
    from backend.tests.plugin_support import install_test_plugin

    provider = _ScriptedProvider(mode)
    RuntimeProvider.register(_ScriptedProvider)
    registry = create_builtin_registry()
    install_test_plugin(
        registry,
        "test.scripted-runtime",
        lambda registration: registration.register_runtime_provider(
            "test.scripted", lambda capabilities: provider
        ),
    )
    settings = Settings.for_data_root(data_root)
    services = create_services(settings, plugins=registry)
    services.install_runtime_provider("test.scripted", provider, default=True)
    return services


async def _post_and_collect_outcome(services: Any) -> list[Any]:
    from backend.conversations import ConversationPost

    atlas = await services.create_card(CardCreate(type="agent", name="Atlas"))
    conversation = await services.create_card(
        CardCreate(type="conversation", name="Reliability room")
    )
    await services.create_edge(EdgeCreate(
        source=atlas.id, target=conversation.id, relationship="participate"
    ))
    session = await services.create_conversation_session(
        conversation.id,
        ConversationSessionCreate(title="Outcomes", participant_ids=[atlas.id]),
    )
    await services.post_conversation_message(
        conversation.id,
        session.id,
        ConversationPost(content="@Atlas report status", mention_agent_ids=[atlas.id]),
    )
    for _ in range(100):
        messages = services.list_conversation_messages(conversation.id, session.id)
        if len(messages) >= 2:
            return messages
        await asyncio.sleep(0.02)
    return services.list_conversation_messages(conversation.id, session.id)


@pytest.mark.asyncio
async def test_failed_conversation_run_persists_a_system_notice(
    data_root: Path,
) -> None:
    services = _scripted_conversation_services(data_root, "failure")
    try:
        messages = await _post_and_collect_outcome(services)
        assert [item.sender_kind for item in messages] == ["user", "system"]
        assert "Atlas could not respond" in messages[-1].content
        assert "model endpoint rejected the request" in messages[-1].content
        assert messages[-1].run_id is not None
    finally:
        services.close()


@pytest.mark.asyncio
async def test_empty_successful_conversation_run_persists_a_system_notice(
    data_root: Path,
) -> None:
    services = _scripted_conversation_services(data_root, "empty")
    try:
        messages = await _post_and_collect_outcome(services)
        assert [item.sender_kind for item in messages] == ["user", "system"]
        assert "finished without producing a response" in messages[-1].content
    finally:
        services.close()


@pytest.mark.asyncio
async def test_suspended_conversation_run_surfaces_a_wait_notice(
    data_root: Path,
) -> None:
    services = _scripted_conversation_services(data_root, "waiting")
    try:
        messages = await _post_and_collect_outcome(services)
        assert [item.sender_kind for item in messages] == ["user", "system"]
        assert "paused this response to wait on external work" in messages[-1].content
    finally:
        services.close()


@pytest.mark.asyncio
async def test_synchronous_handoff_to_suspending_agent_fails_clearly(
    data_root: Path,
) -> None:
    services = _scripted_conversation_services(data_root, "waiting")
    try:
        atlas = await services.create_card(CardCreate(type="agent", name="Atlas"))
        river = await services.create_card(CardCreate(type="agent", name="River"))
        conversation = await services.create_card(
            CardCreate(type="conversation", name="Handoff room")
        )
        for agent in (atlas, river):
            await services.create_edge(EdgeCreate(
                source=agent.id, target=conversation.id, relationship="participate"
            ))
        session = await services.create_conversation_session(
            conversation.id,
            ConversationSessionCreate(
                title="Handoff", participant_ids=[atlas.id, river.id]
            ),
        )
        from backend.errors import RuntimeUnavailableError
        from backend.runs import RunStatus

        with pytest.raises(RuntimeUnavailableError, match="suspended its run"):
            await services.request_conversation_turn(
                atlas.id, conversation.id, session.id, river.id, "Take over"
            )
        manager = services._require_run_manager()
        delegated = [run for run in manager.list_runs(agent_id=river.id)]
        assert delegated and all(
            run.status is RunStatus.CANCELLED for run in delegated
        )
    finally:
        services.close()
