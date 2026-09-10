from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict

from backend.errors import GraphValidationError
from backend.persistence.database import Database
from backend.plugins import PluginDefinition, PluginDescriptor, create_builtin_registry
from backend.world.models import CardBatchPatch, CardCreate, CardPatch
from backend.world.store import WorldStore


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "untouched"


def test_host_status_survives_create_update_batch_restart_and_restore(tmp_path):
    registry = create_builtin_registry()
    definition = replace(registry.node_type("text"), id="example.strict", config_model=StrictConfig,
                         traits=frozenset(), lifecycle=None, template_handler=None, creation_fields=frozenset(),
                         default_status="ready", statuses=frozenset({"ready", "busy"}))
    registry.install(PluginDefinition(PluginDescriptor(id="example.strict", version="1", plugin_api_version="1.14"),
        lambda registration: registration.register_node_type(definition)))
    db = Database(tmp_path / "world.db")
    store = WorldStore(db, registry=registry)
    node = store.create_card(CardCreate(type=definition.id, status="busy", config={"label": "kept"}))
    assert node.status == "busy" and node.config == {"label": "kept"}
    preview = store.preview_update_card(node.id, CardPatch(status="ready"))
    assert preview.status == "ready" and preview.config == node.config
    assert store.get_card(node.id).status == "busy"
    node = store.update_card(node.id, CardPatch(name="Renamed"))
    assert node.status == "busy"
    node = store.update_card(node.id, CardPatch(status="ready"))
    node = store.update_cards([CardBatchPatch(node_id=node.id, patch=CardPatch(status="busy"))])[0]
    assert node.status == "busy" and node.config == {"label": "kept"}
    with pytest.raises(GraphValidationError):
        store.update_card(node.id, CardPatch(status="not-registered"))
    with pytest.raises(GraphValidationError):
        store.update_card(node.id, CardPatch(config={"unexpected": True}))
    db.close()
    db = Database(tmp_path / "world.db")
    store = WorldStore(db, registry=registry)
    saved = store.get_card(node.id)
    assert saved.status == "busy" and saved.config == {"label": "kept"}
    store.delete_card(node.id)
    restored = store.create_card(CardCreate(id=saved.id, type=saved.type, status=saved.status, config=saved.config))
    assert restored.status == "busy" and restored.config == saved.config
    # Models that declare or allow the legacy status field keep their contract.
    agent = store.create_card(CardCreate(type="agent", status="idle"))
    assert agent.config["status"] == agent.status == "idle"
    text = store.create_card(CardCreate(type="text", status="modified"))
    assert text.config["status"] == text.status == "modified"
    db.close()


def test_structure_viewer_placement_uses_the_actual_palette_request(client):
    state = client.get("/api/card-library").json()
    opened = client.post("/api/card-library/actions", json={"action": "open_pack", "id": "science.structure-viewer.default",
                         "expected_revision": state["revision"]})
    assert opened.status_code == 200, opened.text
    placed = client.post("/api/card-library/nodes", json={"type": "science.structure-viewer", "name": "Structure viewer",
        "position": {"x": 50, "y": 90}, "size": {"width": 96, "height": 96}, "expanded": False, "status": "ready", "config": {}})
    assert placed.status_code == 201, placed.text
    node = placed.json()
    assert node["status"] == "ready" and node["config"] == {}
    assert client.patch(f"/api/nodes/{node['id']}", json={"status": "ready", "config": {}}).status_code == 200
    assert client.delete(f"/api/nodes/{node['id']}").status_code == 200
    restored = client.post("/api/nodes/restore", json={"id": node["id"], "type": node["type"], "status": "ready", "config": {}})
    assert restored.status_code == 201, restored.text
