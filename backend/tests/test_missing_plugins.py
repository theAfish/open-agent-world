from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.plugins import PLUGIN_API_VERSION, PluginDefinition, PluginDescriptor, create_builtin_registry
from backend.services import create_services
from backend.world.models import CardCreate, EdgeCreate


@pytest.mark.parametrize("reason", ["plugin_missing", "type_missing", "owner_mismatch", "plugin_disabled"])
def test_unavailable_objects_remain_manageable(tmp_path: Path, reason: str):
    settings = Settings.for_data_root(tmp_path)
    registry = create_builtin_registry()
    registry.install(PluginDefinition(descriptor=PluginDescriptor(id="other.pack", version="1.0.0", plugin_api_version=PLUGIN_API_VERSION), configure=lambda _: None))
    services = create_services(settings, plugins=registry)
    agent = services.world.create_card(CardCreate(type="agent"))
    card = services.world.create_card(CardCreate(type="text", config={"nested": {"values": [1, 2, 3]}}))
    edge = services.world.create_edge(EdgeCreate(source=agent.id, target=card.id, relationship="read"))
    owner = registry.node_type_owner_id("text")
    with services.database.transaction() as db:
        if reason == "plugin_missing":
            db.execute("UPDATE cards SET plugin_id='removed.pack' WHERE id=?", (card.id,))
        elif reason == "type_missing":
            db.execute("UPDATE cards SET type='removed.card' WHERE id=?", (card.id,))
        elif reason == "owner_mismatch":
            # A different installed owner must never interpret the saved data.
            other = next(p.id for p in registry.plugins() if p.id != owner)
            db.execute("UPDATE cards SET plugin_id=? WHERE id=?", (other, card.id))
        before = db.execute("SELECT config_json FROM cards WHERE id=?", (card.id,)).fetchone()[0]
    services.close()
    services = create_services(settings, plugins=registry)
    if reason == "plugin_disabled":
        registry.set_enabled(owner, False)
    try:
        with TestClient(create_app(settings, services=services)) as client:
            response = client.get("/api/world?chunks=0:0")
            assert response.status_code == 200, response.text
            missing = services.world.get_card(card.id)
            assert missing.missing_plugin.reason == reason
            assert services.world.get_edge(edge.id).missing_plugin
            moved = client.post("/api/nodes/batch-update", json={"updates": [{
                "node_id": card.id, "patch": {"name": "Saved", "position": {"x": 450, "y": 260}, "size": {"width": 380, "height": 220}},
            }]})
            assert moved.status_code == 200, moved.text
            with services.database.locked() as db:
                assert db.execute("SELECT config_json FROM cards WHERE id=?", (card.id,)).fetchone()[0] == before
            assert client.delete(f"/api/edges/{edge.id}").status_code == 200
            assert client.delete(f"/api/nodes/{card.id}").status_code == 200
    finally:
        services.close()


def test_missing_container_does_not_block_ordinary_edits(tmp_path: Path):
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings, plugins=create_builtin_registry())
    group = services.world.create_card(CardCreate(type="legion"))
    child = services.world.create_card(CardCreate(type="text", parent_id=group.id))
    with services.database.transaction() as db:
        db.execute("UPDATE cards SET type='removed.container', plugin_id='removed.pack' WHERE id=?", (group.id,))
    services.close()
    services = create_services(settings, plugins=create_builtin_registry())
    try:
        with TestClient(create_app(settings, services=services)) as client:
            moved = client.patch(f"/api/nodes/{group.id}", json={"position": {"x": 100, "y": 200}})
            assert moved.status_code == 200, moved.text
            assert services.world.get_card(child.id).position.x == child.position.x + 100
            renamed = client.patch(f"/api/nodes/{child.id}", json={"name": "Still usable"})
            assert renamed.status_code == 200, renamed.text
            detached = client.patch(f"/api/nodes/{child.id}", json={"parent_id": None})
            assert detached.status_code == 200, detached.text
            assert client.delete(f"/api/nodes/{group.id}").status_code == 200
            assert client.get(f"/api/nodes/{child.id}").status_code == 200
    finally:
        services.close()


def test_missing_relationship_does_not_disable_other_grants(tmp_path: Path):
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings, plugins=create_builtin_registry())
    agent = services.world.create_card(CardCreate(type="agent"))
    first = services.world.create_card(CardCreate(type="text"))
    second = services.world.create_card(CardCreate(type="text"))
    lost = services.world.create_edge(EdgeCreate(source=agent.id, target=first.id, relationship="read"))
    services.world.create_edge(EdgeCreate(source=agent.id, target=second.id, relationship="read"))
    with services.database.transaction() as db:
        db.execute("UPDATE edges SET relationship='removed.read' WHERE id=?", (lost.id,))
    services.close()
    services = create_services(settings, plugins=create_builtin_registry())
    try:
        with TestClient(create_app(settings, services=services)) as client:
            assert services.world.get_edge(lost.id).missing_plugin.reason == "type_missing"
            grants = services.capabilities.derive(agent.id).capabilities
            assert grants and {grant.target_id for grant in grants} == {second.id}
            assert client.patch(f"/api/edges/{lost.id}", json={"direction": "bidirectional"}).status_code == 422
            assert client.delete(f"/api/edges/{lost.id}").status_code == 200
    finally:
        services.close()
