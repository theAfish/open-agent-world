from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.tests.conftest import create_node


def test_world_and_text_persist_across_restart(data_root: Path) -> None:
    settings = Settings.for_data_root(data_root)
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        agent = create_node(first, "agent", id="persistent-agent", position={"x": 2200, "y": 0})
        text = create_node(
            first,
            "text",
            id="persistent-text",
            config={"filename": "persistent.txt"},
            content="survives restart",
        )
        edge = first.post(
            "/api/edges",
            json={
                "id": "persistent-edge",
                "source": agent["id"],
                "target": text["id"],
                "relationship": "read_edit",
            },
        )
        assert edge.status_code == 201

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        world = second.get("/api/world").json()
        assert {node["id"] for node in world["nodes"]} == {
            "persistent-agent",
            "persistent-text",
        }
        assert [edge["id"] for edge in world["edges"]] == ["persistent-edge"]
        assert second.get("/api/resources/persistent-text/text").json()[
            "content"
        ] == "survives restart"
        first_chunk = second.get("/api/world", params={"chunks": "0:0"}).json()
        second_chunk = second.get("/api/world", params={"chunks": "1:0"}).json()
        assert first_chunk["nodes"][0]["id"] == "persistent-text"
        assert second_chunk["nodes"][0]["id"] == "persistent-agent"
        assert [edge["id"] for edge in first_chunk["edges"]] == ["persistent-edge"]
        assert [edge["id"] for edge in second_chunk["edges"]] == ["persistent-edge"]


def test_websocket_streams_typed_world_and_permission_events(client: TestClient) -> None:
    with client.websocket_connect("/ws/events") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "connection_ready"
        assert ready["id"]
        agent = create_node(client, "agent")
        created = websocket.receive_json()
        assert created["type"] == "card_created"
        assert created["node_id"] == agent["id"]
        assert created["stream_id"] == ready["stream_id"]
        assert created["sequence"] > ready["sequence"]

        text = create_node(client, "text")
        assert websocket.receive_json()["type"] == "card_created"
        edge = client.post(
            "/api/edges",
            json={
                "source": agent["id"],
                "target": text["id"],
                "relationship": "read",
            },
        )
        assert edge.status_code == 201
        assert websocket.receive_json()["type"] == "edge_created"
        permission = websocket.receive_json()
        assert permission["type"] == "permission_changed"
        assert permission["payload"]["affected_agent_ids"] == [agent["id"]]


def test_event_overflow_is_detectable_without_a_durable_event_log():
    import asyncio
    from backend.events.hub import EventHub
    from backend.events.models import EventType

    async def run():
        hub = EventHub(queue_size=1)
        async with hub.subscribe() as queue:
            await hub.publish(EventType.CARD_CREATED)
            first = await queue.get()
            await hub.publish(EventType.CARD_UPDATED)
            await hub.publish(EventType.CARD_DELETED)
            last = await queue.get()
            assert first.stream_id == last.stream_id
            assert last.sequence == first.sequence + 2
            assert hub.sequence == last.sequence
    asyncio.run(run())
