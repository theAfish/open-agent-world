"""Small, server-owned account registry for hosted installations.

This database contains credentials, never workspace data. Accounts and external
identity links are provisioned by an operator, not by public HTTP endpoints.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path


class LoginThrottled(Exception):
    """The persistent sign-in budget has been exhausted."""


_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.@+\-]{0,63}\Z", re.ASCII)
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 32768, 8, 3
_MAX_PASSWORD_BYTES = 1024
_LOGIN_WINDOW = 15 * 60
_MAX_THROTTLE_BUCKETS = 4096


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
                          r=_SCRYPT_R, p=_SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)


def _hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) or len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError("Password must contain at least 12 characters and at most 1024 UTF-8 bytes")
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${_derive(password, salt).hex()}"


def _verify_password(password: str, encoded: str | None) -> bool:
    # Unknown accounts, disabled accounts and OIDC-only accounts take the same
    # expensive password-hashing path. No login response reveals account existence.
    try:
        valid_input = isinstance(password, str) and len(password.encode("utf-8")) <= _MAX_PASSWORD_BYTES
    except UnicodeError:
        valid_input = False
    candidate = password if valid_input else "invalid-password"
    salt, expected, valid_hash = b"oaw-dummy-salt-v1", bytes(32), False
    if encoded:
        try:
            scheme, salt_hex, digest_hex = encoded.split("$")
            salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
            valid_hash = scheme == "scrypt-v1" and len(salt) == 16 and len(expected) == 32
        except (ValueError, TypeError):
            pass
    actual = _derive(candidate, salt)
    return hmac.compare_digest(actual, expected) and valid_hash and valid_input


def _user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "enabled": bool(row["enabled"]), "_credential_version": row["credential_version"]}


class AccountStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # SQLite's WAL and SHM inherit database permissions. Pre-create the main
        # file privately before SQLite can create auxiliary files on POSIX hosts.
        if not self.path.exists():
            import os
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        self.path.chmod(0o600)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT,
                    password_hash TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    credential_version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS external_identities (
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    PRIMARY KEY(issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_budgets (
                    bucket TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    expires_at REAL NOT NULL
                );
            """)

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_user(self, username: str, display_name: str | None = None, password: str | None = None) -> dict:
        username = username.strip().casefold() if isinstance(username, str) else ""
        if not _USERNAME.fullmatch(username):
            raise ValueError("Username must be 1–64 ASCII letters, digits or . _ @ + - and start with a letter or digit")
        if display_name is not None and (not isinstance(display_name, str) or len(display_name) > 128
                                          or any(ord(char) < 32 for char in display_name)):
            raise ValueError("Display name must contain at most 128 printable characters")
        password_hash = _hash_password(password) if password is not None else None
        user_id = str(uuid.uuid4())
        try:
            with self._db() as db:
                db.execute("INSERT INTO users(id,username,display_name,password_hash,created_at) VALUES (?,?,?,?,?)",
                           (user_id, username, display_name, password_hash, time.time()))
        except sqlite3.IntegrityError as error:
            raise ValueError("Username is already registered") from error
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict | None:
        with self._db() as db:
            return _user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def list_users(self) -> list[dict]:
        with self._db() as db:
            return [_user(row) for row in db.execute("SELECT * FROM users ORDER BY username")]

    def set_password(self, user_id: str, password: str) -> None:
        password_hash = _hash_password(password)
        with self._db() as db:
            changed = db.execute("UPDATE users SET password_hash=?, credential_version=credential_version+1 WHERE id=?",
                                 (password_hash, user_id)).rowcount
            if not changed:
                raise ValueError("Unknown user")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be a boolean")
        with self._db() as db:
            changed = db.execute("UPDATE users SET enabled=?, credential_version=credential_version+1 WHERE id=?",
                                 (int(enabled), user_id)).rowcount
            if not changed:
                raise ValueError("Unknown user")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def link_identity(self, user_id: str, issuer: str, subject: str) -> None:
        if any(not isinstance(value, str) or not value or len(value) > 2048 for value in (issuer, subject)):
            raise ValueError("External identity requires issuer and subject")
        with self._db() as db:
            if db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
                raise ValueError("Unknown user")
            existing = db.execute("SELECT user_id FROM external_identities WHERE issuer=? AND subject=?",
                                  (issuer, subject)).fetchone()
            if existing:
                if existing["user_id"] != user_id:
                    raise ValueError("External identity is already bound to another user")
                return
            db.execute("INSERT INTO external_identities(issuer,subject,user_id) VALUES (?,?,?)",
                       (issuer, subject, user_id))

    def resolve_identity(self, issuer: str, subject: str) -> dict | None:
        with self._db() as db:
            return _user(db.execute("""SELECT users.* FROM external_identities
                JOIN users ON users.id=external_identities.user_id
                WHERE issuer=? AND subject=? AND users.enabled=1""", (issuer, subject)).fetchone())

    def _consume_login_budget(self, username: str, remote_key: str) -> None:
        now = time.time()
        buckets = [("user:" + username, 10), ("peer:" + remote_key, 60)]
        # BEGIN IMMEDIATE makes check-and-consume atomic across gateway workers.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_budgets WHERE expires_at<=?", (now,))
            count = db.execute("SELECT COUNT(*) FROM login_budgets").fetchone()[0]
            for name, limit in buckets:
                key = hashlib.sha256(name.encode(errors="replace")).hexdigest()
                row = db.execute("SELECT attempts FROM login_budgets WHERE bucket=?", (key,)).fetchone()
                if row and row["attempts"] >= limit or not row and count >= _MAX_THROTTLE_BUCKETS:
                    raise LoginThrottled()
                db.execute("""INSERT INTO login_budgets(bucket,attempts,expires_at) VALUES (?,1,?)
                    ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1""", (key, now + _LOGIN_WINDOW))
                if row is None:
                    count += 1

    def authenticate_password(self, username: str, password: str, remote_key: str) -> dict | None:
        username = username.strip().casefold() if isinstance(username, str) else ""
        if not _USERNAME.fullmatch(username):
            username = "<invalid>"
        self._consume_login_budget(username, remote_key)
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not _verify_password(password, row["password_hash"] if row else None):
            return None
        if not row["enabled"]:
            return None
        return _user(row)

    def create_session(self, user_id: str, seconds: int = 3600, *, credential_version: int | None = None) -> str:
        if not 1 <= seconds <= 86400:
            raise ValueError("Session duration must be between 1 and 86400 seconds")
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT enabled,credential_version FROM users WHERE id=?", (user_id,)).fetchone()
            if (user is None or not user["enabled"] or credential_version is not None
                    and credential_version != user["credential_version"]):
                raise ValueError("Account is unavailable")
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            # Bound stored sessions and revoke oldest when a user signs in often.
            db.execute("""DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN
                (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 9)""", (user_id, user_id))
            db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES (?,?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), user_id, now + seconds, now))
        return token

    def get_session(self, token: str | None) -> dict | None:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self._db() as db:
            row = db.execute("""SELECT users.*,sessions.expires_at AS session_expires_at FROM sessions
                JOIN users ON users.id=sessions.user_id
                WHERE token_hash=? AND expires_at>? AND users.enabled=1""",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            user = _user(row)
            if user:
                user["session_expires_at"] = row["session_expires_at"]
            return user

    def revoke_session(self, token: str | None) -> None:
        if not isinstance(token, str) or len(token) > 256:
            return
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
