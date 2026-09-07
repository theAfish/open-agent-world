"""Encrypted bindings scoped to a live node ID, never included in node state."""
import json
from cryptography.fernet import InvalidToken
from backend.errors import ResourceValidationError


class ExecutionCredentialStore:
    def __init__(self, settings_store, world):
        self.settings_store = settings_store
        self.database = settings_store.database
        self.world = world

    def _key(self, node_id, reference):
        node = self.world.get_card(node_id)
        return "execution_credential:" + json.dumps([node_id, node.created_at.isoformat(), reference], separators=(",", ":"))

    def bind(self, node_id, reference, value):
        encrypted = self.settings_store._fernet(create=True).encrypt(value.encode()).decode()
        with self.database.transaction(immediate=True) as connection:
            connection.execute("INSERT INTO application_settings (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (self._key(node_id, reference), json.dumps(encrypted)))

    def unbind(self, node_id, reference):
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM application_settings WHERE key = ?", (self._key(node_id, reference),))

    def configured(self, node_id, reference):
        with self.database.locked() as connection:
            return connection.execute("SELECT 1 FROM application_settings WHERE key = ?",
                (self._key(node_id, reference),)).fetchone() is not None

    def resolve(self, node_id, reference):
        with self.database.locked() as connection:
            row = connection.execute("SELECT value_json FROM application_settings WHERE key = ?",
                (self._key(node_id, reference),)).fetchone()
        if row is None:
            raise ResourceValidationError(f"Credential requirement {reference!r} is unbound; bind it on this Environment Profile")
        try:
            return self.settings_store._fernet(create=False).decrypt(json.loads(row["value_json"]).encode()).decode()
        except (InvalidToken, ValueError, OSError):
            raise ResourceValidationError("Execution credential cannot be decrypted on this host; explicitly rebind it") from None
