"""Pack formations are collected in the Library, never implicitly equipped."""
from dataclasses import replace
import json

import pytest

from backend.card_library import KEY, CardLibraryStore, LibraryEdit
from backend.errors import GraphValidationError, NotFoundError
from backend.legions.presets import preset_record
from backend.legions.store import LegionStore
from backend.persistence.database import Database
from backend.plugins import PackDefinition, PluginDefinition, PluginDescriptor, create_builtin_registry
from backend.plugins.presets import LegionPresetDefinition, PresetNode

PRESET = "example.formation"


def registry_with_preset():
    registry = create_builtin_registry()
    card = replace(registry.node_type("text"), id="example.card", lifecycle=None, template_handler=None)

    def register(r):
        r.register_node_type(card)
        for suffix in ("one", "two"):
            r.register_pack(PackDefinition(id=f"example.{suffix}", name=suffix, cards=(card.id,)))
        # All members belong to core. Provenance must still be the registering
        # plugin, rather than a guessed owner based on member node types.
        r.register_legion_preset(LegionPresetDefinition(id=PRESET, name="Example formation", nodes=(
            PresetNode(key="group", type="legion", name="Group", parent_key=None),
            PresetNode(key="note", type="text", name="Note", payload={"content": ""}),
        )))

    registry.install(PluginDefinition(PluginDescriptor(id="example", version="1", plugin_api_version="1.18"), register))
    return registry


def edit(store, action, **kwargs):
    return store.edit(LibraryEdit(expected_revision=store.read().revision, action=action, **kwargs))


def deck(store, deck_id):
    return next(item for item in store.read().decks if item.id == deck_id)


def test_presets_require_an_open_source_pack_and_never_fill_decks(tmp_path):
    db = Database(tmp_path / "world.db")
    try:
        store = CardLibraryStore(db, registry_with_preset())
        assert store.snapshot()["preset_pack_ids"][PRESET] == ["example.one", "example.two"]
        assert all(not item.entries for item in store.read().decks)
        entry = {"kind": "legion", "id": PRESET}
        with pytest.raises(GraphValidationError, match="source pack"):
            edit(store, "move_entry", id="saved-legions", entry=entry)
        edit(store, "open_pack", id="open-agent-world.core.default")
        with pytest.raises(GraphValidationError, match="source pack"):
            edit(store, "move_entry", id="starter", entry=entry)
        edit(store, "open_pack", id="example.two")
        assert all(not item.entries for item in store.read().decks)
        for _ in range(2):
            edit(store, "move_entry", id="saved-legions", entry=entry)
        assert [item.id for item in deck(store, "saved-legions").entries] == [PRESET]
        edit(store, "open_pack", id="example.one")
        assert [item.id for item in deck(store, "saved-legions").entries] == [PRESET]
        # Independent Library additions preserve other deck memberships.
        edit(store, "move_entry", id="starter", entry=entry)
        target = edit(store, "create_deck", name="Research").active_deck_id
        edit(store, "move_entry", id=target, source_deck_id="saved-legions", entry=entry)
        assert not deck(store, "saved-legions").entries
        assert [item.id for item in deck(store, target).entries] == [PRESET]
        assert [item.id for item in deck(store, "starter").entries] == [PRESET]
        edit(store, "update_deck", id=target, entries=[])
        edit(store, "update_deck", id="starter", entries=[])
        restarted = CardLibraryStore(db, registry_with_preset())
        assert all(not item.entries for item in restarted.read().decks)
        assert PRESET in restarted.snapshot()["preset_pack_ids"]
    finally:
        db.close()


def test_unavailable_preset_reference_can_move_and_be_removed(tmp_path):
    db = Database(tmp_path / "world.db")
    try:
        store = CardLibraryStore(db, registry_with_preset())
        edit(store, "open_pack", id="example.one")
        entry = {"kind": "legion", "id": PRESET}
        edit(store, "move_entry", id="saved-legions", entry=entry)
        edit(store, "set_plugin_enabled", id="example", enabled=False)
        assert PRESET not in store.snapshot()["preset_pack_ids"]
        edit(store, "move_entry", id="starter", source_deck_id="saved-legions", entry=entry)
        with pytest.raises(NotFoundError):
            edit(store, "move_entry", id="saved-legions", entry=entry)
        edit(store, "update_deck", id="starter", entries=[])
        edit(store, "set_plugin_enabled", id="example", enabled=True)
        assert all(not item.entries for item in store.read().decks)
    finally:
        db.close()


def test_virtual_tray_migration_is_once_and_preserves_saved_templates(tmp_path):
    db = Database(tmp_path / "world.db")
    try:
        registry = registry_with_preset()
        store = CardLibraryStore(db, registry)
        saved = LegionStore(db).create("My formation", "Keep this", preset_record("assistant", registry).blueprint)
        edit(store, "open_pack", id="example.one")
        edit(store, "update_deck", id="starter", name="My choices", entries=[{"kind": "legion", "id": PRESET}])
        edit(store, "activate_deck", id="starter")
        payload = store.read().model_dump(mode="json")
        payload["schema_version"] = 1
        payload["decks"] = [item for item in payload["decks"] if item["id"] != "saved-legions"]
        with db.transaction(immediate=True) as connection:
            connection.execute("UPDATE application_settings SET value_json=? WHERE key=?", (json.dumps(payload), KEY))
        migrated = store.read()
        assert migrated.schema_version == 2
        assert migrated.active_deck_id == "starter"
        assert migrated.decks[0].model_dump(mode="json") == payload["decks"][0]
        assert [item.id for item in deck(store, "saved-legions").entries] == [saved.id]
        assert store.read().revision == migrated.revision
        edit(store, "update_deck", id="saved-legions", entries=[])
        assert not CardLibraryStore(db, registry).read().decks[-1].entries
        assert LegionStore(db).get(saved.id) == saved
        edit(store, "delete_deck", id="saved-legions")
        assert all(item.id != "saved-legions" for item in CardLibraryStore(db, registry).read().decks)
        assert LegionStore(db).get(saved.id) == saved
    finally:
        db.close()


def test_existing_real_legions_deck_is_not_repopulated_by_migration(tmp_path):
    db = Database(tmp_path / "world.db")
    try:
        store = CardLibraryStore(db, registry_with_preset())
        edit(store, "update_deck", id="saved-legions", name="Curated formations", entries=[])
        edit(store, "activate_deck", id="saved-legions")
        before = store.read().model_dump(mode="json")
        payload = {**before, "schema_version": 1}
        with db.transaction(immediate=True) as connection:
            connection.execute("UPDATE application_settings SET value_json=? WHERE key=?", (json.dumps(payload), KEY))
        after = store.read().model_dump(mode="json")
        assert after["decks"] == before["decks"]
        assert after["active_deck_id"] == before["active_deck_id"]
    finally:
        db.close()
