import hashlib
import sqlite3

import pytest

from backend.hosting.accounts import AccountStore, LoginThrottled


PASSWORD = "a long unique test password"


def test_passwords_and_sessions_are_hashed_and_persist(tmp_path):
    path = tmp_path / "accounts.sqlite3"
    store = AccountStore(path)
    alice = store.create_user("Alice", "Alice Example", PASSWORD)
    assert store.authenticate_password("ALICE", PASSWORD, "peer")["id"] == alice["id"]
    assert store.authenticate_password("alice", "wrong-password", "peer") is None
    assert store.authenticate_password("missing", PASSWORD, "peer") is None
    token = store.create_session(alice["id"])
    reopened = AccountStore(path)
    assert reopened.get_session(token)["id"] == alice["id"]
    with sqlite3.connect(path) as db:
        encoded = db.execute("SELECT password_hash FROM users").fetchone()[0]
        stored_token = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert encoded.startswith("scrypt-v1$") and PASSWORD not in encoded
    assert stored_token == hashlib.sha256(token.encode()).hexdigest()
    assert token not in path.read_bytes().decode("latin1")
    assert "password_hash" not in store.get_user(alice["id"])


def test_password_reset_disable_and_logout_revoke_sessions(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user = store.create_user("alice", password=PASSWORD)
    token = store.create_session(user["id"])
    store.set_password(user["id"], "a newly chosen test password")
    assert store.get_session(token) is None
    with pytest.raises(ValueError, match="unavailable"):
        store.create_session(user["id"], credential_version=user["_credential_version"])
    token = store.create_session(user["id"])
    store.set_enabled(user["id"], False)
    assert store.get_session(token) is None
    assert store.authenticate_password("alice", "a newly chosen test password", "peer") is None
    with pytest.raises(ValueError):
        store.create_session(user["id"])
    store.set_enabled(user["id"], True)
    assert store.get_session(token) is None
    token = store.create_session(user["id"])
    store.revoke_session(token)
    assert store.get_session(token) is None


def test_identity_link_is_explicit_stable_and_cannot_move_to_another_user(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    alice = store.create_user("alice@example.com")
    bob = store.create_user("bob")
    assert store.resolve_identity("https://id.example", "alice@example.com") is None
    store.link_identity(alice["id"], "https://id.example", "external-subject")
    store.link_identity(alice["id"], "https://another.example", "different-subject")
    assert store.resolve_identity("https://id.example", "external-subject")["id"] == alice["id"]
    assert store.resolve_identity("https://another.example", "different-subject")["id"] == alice["id"]
    assert store.resolve_identity("https://another.example", "external-subject") is None
    with pytest.raises(ValueError, match="already bound"):
        store.link_identity(bob["id"], "https://id.example", "external-subject")
    store.set_enabled(alice["id"], False)
    assert store.resolve_identity("https://id.example", "external-subject") is None


def test_login_throttle_survives_restarts_and_expires(tmp_path, monkeypatch):
    from backend.hosting import accounts
    clock = [1000.0]
    monkeypatch.setattr(accounts.time, "time", lambda: clock[0])
    store = AccountStore(tmp_path / "accounts.sqlite3")
    # Test the persistent budget directly; password derivation is covered above.
    for _ in range(10):
        store._consume_login_budget("unknown-user", "peer")
    reopened = AccountStore(store.path)
    with pytest.raises(LoginThrottled):
        reopened.authenticate_password("unknown-user", PASSWORD, "different-peer")
    clock[0] += 901
    assert reopened.authenticate_password("unknown-user", PASSWORD, "peer") is None


def test_peer_throttle_and_bucket_limit_prevent_unbounded_username_spray(tmp_path, monkeypatch):
    from backend.hosting import accounts
    store = AccountStore(tmp_path / "accounts.sqlite3")
    for index in range(60):
        store._consume_login_budget(f"name-{index}", "same-peer")
    with pytest.raises(LoginThrottled):
        store._consume_login_budget("next-name", "same-peer")
    monkeypatch.setattr(accounts, "_MAX_THROTTLE_BUCKETS", 61)
    with pytest.raises(LoginThrottled):
        store._consume_login_budget("new-name", "new-peer")
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM login_budgets").fetchone()[0] == 61


def test_sessions_expire_and_are_bounded(tmp_path, monkeypatch):
    from backend.hosting import accounts
    clock = [1000.0]
    monkeypatch.setattr(accounts.time, "time", lambda: clock[0])
    store = AccountStore(tmp_path / "accounts.sqlite3")
    user = store.create_user("alice")
    tokens = []
    for _ in range(12):
        tokens.append(store.create_session(user["id"], 60))
        clock[0] += 1
    assert store.get_session(tokens[0]) is None
    assert store.get_session(tokens[1]) is None
    assert store.get_session(tokens[-1])["id"] == user["id"]
    clock[0] += 60
    assert store.get_session(tokens[-1]) is None
    assert store.get_session("made-up") is None


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_password_policy_and_case_insensitive_unique_names(tmp_path, password):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    with pytest.raises(ValueError, match="Password"):
        store.create_user("alice", password=password)
    store.create_user("Alice")
    with pytest.raises(ValueError, match="already registered"):
        store.create_user("ALICE")
