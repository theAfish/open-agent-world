"""Application-owned collection and deck state; plugin definitions stay registry-owned."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import ConflictError, GraphValidationError, NotFoundError, RevisionConflictError
from backend.persistence.database import Database
from backend.plugins.registry import NodeTypeCatalogItem, PackCatalogItem, PluginDescriptor, PluginRegistry

KEY = "card_library.v1"
CORE = "open-agent-world.core"


def now() -> str:
    return datetime.now(UTC).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PluginInstallState(Model):
    descriptor: PluginDescriptor
    installed: bool = True
    enabled: bool = True


class UserPackState(Model):
    definition: PackCatalogItem
    owned: bool = True
    opened: bool = False
    opened_at: str | None = None


class CollectionEntry(Model):
    card_id: str
    plugin_id: str
    source_pack_ids: list[str]
    unlocked: bool = True
    unlocked_at: str


class DeckEntry(Model):
    kind: Literal["node", "legion"] = "node"
    id: str = Field(min_length=1, max_length=128)


class Deck(Model):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    icon: str = Field(default="layers", max_length=40)
    entries: list[DeckEntry] = Field(default_factory=list, max_length=2000)


class LibraryState(Model):
    schema_version: Literal[1] = 1
    revision: int = 0
    migration_pending: bool = False
    plugins: dict[str, PluginInstallState] = Field(default_factory=dict)
    packs: dict[str, UserPackState] = Field(default_factory=dict)
    # Last observed public metadata is retained only to describe unavailable content.
    card_definitions: dict[str, NodeTypeCatalogItem] = Field(default_factory=dict)
    collection: dict[str, CollectionEntry] = Field(default_factory=dict)
    decks: list[Deck] = Field(default_factory=lambda: [Deck(id="starter", name="My deck")])
    active_deck_id: str = "starter"


class LibraryEdit(Model):
    expected_revision: int = Field(ge=0)
    action: Literal["open_pack", "create_deck", "update_deck", "delete_deck", "activate_deck", "move_entry", "import_legacy", "set_plugin_enabled"]
    id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    icon: str | None = Field(default=None, min_length=1, max_length=40)
    entries: list[DeckEntry] | None = Field(default=None, max_length=2000)
    entry: DeckEntry | None = None
    source_deck_id: str | None = Field(default=None, max_length=128)
    decks: list[Deck] | None = Field(default=None, max_length=100)
    enabled: bool | None = None


class CardLibraryStore:
    def __init__(self, database: Database, registry: PluginRegistry):
        self.database = database
        self.registry = registry
        self.reconcile()

    @staticmethod
    def _read(db) -> LibraryState | None:
        row = db.execute("SELECT value_json FROM application_settings WHERE key=?", (KEY,)).fetchone()
        return LibraryState.model_validate_json(row[0]) if row else None

    @staticmethod
    def _write(db, state: LibraryState) -> None:
        db.execute("INSERT INTO application_settings(key,value_json) VALUES (?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (KEY, state.model_dump_json()))

    def reconcile(self) -> LibraryState:
        """Observe installation without granting cards or filling existing decks."""
        catalog = self.registry.catalog(include_disabled=True)
        with self.database.transaction(immediate=True) as db:
            old = self._read(db)
            state = old.model_copy(deep=True) if old else LibraryState(migration_pending=self.database.preexisting_world)
            installed = {p.id for p in catalog.plugins}
            for pid, plugin in state.plugins.items():
                plugin.installed = pid in installed
            for descriptor in catalog.plugins:
                saved = state.plugins.get(descriptor.id)
                state.plugins[descriptor.id] = PluginInstallState(descriptor=descriptor, enabled=saved.enabled if saved else True)
            for pack in catalog.packs:
                saved = state.packs.get(pack.id)
                if saved and saved.definition.plugin_id != pack.plugin_id:
                    raise ConflictError(f"Pack {pack.id!r} changed plugin ownership")
                if saved:
                    saved.definition = pack
                else:
                    state.packs[pack.id] = UserPackState(definition=pack)
            for card in catalog.node_types:
                saved = state.card_definitions.get(card.id)
                if saved and saved.plugin_id != card.plugin_id:
                    raise ConflictError(f"Card {card.id!r} changed plugin ownership")
                state.card_definitions[card.id] = card
            if old is None and state.migration_pending:
                for pack in state.packs.values():
                    self._unlock(state, pack)
                groups: dict[str, Deck] = {}
                for card in catalog.node_types:
                    if card.user_creatable:
                        deck = groups.setdefault(card.deck_id, Deck(id=card.deck_id, name=card.deck_label, icon=card.deck_icon))
                        deck.entries.append(DeckEntry(id=card.id))
                legions = db.execute("SELECT id FROM legions ORDER BY created_at, id").fetchall()
                if legions:
                    groups["saved-legions"] = Deck(id="saved-legions", name="Legions", entries=[DeckEntry(kind="legion", id=row[0]) for row in legions])
                state.decks = list(groups.values()) or state.decks
                state.active_deck_id = state.decks[0].id
            # Catalog dictionaries may contain tuples; SQLite JSON restores lists.
            if old is None or state.model_dump(mode="json") != old.model_dump(mode="json"):
                state.revision += 1
                self._write(db, state)
        for pid, plugin in state.plugins.items():
            if plugin.installed:
                self.registry.set_enabled(pid, plugin.enabled)
        return state

    def read(self) -> LibraryState:
        return self.reconcile()

    def card_available(self, state: LibraryState, card_id: str) -> bool:
        card = state.card_definitions.get(card_id)
        if not card or not self.registry.is_enabled(card.plugin_id):
            return False
        try:
            return self.registry.node_type_owner_id(card_id) == card.plugin_id and self.registry.node_type(card_id).user_creatable
        except (ValueError, GraphValidationError):
            return False

    def assert_collected(self, card_id: str) -> None:
        state = self.read()
        if card_id not in state.collection or not state.collection[card_id].unlocked:
            raise GraphValidationError("Open this card's pack in the Library before adding it to the world")
        if not self.card_available(state, card_id):
            raise GraphValidationError("This card is unavailable; its plugin must be installed and enabled")

    @staticmethod
    def _unlock(state: LibraryState, pack: UserPackState) -> None:
        timestamp = now()
        pack.opened = True
        pack.opened_at = pack.opened_at or timestamp
        for cid in pack.definition.cards:
            entry = state.collection.get(cid)
            if entry:
                if pack.definition.id not in entry.source_pack_ids:
                    entry.source_pack_ids.append(pack.definition.id)
                entry.unlocked = True
            else:
                state.collection[cid] = CollectionEntry(card_id=cid, plugin_id=pack.definition.plugin_id,
                    source_pack_ids=[pack.definition.id], unlocked_at=timestamp)

    def _validate_additions(self, db, state: LibraryState, entries: list[DeckEntry], previous: list[DeckEntry]) -> None:
        keys = [(entry.kind, entry.id) for entry in entries]
        if len(set(keys)) != len(keys):
            raise GraphValidationError("A deck cannot contain duplicate card references")
        for entry in entries:
            if entry in previous:
                continue  # Unavailable references can be kept or removed without losing the deck.
            if entry.kind == "legion":
                if not db.execute("SELECT 1 FROM legions WHERE id=?", (entry.id,)).fetchone():
                    raise NotFoundError("Saved Legion no longer exists")
            elif entry.id not in state.collection or not state.collection[entry.id].unlocked or not self.card_available(state, entry.id):
                raise GraphValidationError("Only collected, available cards can be added to a deck")

    def edit(self, request: LibraryEdit) -> LibraryState:
        self.reconcile()
        with self.database.transaction(immediate=True) as db:
            state = self._read(db)
            assert state is not None
            if request.expected_revision != state.revision:
                raise RevisionConflictError("The Library changed in another window. It has been refreshed; retry your change.")
            if request.action == "open_pack":
                pack = state.packs.get(request.id or "")
                current = {p.id for p in self.registry.catalog().packs}
                if not pack or not pack.owned or request.id not in current:
                    raise NotFoundError("Owned pack is not currently installed")
                if not self.registry.is_enabled(pack.definition.plugin_id):
                    raise GraphValidationError("Enable the pack's plugin before opening it")
                self._unlock(state, pack)
            elif request.action == "create_deck":
                if not request.name or len(state.decks) >= 100:
                    raise GraphValidationError("Provide a deck name; at most 100 decks are supported")
                deck = Deck(id=str(uuid4()), name=request.name, icon=request.icon or "folder", entries=request.entries or [])
                self._validate_additions(db, state, deck.entries, [])
                state.decks.append(deck)
                state.active_deck_id = deck.id
            elif request.action == "move_entry":
                target = next((d for d in state.decks if d.id == request.id), None)
                source = next((d for d in state.decks if d.id == request.source_deck_id), None)
                if target is None or (request.source_deck_id is not None and source is None):
                    raise NotFoundError("Deck no longer exists")
                if request.entry is None:
                    raise GraphValidationError("Choose a card to move")
                if source is not None and request.entry not in source.entries:
                    raise GraphValidationError("Card is no longer in the source deck")
                if source is target:
                    return state
                if request.entry not in target.entries:
                    if len(target.entries) >= 2000:
                        raise GraphValidationError("A deck supports at most 2000 cards")
                    entries = [*target.entries, request.entry]
                    # Moving an existing reference preserves even unavailable cards.
                    self._validate_additions(db, state, entries, [*target.entries, *(source.entries if source else [])])
                    target.entries = entries
                if source is not None:
                    source.entries = [entry for entry in source.entries if entry != request.entry]
                state.active_deck_id = target.id
            elif request.action in {"update_deck", "delete_deck", "activate_deck"}:
                deck = next((d for d in state.decks if d.id == request.id), None)
                if deck is None:
                    raise NotFoundError("Deck no longer exists")
                if request.action == "update_deck":
                    if request.name is not None:
                        deck.name = request.name
                    if request.icon is not None:
                        deck.icon = request.icon
                    if request.entries is not None:
                        self._validate_additions(db, state, request.entries, deck.entries)
                        deck.entries = request.entries
                elif request.action == "activate_deck":
                    state.active_deck_id = deck.id
                else:
                    if len(state.decks) == 1:
                        raise GraphValidationError("Keep at least one deck")
                    state.decks.remove(deck)
                    if state.active_deck_id == deck.id:
                        state.active_deck_id = state.decks[0].id
            elif request.action == "import_legacy":
                if not state.migration_pending:
                    return state
                if request.decks:
                    ids = [deck.id for deck in request.decks]
                    if len(set(ids)) != len(ids):
                        raise GraphValidationError("Deck IDs must be unique")
                    for deck in request.decks:
                        self._validate_additions(db, state, deck.entries, [])
                    # Saved formations had a separate tray; retain them during folder import.
                    formation_decks = [d for d in state.decks if any(e.kind == "legion" for e in d.entries) and d.id not in ids]
                    state.decks = request.decks + formation_decks
                    state.active_deck_id = next((d.id for d in state.decks if d.entries), state.decks[0].id)
            elif request.action == "set_plugin_enabled":
                plugin = state.plugins.get(request.id or "")
                if not plugin or not plugin.installed:
                    raise NotFoundError("Plugin is not installed")
                if request.enabled is None:
                    raise GraphValidationError("Provide the plugin enabled state")
                if request.id == CORE and not request.enabled:
                    raise GraphValidationError("The core plugin is required by the application")
                plugin.enabled = request.enabled
            state.migration_pending = False
            state.revision += 1
            self._write(db, state)
        if request.action == "set_plugin_enabled":
            self.registry.set_enabled(request.id, request.enabled)
        return state

    def snapshot(self) -> dict:
        state = self.read()
        current = self.registry.catalog(include_disabled=True)
        result = state.model_dump(mode="json")
        result["available_card_ids"] = [cid for cid in state.collection if self.card_available(state, cid)]
        result["available_pack_ids"] = [pack.id for pack in current.packs if self.registry.is_enabled(pack.plugin_id)]
        return result
