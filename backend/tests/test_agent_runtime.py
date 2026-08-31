from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agents import (
    AgentConfig,
    AgentEventType,
    GoogleAdkAgentRuntime,
    MockAgentRuntime,
    ScopedToolDefinition,
    ToolParameter,
)
from backend.agents.tools import build_scoped_tool_callables
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.main import create_app
from backend.services import create_services


class MutableCapabilityProvider:
    def __init__(self) -> None:
        self.allowed = True
        self.invocations: list[tuple[str, str, dict[str, Any]]] = []
        self.definition = ScopedToolDefinition(
            capability_id="text.edit:notes",
            name="replace_notes",
            description="Replace the connected notes resource.",
            parameters=(ToolParameter("content", str, "Complete replacement text."),),
        )

    async def list_tools(self, agent_id: str) -> Sequence[ScopedToolDefinition]:
        del agent_id
        return (self.definition,) if self.allowed else ()

    async def invoke_tool(
        self, agent_id: str, capability_id: str, arguments: Mapping[str, Any]
    ) -> Any:
        if not self.allowed or capability_id != self.definition.capability_id:
            raise PermissionError("capability was revoked")
        values = dict(arguments)
        self.invocations.append((agent_id, capability_id, values))
        return {"content": values["content"]}


def test_google_adk_is_the_default_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_AGENT_WORLD_AGENT_RUNTIME", raising=False)
    assert Settings.from_environment().agent_runtime == "google-adk"


def test_litellm_runtime_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_AGENT_WORLD_AGENT_RUNTIME", "litellm")
    with pytest.raises(ValueError, match="google-adk.*mock"):
        Settings.from_environment()


def test_adk_litellm_connection_settings_stay_runtime_only(data_root: Path) -> None:
    settings = Settings.for_data_root(data_root)
    runtime = GoogleAdkAgentRuntime(MutableCapabilityProvider())
    services = create_services(settings, agent_runtime=runtime)
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client:
            response = client.put(
                "/api/settings/llm",
                json={"base_url": "https://llmapi.paratera.com", "api_key": "session-secret"},
            )
            assert response.status_code == 200
            assert response.json() == {"configured": True}
            assert runtime._litellm_connection == {
                "api_base": "https://llmapi.paratera.com",
                "api_key": "session-secret",
            }
            model = runtime._adk_model("openai/gpt-4o-mini")
            assert type(model).__name__ == "LiteLlm"
            assert model._additional_args == runtime._litellm_connection
            assert "session-secret" not in response.text
    finally:
        services.close()


def test_adk_resolves_provider_qualified_models_through_litellm() -> None:
    from google.adk.models import LLMRegistry

    model = LLMRegistry.new_llm("openai/gpt-4o-mini")

    assert type(model).__name__ == "LiteLlm"
    assert model.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_scoped_tool_rechecks_provider_after_revocation() -> None:
    provider = MutableCapabilityProvider()
    tool = build_scoped_tool_callables(
        provider, "agent-1", (provider.definition,)
    )[0]

    assert await tool(content="authorized") == {"content": "authorized"}
    provider.allowed = False
    with pytest.raises(PermissionError, match="revoked"):
        await tool(content="must fail")


@pytest.mark.asyncio
async def test_mock_runtime_is_explicit_and_rebuilds_tools_each_run() -> None:
    provider = MutableCapabilityProvider()
    runtime = MockAgentRuntime(provider)
    await runtime.create_agent(AgentConfig(agent_id="agent-1", name="Atlas"))

    first = [event async for event in runtime.run("agent-1", "inspect notes")]
    message = next(event for event in first if event.type is AgentEventType.MESSAGE)
    assert message.payload["available_tools"] == ["replace_notes"]
    assert first[-1].payload["status"] == "idle"

    provider.allowed = False
    second = [event async for event in runtime.run("agent-1", "try again")]
    message = next(event for event in second if event.type is AgentEventType.MESSAGE)
    assert message.payload["available_tools"] == []


def _receive_run_events(websocket: Any, run_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(20):
        event = websocket.receive_json()
        if event.get("payload", {}).get("run_id") != run_id:
            continue
        events.append(event)
        if event["type"] == "agent_completed":
            return events
    raise AssertionError("agent run did not complete within the expected event window")


def _receive_until(websocket: Any, expected: set[str]) -> list[str]:
    received: list[str] = []
    for _ in range(12):
        event_type = websocket.receive_json()["type"]
        received.append(event_type)
        if expected.issubset(received):
            return received
    raise AssertionError(f"missing {expected - set(received)} from WebSocket events")


def test_agent_api_streams_graph_derived_tools_and_live_revocation(
    data_root: Path,
) -> None:
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.agent_runtime = MockAgentRuntime(WorldAgentCapabilityProvider(services))
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client, client.websocket_connect(
            "/ws/events"
        ) as websocket:
            assert websocket.receive_json()["type"] == "connection_ready"
            agent = client.post(
                "/api/nodes", json={"type": "agent", "name": "Atlas"}
            ).json()
            assert websocket.receive_json()["type"] == "card_created"
            text = client.post(
                "/api/nodes",
                json={
                    "type": "text",
                    "config": {"filename": "notes.txt"},
                    "content": "alpha",
                },
            ).json()
            assert websocket.receive_json()["type"] == "card_created"
            edge = client.post(
                "/api/edges",
                json={
                    "source": agent["id"],
                    "target": text["id"],
                    "relationship": "read_edit",
                },
            ).json()
            assert websocket.receive_json()["type"] == "edge_created"
            assert websocket.receive_json()["type"] == "permission_changed"

            accepted = client.post(
                f"/api/agents/{agent['id']}/run", json={"prompt": "use the notes"}
            )
            assert accepted.status_code == 202
            run_events = _receive_run_events(websocket, accepted.json()["run_id"])
            message = next(item for item in run_events if item["type"] == "agent_message")
            assert len(message["payload"]["available_tools"]) == 2

            assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
            _receive_until(websocket, {"edge_deleted", "permission_changed"})
            accepted = client.post(
                f"/api/agents/{agent['id']}/run", json={"prompt": "try again"}
            )
            run_events = _receive_run_events(websocket, accepted.json()["run_id"])
            message = next(item for item in run_events if item["type"] == "agent_message")
            assert message["payload"]["available_tools"] == []
    finally:
        services.close()
