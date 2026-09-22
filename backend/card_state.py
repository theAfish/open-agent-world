"""Host-owned card namespaces, layered over the existing revisioned StateStore."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from uuid import uuid4

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins.state import LEGACY_STATE

# Request/batch context is captured once. Plugins never receive this identifier.
active_state_session: ContextVar[str | None] = ContextVar("card_state_session", default=None)
DEFAULT_SESSION = "default"


@contextmanager
def state_session(session_id):
    token = active_state_session.set(session_id)
    try:
        yield
    finally:
        active_state_session.reset(token)


def effective_scope(spec, override=None):
    spec = spec or LEGACY_STATE
    if spec.mode == "none":
        return None
    # A removed option or revoked user configurability returns to developer policy.
    if spec.user_configurable and len(spec.supported_scopes) > 1 and override in spec.supported_scopes:
        return override
    return spec.default_scope


def validate_override(spec, value):
    spec = spec or LEGACY_STATE
    if spec.mode != "scoped" or not spec.user_configurable or len(spec.supported_scopes) < 2:
        raise PermissionDeniedError("This card's data persistence is chosen by its plugin")
    if value is not None and value not in spec.supported_scopes:
        raise ResourceValidationError("Unsupported data persistence setting")
    return value


class ScopedStateStore:
    def __init__(self, services):
        self.services = services

    def default_session(self, card_id):
        """Stable fallback for unopened workspaces and pre-session standalone cards."""
        world = self.services.world
        card = world.get_card(card_id)
        for owner in world.ancestors(card):
            if owner.config.get("session_id"):
                return owner.config["session_id"]
            conversations = [node for node in world.descendants(owner.id)
                             if self.services.plugins.has_trait(node.type, "core.conversation")]
            if len(conversations) == 1:
                sessions = self.services.conversations.list_sessions(conversations[0].id)
                default = next((session for session in sessions if session.is_default), None)
                if default:
                    return default.id
        conversations = [node for node in world.list_cards()
                         if self.services.plugins.has_trait(node.type, "core.conversation")]
        if len(conversations) == 1:
            default = next((session for session in self.services.conversations.list_sessions(conversations[0].id) if session.is_default), None)
            if default:
                return default.id
        return DEFAULT_SESSION

    def session_id(self, card_id):
        invocation = self.services.run_manager.current_context if self.services.run_manager else None
        session_id = (invocation.context_id if invocation else None) or active_state_session.get()
        if session_id is not None:
            if session_id != DEFAULT_SESSION:
                with self.services.database.locked() as db:
                    exists = db.execute("SELECT 1 FROM conversation_sessions WHERE id=?", (session_id,)).fetchone()
                if not exists:
                    raise ResourceValidationError("The selected conversation session no longer exists")
            return session_id
        return self.default_session(card_id)

    def identity(self, card_id):
        card = self.services.world.get_card(card_id)
        scope_type = effective_scope(self.services.plugins.node_type(card.type).state, card.state_scope_override)
        if scope_type is None:
            raise PermissionDeniedError("This plugin has no persistent state capability")
        return scope_type, "*" if scope_type == "shared" else self.session_id(card_id)

    def bind(self, card_id, *, authorize=None):
        card = self.services.world.get_card(card_id)
        spec = self.services.plugins.node_type(card.type).state
        if spec is not None and spec.mode == "none":
            return None
        return BoundCardState(self, card_id, self.identity(card_id), authorize)

    def scope(self, card_id, identity=None):
        services = self.services
        card = services.world.get_card(card_id)
        spec = services.plugins.node_type(card.type).state
        if spec is None:
            return services.state.ensure_scope("node_document", card_id, schema_id="core.node_document")
        if spec.mode == "none":
            raise PermissionDeniedError("This plugin has no persistent state capability")
        scope_type, scope_id = identity or self.identity(card_id)
        with services.database.transaction(immediate=True) as db:
            # Adopt the old namespace once, without copying it into new sessions.
            # Mapping creation and adoption roll back with the enclosing mutation.
            legacy = db.execute("SELECT scope_id FROM state_scopes WHERE scope_kind='node_document' AND owner_id=?", (card_id,)).fetchone()
            if legacy and not db.execute("SELECT 1 FROM card_state_instances WHERE state_scope_id=?", (legacy["scope_id"],)).fetchone():
                legacy_id = "*" if scope_type == "shared" else self.default_session(card_id)
                db.execute("INSERT OR IGNORE INTO card_state_instances(card_id, scope_type, scope_id, state_scope_id) VALUES(?,?,?,?)",
                           (card_id, scope_type, legacy_id, legacy["scope_id"]))
            row = db.execute("SELECT s.* FROM card_state_instances c JOIN state_scopes s ON s.scope_id=c.state_scope_id WHERE c.card_id=? AND c.scope_type=? AND c.scope_id=?",
                             (card_id, scope_type, scope_id)).fetchone()
            if row:
                return services.state.get_scope(row["scope_kind"], row["owner_id"])
            scope = services.state.ensure_scope("node_document", "card-state-" + str(uuid4()), schema_id="core.node_document")
            db.execute("INSERT INTO card_state_instances(card_id, scope_type, scope_id, state_scope_id) VALUES(?,?,?,?)",
                       (card_id, scope_type, scope_id, scope.scope_id))
            return scope

    def existing(self, card_id):
        with self.services.database.locked() as db:
            return [(row["scope_type"], row["scope_id"]) for row in db.execute(
                "SELECT scope_type, scope_id FROM card_state_instances WHERE card_id=?", (card_id,))]


@dataclass(frozen=True)
class BoundCardState:
    store: ScopedStateStore
    card_id: str
    identity: tuple[str, str]
    authorize: object = None

    def _scope(self):
        if self.authorize:
            self.authorize()
        if self.identity[0] == "session" and self.identity[1] != DEFAULT_SESSION:
            with self.store.services.database.locked() as db:
                if not db.execute("SELECT 1 FROM conversation_sessions WHERE id=?", (self.identity[1],)).fetchone():
                    raise ResourceValidationError("The selected conversation session no longer exists")
        return self.store.scope(self.card_id, self.identity)

    def get(self):
        record = self.store.services.state.get_record(self._scope(), "data")
        return {"value": record.value, "revision": record.revision}

    def set(self, value, expected_revision=None):
        try:
            valid = isinstance(value, dict) and len(json.dumps(value, allow_nan=False).encode("utf-8")) <= 256 * 1024
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ResourceValidationError("Card state must be a JSON object up to 256 KiB")
        with self.store.services.database.transaction(immediate=True):
            self.store.services.state.set(self._scope(), "data", value, expected_revision=expected_revision)
            return self.get()

    def update(self, patch, expected_revision=None):
        with self.store.services.database.transaction(immediate=True):
            return self.set({**self.get()["value"], **patch}, expected_revision)

    def delete(self, expected_revision=None):
        return self.set({}, expected_revision)
