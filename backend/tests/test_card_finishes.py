from dataclasses import replace
import json
from math import nextafter
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.card_finishes import CARD_FINISH_WEIGHTS, roll_card_finish, weighted_choice
from backend.card_library import KEY, CardLibraryStore, CollectionEntry, LibraryEdit
from backend.config import Settings
from backend.errors import RevisionConflictError
from backend.main import create_app
from backend.persistence.database import Database
from backend.plugins import PackDefinition, PluginDefinition, PluginDescriptor, create_builtin_registry
from backend.services import create_services
from backend.world.models import Card, CardCreate
from backend.world.store import WorldStore


def edit(store, action, **kwargs):
    return store.edit(LibraryEdit(expected_revision=store.read().revision, action=action, **kwargs))


@pytest.mark.parametrize(("sample", "expected"), [
    (0, "normal"),
    (nextafter(0.72, 0), "normal"), (0.72, "foil"),
    (nextafter(0.88, 0), "foil"), (0.88, "rainbow"),
    (nextafter(0.94, 0), "rainbow"), (0.94, "starlight"),
    (nextafter(0.98, 0), "starlight"), (0.98, "laser"),
    (nextafter(1, 0), "laser"),
])
def test_finish_weight_boundaries(sample, expected):
    assert weighted_choice(CARD_FINISH_WEIGHTS, sample) == expected


def test_weighted_choice_relative_weights_and_disabled_outcomes():
    weights = {"disabled": 0, "first": 2, "second": 6, "also-disabled": 0}
    assert weighted_choice(weights, 0) == "first"
    assert weighted_choice(weights, nextafter(0.25, 0)) == "first"
    assert weighted_choice(weights, 0.25) == "second"
    assert weighted_choice(weights, nextafter(1, 0)) == "second"


@pytest.mark.parametrize("weights", [{}, {"x": 0}, {"x": -1}, {"x": float("nan")}, {"x": float("inf")}])
def test_weighted_choice_rejects_invalid_weights(weights):
    with pytest.raises(ValueError, match="weights"):
        weighted_choice(weights, 0)


@pytest.mark.parametrize("sample", [-0.01, 1, float("nan"), float("inf")])
def test_weighted_choice_rejects_invalid_samples(sample):
    with pytest.raises(ValueError, match="random_value"):
        weighted_choice(CARD_FINISH_WEIGHTS, sample)


def test_roll_uses_exactly_one_random_sample(monkeypatch):
    sample = Mock(return_value=0.95)
    monkeypatch.setattr("backend.card_finishes.random.random", sample)
    assert roll_card_finish() == "starlight"
    sample.assert_called_once_with()


def test_pack_rolls_once_per_new_entry_and_persists_across_decks_and_restart(tmp_path, monkeypatch):
    roll = Mock(return_value="rainbow")
    monkeypatch.setattr("backend.card_library.roll_card_finish", roll)
    path = tmp_path / "world.db"
    db = Database(path)
    store = CardLibraryStore(db, create_builtin_registry())
    initial = store.read()
    assert roll.call_count == 0
    pack_id = "open-agent-world.core.default"
    state = edit(store, "open_pack", id=pack_id)
    assert roll.call_count == len(set(state.packs[pack_id].definition.cards))
    assert all(entry.finish == "rainbow" for entry in state.collection.values())
    calls = roll.call_count
    with pytest.raises(RevisionConflictError):
        store.edit(LibraryEdit(expected_revision=initial.revision, action="open_pack", id=pack_id))
    edit(store, "open_pack", id=pack_id)
    edit(store, "update_deck", id="starter", entries=[{"id": "text"}])
    target = edit(store, "create_deck", name="Special prints").active_deck_id
    edit(store, "move_entry", id=target, source_deck_id="starter", entry={"id": "text"})
    expected = store.snapshot()
    db.close()
    db = Database(path)
    restarted = CardLibraryStore(db, create_builtin_registry())
    assert restarted.snapshot() == expected
    assert restarted.read().collection["text"].finish == "rainbow"
    assert roll.call_count == calls
    db.close()


def test_overlapping_packs_preserve_finish_but_new_content_receives_a_roll(tmp_path, monkeypatch):
    registry = create_builtin_registry()
    first = replace(registry.node_type("text"), id="example.first", lifecycle=None, template_handler=None)
    second = replace(first, id="example.second")

    def register(registration):
        registration.register_node_type(first)
        registration.register_node_type(second)
        registration.register_pack(PackDefinition(id="example.one", name="One", cards=(first.id,)))
        registration.register_pack(PackDefinition(id="example.two", name="Two", cards=(first.id, second.id)))

    registry.install(PluginDefinition(PluginDescriptor(id="example", version="1", plugin_api_version="1.14"), register))
    roll = Mock(side_effect=["foil", "laser"])
    monkeypatch.setattr("backend.card_library.roll_card_finish", roll)
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, registry)
    edit(store, "open_pack", id="example.one")
    edit(store, "open_pack", id="example.two")
    state = edit(store, "open_pack", id="example.two")
    assert state.collection[first.id].finish == "foil"
    assert state.collection[first.id].source_pack_ids == ["example.one", "example.two"]
    assert state.collection[second.id].finish == "laser"
    assert roll.call_count == 2
    db.close()


def test_old_collection_and_legacy_world_never_roll(tmp_path, monkeypatch):
    roll = Mock(side_effect=AssertionError("migration must not roll finishes"))
    monkeypatch.setattr("backend.card_library.roll_card_finish", roll)
    path = tmp_path / "world.db"
    Database(path).close()
    db = Database(path)
    store = CardLibraryStore(db, create_builtin_registry())
    state = store.read()
    assert state.collection and all(entry.finish == "normal" for entry in state.collection.values())
    payload = state.model_dump(mode="json")
    for entry in payload["collection"].values():
        entry.pop("finish")
    with db.transaction(immediate=True) as connection:
        connection.execute("UPDATE application_settings SET value_json=? WHERE key=?", (json.dumps(payload), KEY))
    db.close()
    db = Database(path)
    restarted = CardLibraryStore(db, create_builtin_registry())
    state = edit(restarted, "open_pack", id="open-agent-world.core.default")
    assert all(entry.finish == "normal" for entry in state.collection.values())
    roll.assert_not_called()
    db.close()


def test_old_card_serialization_and_database_migrate_to_normal(tmp_path):
    path = tmp_path / "world.db"
    db = Database(path)
    world = WorldStore(db, create_builtin_registry())
    card = world.create_card(CardCreate(type="text"))
    payload = card.model_dump(mode="json")
    payload.pop("finish")
    assert Card.model_validate(payload).finish == "normal"
    assert CardCreate(type="text").finish == "normal"
    assert CollectionEntry(card_id="text", plugin_id="core", source_pack_ids=[], unlocked_at="old").finish == "normal"
    db.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE cards DROP COLUMN finish")
    db = Database(path)
    assert WorldStore(db, create_builtin_registry()).get_card(card.id).finish == "normal"
    db.close()
    with pytest.raises(ValidationError):
        CardCreate(type="text", finish="foil+rainbow")


def test_owned_finish_survives_placement_copy_legion_restore_and_reload(tmp_path, monkeypatch):
    roll = Mock(return_value="starlight")
    monkeypatch.setattr("backend.card_library.roll_card_finish", roll)
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings)
    with TestClient(create_app(settings, services=services)) as client:
        # A card that already exists must retain its independent normal finish.
        legacy = client.post("/api/nodes", json={"type": "agent"}).json()
        edit(services.card_library, "open_pack", id="open-agent-world.core.default")
        calls = roll.call_count
        placed = client.post("/api/card-library/nodes", json={"type": "agent", "finish": "laser"})
        assert placed.status_code == 201, placed.text
        card = placed.json()
        assert card["finish"] == "starlight"
        duplicate = client.post(f"/api/nodes/{card['id']}/duplicate")
        assert duplicate.status_code == 200, duplicate.text
        assert all(node["finish"] == "starlight" for node in duplicate.json()["nodes"])
        saved = client.post("/api/legions", json={"name": "Print collection", "node_ids": [legacy["id"], card["id"]]})
        assert saved.status_code == 201, saved.text
        instantiated = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
        assert instantiated.status_code == 201, instantiated.text
        assert sorted(node["finish"] for node in instantiated.json()["nodes"]) == ["normal", "starlight"]
        assert client.get(f"/api/nodes/{legacy['id']}").json()["finish"] == "normal"
        assert client.delete(f"/api/nodes/{card['id']}").status_code == 200
        restored = client.post("/api/nodes/restore", json={"id": card["id"], "type": card["type"], "finish": card["finish"]})
        assert restored.status_code == 201, restored.text
        assert restored.json()["finish"] == "starlight"
        assert roll.call_count == calls
    services.close()
    restarted = create_services(settings)
    try:
        assert restarted.world.get_card(card["id"]).finish == "starlight"
        assert restarted.world.get_card(legacy["id"]).finish == "normal"
        assert sorted(node.finish for node in restarted.legions.get(saved.json()["id"]).blueprint.nodes) == ["normal", "starlight"]
        assert roll.call_count == calls
    finally:
        restarted.close()


def test_deployment_copy_and_public_workspace_preserve_finishes(tmp_path):
    from backend.deploy import create_deployment

    source, target = tmp_path / "source", tmp_path / "deployed"
    password = "printed-card-test-password"
    with TestClient(create_app(Settings.for_data_root(source))) as client:
        legion = client.post("/api/nodes/restore", json={
            "id": "printed-legion", "type": "legion", "finish": "foil",
        })
        assert legion.status_code == 201, legion.text
        card = client.post("/api/nodes", json={
            "type": "text", "parent_id": "printed-legion", "finish": "laser", "content": "Printed guide",
        })
        assert card.status_code == 201, card.text
        layout = {"version": 2, "root": {"kind": "pane", "view": {"card_id": card.json()["id"]}}}
        updated = client.patch("/api/nodes/printed-legion", json={"config": {"workspace_layout": layout}})
        assert updated.status_code == 200, updated.text
        release = client.post("/api/deployments", json={"legion_id": "printed-legion", "name": "Printed guide"})
        assert release.status_code == 201, release.text
    create_deployment(source, target, release.json()["id"], password=password)
    with TestClient(create_app(Settings.for_data_root(target))) as client:
        signed_in = client.post("/api/deployment/session", json={"password": password})
        assert signed_in.status_code == 200, signed_in.text
        response = client.get("/api/runtime-app")
        assert response.status_code == 200, response.text
        assert response.json()["cards"][0]["finish"] == "laser"
        assert response.json()["legion"]["finish"] == "foil"
