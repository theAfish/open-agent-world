from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.card_library import CardLibraryStore, LibraryEdit
from backend.config import Settings
from backend.errors import GraphValidationError, RevisionConflictError
from backend.main import create_app
from backend.persistence.database import Database
from backend.plugins import PackDefinition, PluginDefinition, PluginDescriptor, create_builtin_registry
from backend.services import create_services


class EmptyConfig(BaseModel):
    pass


def install(registry, *, explicit=True, extra=False):
    node = replace(registry.node_type("text"), id="example.card", label="Example card", lifecycle=None,
                   config_model=EmptyConfig, traits=frozenset(), creation_fields=frozenset(), template_handler=None)
    def register(registration):
        registration.register_node_type(node)
        if extra:
            registration.register_node_type(replace(node, id="example.extra"))
        if explicit:
            registration.register_pack(PackDefinition(id="example.default", name="Example pack", cards=tuple(registration.nodes)))
    registry.install(PluginDefinition(PluginDescriptor(id="example", version="2" if extra else "1", plugin_api_version="1.14"), register))


def edit(store, action, **kwargs):
    return store.edit(LibraryEdit(expected_revision=store.read().revision, action=action, **kwargs))


def test_new_install_open_deck_remove_restart(tmp_path):
    registry = create_builtin_registry()
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, registry)
    assert not store.read().collection
    assert not store.read().decks[0].entries
    install(registry)
    state = store.read()
    assert not state.packs["example.default"].opened
    assert "example.card" not in state.collection
    with pytest.raises(GraphValidationError, match="Only collected"):
        edit(store, "update_deck", id="starter", entries=[{"id": "example.card"}])
    state = edit(store, "open_pack", id="example.default")
    opened_at = state.packs["example.default"].opened_at
    unlocked_at = state.collection["example.card"].unlocked_at
    assert not state.decks[0].entries
    edit(store, "open_pack", id="example.default")
    assert store.read().collection["example.card"].unlocked_at == unlocked_at
    state = edit(store, "create_deck", name="Research", icon="zap", entries=[{"id": "example.card"}])
    deck_id = state.active_deck_id
    db.close()

    registry = create_builtin_registry()
    install(registry)
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, registry)
    state = store.read()
    assert state.active_deck_id == deck_id
    assert state.decks[-1].entries[0].id == "example.card"
    assert state.decks[-1].icon == "zap"
    assert state.packs["example.default"].opened_at == opened_at
    edit(store, "update_deck", id=deck_id, entries=[])
    assert "example.card" in store.read().collection
    assert not store.read().decks[-1].entries
    assert not store.read().migration_pending
    db.close()


def test_disable_uninstall_reinstall_preserves_references(tmp_path):
    path = tmp_path / "world.db"
    registry = create_builtin_registry()
    install(registry)
    db = Database(path)
    store = CardLibraryStore(db, registry)
    edit(store, "open_pack", id="example.default")
    edit(store, "update_deck", id="starter", entries=[{"id": "example.card"}])
    edit(store, "set_plugin_enabled", id="example", enabled=False)
    assert "example.card" not in store.snapshot()["available_card_ids"]
    assert all(node.id != "example.card" for node in registry.catalog().node_types)
    db.close()
    db = Database(path)
    store = CardLibraryStore(db, create_builtin_registry())
    assert not store.read().plugins["example"].installed
    assert store.read().packs["example.default"].owned
    assert store.read().decks[0].entries[0].id == "example.card"
    db.close()
    registry = create_builtin_registry()
    install(registry)
    db = Database(path)
    store = CardLibraryStore(db, registry)
    assert not registry.is_enabled("example")
    edit(store, "set_plugin_enabled", id="example", enabled=True)
    assert "example.card" in store.snapshot()["available_card_ids"]
    db.close()


def test_migration_once_and_browser_folder_import(tmp_path):
    path = tmp_path / "world.db"
    Database(path).close()  # A pre-Pack database, including an empty old world.
    registry = create_builtin_registry()
    db = Database(path)
    store = CardLibraryStore(db, registry)
    state = store.read()
    assert state.migration_pending
    assert state.collection["agent"].unlocked
    assert all(pack.opened for pack in state.packs.values())
    assert any(deck.entries for deck in state.decks)
    edit(store, "import_legacy", decks=[{"id": "custom-kit", "name": "My old kit", "entries": [{"id": "text"}]}])
    assert store.read().active_deck_id == "custom-kit"
    install(registry)
    assert not store.read().packs["example.default"].opened
    assert "example.card" not in store.read().collection
    edit(store, "import_legacy", decks=[{"id": "bad", "name": "Must not replace"}])
    assert store.read().decks[0].id == "custom-kit"
    db.close()


def test_updates_do_not_silently_unlock_new_content(tmp_path):
    path = tmp_path / "world.db"
    registry = create_builtin_registry()
    install(registry)
    db = Database(path)
    store = CardLibraryStore(db, registry)
    edit(store, "open_pack", id="example.default")
    db.close()
    registry = create_builtin_registry()
    install(registry, extra=True)
    db = Database(path)
    store = CardLibraryStore(db, registry)
    assert store.read().plugins["example"].descriptor.version == "2"
    assert "example.extra" not in store.read().collection
    edit(store, "open_pack", id="example.default")
    assert "example.extra" in store.read().collection
    db.close()


def test_legacy_pack_and_atomic_validation():
    registry = create_builtin_registry()
    install(registry, explicit=False)
    pack = next(p for p in registry.catalog().packs if p.plugin_id == "example")
    assert pack.id == "example.default" and pack.compatibility
    assert pack.cards == ("example.card",)
    def invalid(registration):
        registration.register_pack(PackDefinition(id="invalid.pack", name="Invalid", cards=("example.card",)))
    with pytest.raises(ValueError, match="only this plugin"):
        registry.install(PluginDefinition(PluginDescriptor(id="invalid", version="1", plugin_api_version="1.14"), invalid))
    assert not registry.has_plugin("invalid")
    assert all(p.id != "invalid.pack" for p in registry.catalog().packs)


def test_revision_conflicts_and_deck_validation(tmp_path):
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, create_builtin_registry())
    stale = store.read().revision
    edit(store, "open_pack", id="open-agent-world.core.default")
    with pytest.raises(RevisionConflictError):
        store.edit(LibraryEdit(expected_revision=stale, action="delete_deck", id="starter"))
    with pytest.raises(GraphValidationError, match="duplicate"):
        edit(store, "update_deck", id="starter", entries=[{"id": "text"}, {"id": "text"}])
    with pytest.raises(GraphValidationError, match="at least one"):
        edit(store, "delete_deck", id="starter")
    with pytest.raises(GraphValidationError, match="Only collected"):
        edit(store, "update_deck", id="starter", entries=[{"id": "legion"}])
    db.close()


def test_api_collection_admission_and_disable_usage(tmp_path):
    registry = create_builtin_registry()
    install(registry)
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        def action(kind, **kwargs):
            state = client.get("/api/card-library").json()
            return client.post("/api/card-library/actions", json={"action": kind, "expected_revision": state["revision"], **kwargs})
        assert client.post("/api/card-library/nodes", json={"type": "example.card"}).status_code == 422
        assert action("open_pack", id="example.default").status_code == 200
        node = client.post("/api/card-library/nodes", json={"type": "example.card"})
        assert node.status_code == 201, node.text
        blocked = action("set_plugin_enabled", id="example", enabled=False)
        assert blocked.status_code == 409 and "in use" in blocked.text
        assert client.delete("/api/nodes/" + node.json()["id"]).status_code == 200
        assert action("set_plugin_enabled", id="example", enabled=False).status_code == 200
        assert client.post("/api/nodes", json={"type": "example.card"}).status_code == 422
        assert client.post("/api/card-library/nodes", json={"type": "example.card"}).status_code == 422
        assert action("set_plugin_enabled", id="example", enabled=True).status_code == 200
        assert client.post("/api/card-library/nodes", json={"type": "example.card"}).status_code == 201
    services.close()


def test_saved_legion_remains_visible_but_unavailable_when_plugin_disabled(tmp_path):
    registry = create_builtin_registry()
    install(registry)
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        nodes = [client.post("/api/nodes", json={"type": "example.card"}).json()["id"] for _ in range(2)]
        saved = client.post("/api/legions", json={"name": "Saved example", "node_ids": nodes})
        assert saved.status_code == 201, saved.text
        for node in nodes:
            assert client.delete(f"/api/nodes/{node}").status_code == 200
        edit(services.card_library, "set_plugin_enabled", id="example", enabled=False)
        listing = client.get("/api/legions")
        assert listing.status_code == 200, listing.text
        assert not listing.json()[0]["compatible"]
        assert "disabled plugin" in " ".join(listing.json()[0]["issues"])
        assert client.post(f"/api/legions/{saved.json()['id']}/instances", json={}).status_code == 422
        edit(services.card_library, "set_plugin_enabled", id="example", enabled=True)
        assert client.get("/api/legions").json()[0]["compatible"]
    services.close()


def test_cached_runtime_is_gated_and_plugin_state_survives_application_restart(tmp_path):
    from backend.agents.mock import MockAgentRuntime
    from backend.errors import PluginUnavailableError

    def registry_with_runtime():
        registry = create_builtin_registry()
        registry.install(PluginDefinition(PluginDescriptor(id="example.runtime", version="1", plugin_api_version="1.14"),
            lambda r: r.register_runtime_provider("example.runtime", MockAgentRuntime)))
        return registry

    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings, plugins=registry_with_runtime())
    with TestClient(create_app(settings, services=services)) as client:
        services.run_manager._provider("example.runtime")  # Cache it before disabling.
        state = client.get("/api/card-library").json()
        response = client.post("/api/card-library/actions", json={"action": "set_plugin_enabled", "id": "example.runtime", "enabled": False, "expected_revision": state["revision"]})
        assert response.status_code == 200, response.text
        with pytest.raises(PluginUnavailableError, match="disabled"):
            services.run_manager._provider("example.runtime")
        edit(services.card_library, "open_pack", id="open-agent-world.core.default")
        edit(services.card_library, "update_deck", id="starter", entries=[{"id": "text"}])
    services.close()
    services = create_services(settings, plugins=registry_with_runtime())
    with TestClient(create_app(settings, services=services)) as client:
        state = client.get("/api/card-library").json()
        assert not state["plugins"]["example.runtime"]["enabled"]
        assert state["decks"][0]["entries"] == [{"kind": "node", "id": "text"}]
        assert state["packs"]["open-agent-world.core.default"]["opened"]
    services.close()


def test_shared_card_provenance_does_not_duplicate_collection(tmp_path):
    registry = create_builtin_registry()
    node = replace(registry.node_type("text"), id="example.card", lifecycle=None, template_handler=None)
    def register(r):
        r.register_node_type(node)
        r.register_pack(PackDefinition(id="example.one", name="One", cards=(node.id,)))
        r.register_pack(PackDefinition(id="example.two", name="Two", cards=(node.id,)))
    registry.install(PluginDefinition(PluginDescriptor(id="example", version="1", plugin_api_version="1.14"), register))
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, registry)
    edit(store, "open_pack", id="example.one")
    edit(store, "open_pack", id="example.two")
    assert list(store.read().collection) == [node.id]
    assert store.read().collection[node.id].source_pack_ids == ["example.one", "example.two"]
    revision = store.read().revision
    assert store.read().revision == revision  # Read/reconcile must not fabricate a change.
    db.close()


def test_move_entry_is_atomic_deduplicated_and_preserves_other_memberships(tmp_path):
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, create_builtin_registry())
    edit(store, "open_pack", id="open-agent-world.core.default")
    edit(store, "update_deck", id="starter", entries=[{"id": "text"}, {"id": "image"}])
    target = edit(store, "create_deck", name="Target", entries=[{"id": "text"}]).active_deck_id
    other = edit(store, "create_deck", name="Other", entries=[{"id": "text"}]).active_deck_id
    stale = store.read().revision
    state = edit(store, "move_entry", source_deck_id="starter", id=target, entry={"id": "text"})
    decks = {deck.id: deck for deck in state.decks}
    assert [entry.id for entry in decks["starter"].entries] == ["image"]
    assert [entry.id for entry in decks[target].entries] == ["text"]
    assert [entry.id for entry in decks[other].entries] == ["text"]
    assert state.active_deck_id == target
    with pytest.raises(RevisionConflictError):
        store.edit(LibraryEdit(action="move_entry", expected_revision=stale, source_deck_id="starter", id=target, entry={"id": "image"}))
    assert store.read().model_dump(mode="json") == state.model_dump(mode="json")
    with pytest.raises(GraphValidationError, match="source deck"):
        edit(store, "move_entry", source_deck_id="starter", id=target, entry={"id": "agent"})
    assert store.read().model_dump(mode="json") == state.model_dump(mode="json")
    db.close()
    db = Database(tmp_path / "world.db")
    assert CardLibraryStore(db, create_builtin_registry()).read().model_dump(mode="json") == state.model_dump(mode="json")
    db.close()


def test_move_unavailable_reference_preserves_collection_rules(tmp_path):
    registry = create_builtin_registry()
    install(registry)
    db = Database(tmp_path / "world.db")
    store = CardLibraryStore(db, registry)
    edit(store, "open_pack", id="example.default")
    edit(store, "update_deck", id="starter", entries=[{"id": "example.card"}])
    target = edit(store, "create_deck", name="Archived").active_deck_id
    edit(store, "set_plugin_enabled", id="example", enabled=False)
    state = edit(store, "move_entry", source_deck_id="starter", id=target, entry={"id": "example.card"})
    assert not state.decks[0].entries
    assert state.decks[1].entries[0].id == "example.card"
    with pytest.raises(GraphValidationError, match="Only collected"):
        edit(store, "move_entry", id="starter", entry={"id": "example.card"})
    assert store.read().model_dump(mode="json") == state.model_dump(mode="json")
    db.close()
