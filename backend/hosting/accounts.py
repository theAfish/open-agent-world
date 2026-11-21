"""Small, server-owned account registry for hosted installations.

This database contains credentials, never workspace data.
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


class CodeThrottled(Exception):
    """Verification code sending cooldown or hourly limit has been reached."""

    def __init__(self, retry_after: int = 60):
        super().__init__("Too many verification code requests")
        self.retry_after = max(1, int(retry_after))


class AccountConflict(ValueError):
    """A unique account attribute (username, email, or phone) is already bound."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.@+\-]{0,63}\Z", re.ASCII)
_REGISTER_USERNAME = re.compile(r"[a-z][a-z0-9_.\-]{1,31}\Z", re.ASCII)
_EMAIL = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z",
    re.ASCII,
)
_CODE_PURPOSES = frozenset({
    "login", "register_email", "register_phone", "bind_email", "bind_phone", "reset_password",
})
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 32768, 8, 3
_MAX_PASSWORD_BYTES = 1024
_LOGIN_WINDOW = 15 * 60
_MAX_THROTTLE_BUCKETS = 4096
_MAX_CODE_ATTEMPTS = 5
_HOURLY_CODE_LIMIT = 10

DEFAULT_SESSION_SECONDS = 86400
PERSISTENT_SESSION_SECONDS = 30 * 86400
SLIDING_RENEWAL_INTERVAL = 3600


def normalize_register_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Username must start with a letter and contain 2–32 letters, digits, ., _, or -")
    cleaned = value.strip().casefold()
    if not _REGISTER_USERNAME.fullmatch(cleaned):
        raise ValueError("Username must start with a letter and contain 2–32 letters, digits, ., _, or -")
    return cleaned


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid email address")
    cleaned = value.strip().casefold()
    if not cleaned or len(cleaned) > 254 or ".." in cleaned or not _EMAIL.fullmatch(cleaned):
        raise ValueError("Invalid email address")
    return cleaned


def normalize_phone(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid phone number")
    cleaned = re.sub(r"[\s\-()]", "", value.strip())
    if re.fullmatch(r"1[3-9]\d{9}", cleaned):
        return "+86" + cleaned
    if re.fullmatch(r"\+?861[3-9]\d{9}", cleaned):
        return "+86" + cleaned[-11:]
    if re.fullmatch(r"\+[1-9]\d{7,14}", cleaned):
        return cleaned
    raise ValueError("Invalid phone number")


def detect_contact_channel(target: str) -> tuple[str, str]:
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Email or phone number is required")
    if "@" in target:
        return "email", normalize_email(target)
    return "sms", normalize_phone(target)


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    if phone.startswith("+86") and len(phone) == 14:
        return f"+86 {phone[3:6]}****{phone[-4:]}"
    if len(phone) >= 7:
        return f"{phone[:4]}****{phone[-3:]}"
    return "****"


def _validate_display_name(display_name: str | None) -> str | None:
    if display_name is None:
        return None
    if not isinstance(display_name, str):
        raise ValueError("Display name must contain at most 128 printable characters")
    cleaned = display_name.strip()
    if not cleaned:
        return None
    if len(cleaned) > 128 or any(ord(char) < 32 for char in cleaned):
        raise ValueError("Display name must contain at most 128 printable characters")
    return cleaned


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
                          r=_SCRYPT_R, p=_SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)


def validate_user_password(password: str) -> None:
    if (not isinstance(password, str) or len(password) < 8
            or len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES
            or not re.search(r"[A-Z]", password)
            or not re.search(r"[a-z]", password)
            or not re.search(r"[0-9]", password)):
        raise ValueError(
            "Password must be at least 8 characters and include uppercase letters, lowercase letters, and digits"
        )


def _hash_password(password: str, *, require_complexity: bool = False) -> str:
    if require_complexity:
        validate_user_password(password)
    else:
        valid_length = isinstance(password, str) and len(password.encode("utf-8")) <= _MAX_PASSWORD_BYTES
        has_complexity = valid_length and len(password) >= 8 and bool(
            re.search(r"[A-Z]", password) and re.search(r"[a-z]", password) and re.search(r"[0-9]", password)
        )
        has_passphrase = valid_length and len(password) >= 12
        if not (has_complexity or has_passphrase):
            raise ValueError(
                "Password must be at least 8 characters with uppercase, lowercase, and digits (or at least 12 characters)"
            )
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


def _hash_code(channel: str, target: str, purpose: str, code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{channel}:{target}:{purpose}:{code}".encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def _verify_code_hash(channel: str, target: str, purpose: str, code: str, encoded: str) -> bool:
    try:
        salt, _ = encoded.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_hash_code(channel, target, purpose, code, salt), encoded)


def _derive_unique_username(db: sqlite3.Connection, hint: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", hint.casefold()).lstrip("_.-")[:48]
    base = cleaned if cleaned and cleaned[0].isalnum() else f"u_{secrets.token_hex(4)}"
    candidate = base
    for _ in range(16):
        if db.execute("SELECT 1 FROM users WHERE username=?", (candidate,)).fetchone() is None:
            return candidate
        candidate = f"{base[:40]}_{secrets.token_hex(3)}"
    return f"u_{secrets.token_hex(8)}"


def _user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    keys = row.keys()
    phone = row["phone"] if "phone" in keys else None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"] if "email" in keys else None,
        "phone": phone,
        "phone_masked": mask_phone(phone),
        "has_password": bool(row["password_hash"]),
        "enabled": bool(row["enabled"]),
        "role": row["role"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"] if "last_login_at" in keys else None,
        "_credential_version": row["credential_version"],
    }


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
                CREATE TABLE IF NOT EXISTS verification_codes (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL CHECK(channel IN ('email', 'sms')),
                    target TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    requester_key TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS verification_codes_lookup
                    ON verification_codes(channel, target, purpose, created_at DESC);
                CREATE TABLE IF NOT EXISTS registration_tickets (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT,
                    email TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    created_at REAL NOT NULL
                );
            """)
            ticket_columns = {row["name"] for row in db.execute("PRAGMA table_info(registration_tickets)")}
            if "username" not in ticket_columns:
                db.execute("ALTER TABLE registration_tickets ADD COLUMN username TEXT")
            user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "role" not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin'))")
            for column, ddl in (
                ("email", "TEXT"),
                ("email_verified_at", "REAL"),
                ("phone", "TEXT"),
                ("phone_verified_at", "REAL"),
                ("last_login_at", "REAL"),
            ):
                if column not in user_columns:
                    db.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email) WHERE email IS NOT NULL")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_phone_unique ON users(phone) WHERE phone IS NOT NULL")

            session_columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            if "persistent" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0 CHECK(persistent IN (0, 1))")
            if "duration_seconds" not in session_columns:
                db.execute(f"ALTER TABLE sessions ADD COLUMN duration_seconds INTEGER NOT NULL DEFAULT {DEFAULT_SESSION_SECONDS}")

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

    def create_user(self, username: str, display_name: str | None = None, password: str | None = None, *,
                    role: str = "user", email: str | None = None, phone: str | None = None) -> dict:
        if role not in {"user", "admin"}:
            raise ValueError("Role must be user or admin")
        username = username.strip().casefold() if isinstance(username, str) else ""
        if not _USERNAME.fullmatch(username):
            raise ValueError("Username must be 1–64 ASCII letters, digits or . _ @ + - and start with a letter or digit")
        if display_name is not None and (not isinstance(display_name, str) or len(display_name) > 128
                                          or any(ord(char) < 32 for char in display_name)):
            raise ValueError("Display name must contain at most 128 printable characters")
        normalized_email = normalize_email(email) if email is not None else None
        normalized_phone = normalize_phone(phone) if phone is not None else None
        password_hash = _hash_password(password) if password is not None else None
        now = time.time()
        user_id = str(uuid.uuid4())
        try:
            with self._db() as db:
                db.execute(
                    """INSERT INTO users(
                        id, username, display_name, password_hash, created_at, role,
                        email, email_verified_at, phone, phone_verified_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, username, display_name, password_hash, now, role,
                        normalized_email, now if normalized_email else None,
                        normalized_phone, now if normalized_phone else None,
                    ),
                )
        except sqlite3.IntegrityError as error:
            message = str(error).lower()
            if "email" in message:
                raise AccountConflict("email_registered", "Email is already registered") from error
            if "phone" in message:
                raise AccountConflict("phone_registered", "Phone number is already registered") from error
            raise AccountConflict("username_registered", "Username is already registered") from error
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict | None:
        with self._db() as db:
            return _user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def list_users(self, *, query: str | None = None, enabled: bool | None = None) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM users ORDER BY created_at DESC, username").fetchall()
        users = [_user(row) for row in rows]
        if enabled is not None:
            users = [u for u in users if u["enabled"] is enabled]
        if query:
            q = query.strip().casefold()
            if q:
                users = [
                    u for u in users
                    if any(
                        value and q in str(value).casefold()
                        for value in (u["id"], u["username"], u["display_name"], u["email"], u["phone"], u["phone_masked"])
                    )
                ]
        return users

    def update_profile(self, user_id: str, *, display_name: str | None) -> dict:
        cleaned = _validate_display_name(display_name)
        with self._db() as db:
            changed = db.execute("UPDATE users SET display_name=? WHERE id=?", (cleaned, user_id)).rowcount
            if not changed:
                raise ValueError("Unknown user")
            return _user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def set_password(self, user_id: str, password: str) -> None:
        password_hash = _hash_password(password)
        with self._db() as db:
            changed = db.execute("UPDATE users SET password_hash=?, credential_version=credential_version+1 WHERE id=?",
                                 (password_hash, user_id)).rowcount
            if not changed:
                raise ValueError("Unknown user")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def change_password(self, user_id: str, new_password: str, *, current_password: str | None = None) -> dict:
        new_hash = _hash_password(new_password, require_complexity=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None or not row["enabled"]:
                raise ValueError("Unknown user")
            if row["password_hash"] is not None:
                if not _verify_password(current_password or "", row["password_hash"]):
                    raise PermissionError("Current password is incorrect")
            db.execute("UPDATE users SET password_hash=?, credential_version=credential_version+1 WHERE id=?",
                       (new_hash, user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return _user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be a boolean")
        with self._db() as db:
            changed = db.execute("UPDATE users SET enabled=?, credential_version=credential_version+1 WHERE id=?",
                                 (int(enabled), user_id)).rowcount
            if not changed:
                raise ValueError("Unknown user")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def set_role(self, user_id: str, role: str) -> None:
        """Operator-only promotion; existing sessions are revoked on role changes."""
        if role not in {"user", "admin"}:
            raise ValueError("Role must be user or admin")
        with self._db() as db:
            if not db.execute("UPDATE users SET role=?, credential_version=credential_version+1 WHERE id=?", (role, user_id)).rowcount:
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

    def _lookup_account_row(self, db: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
        if not isinstance(identifier, str):
            return None
        cleaned = identifier.strip()
        if not cleaned or len(cleaned) > 254:
            return None
        lowered = cleaned.casefold()
        if _USERNAME.fullmatch(lowered):
            row = db.execute("SELECT * FROM users WHERE username=?", (lowered,)).fetchone()
            if row is not None:
                return row
        if "@" in cleaned:
            try:
                return db.execute("SELECT * FROM users WHERE email=?", (normalize_email(cleaned),)).fetchone()
            except ValueError:
                return None
        try:
            return db.execute("SELECT * FROM users WHERE phone=?", (normalize_phone(cleaned),)).fetchone()
        except ValueError:
            return None

    def authenticate_password(self, username: str, password: str, remote_key: str) -> dict | None:
        raw = username.strip().casefold() if isinstance(username, str) else ""
        budget_key = raw if raw and len(raw) <= 254 and all(32 <= ord(c) < 127 for c in raw) else "<invalid>"
        self._consume_login_budget(budget_key, remote_key)
        with self._db() as db:
            row = self._lookup_account_row(db, username)
        if not _verify_password(password, row["password_hash"] if row else None):
            return None
        if not row["enabled"]:
            return None
        now = time.time()
        with self._db() as db:
            db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, row["id"]))
            return _user(db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())

    def issue_verification_code(self, channel: str, target: str, purpose: str, remote_key: str, *,
                                ttl: int = 300, cooldown: int = 60) -> tuple[str, str]:
        if channel not in {"email", "sms"}:
            raise ValueError("Verification channel must be email or sms")
        if purpose not in _CODE_PURPOSES:
            raise ValueError("Unsupported verification purpose")
        if not 60 <= ttl <= 1800 or not 0 <= cooldown <= 300:
            raise ValueError("Invalid verification lifetime")
        normalized = normalize_email(target) if channel == "email" else normalize_phone(target)
        now = time.time()
        code = f"{secrets.randbelow(1_000_000):06d}"
        encoded = _hash_code(channel, normalized, purpose, code)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM verification_codes WHERE created_at<=?", (now - 86400,))
            if purpose == "register_email":
                existing = db.execute("SELECT phone FROM users WHERE email=?", (normalized,)).fetchone()
                if existing is not None and existing["phone"] is not None:
                    raise AccountConflict("email_registered", "Email is already registered")
            latest = db.execute(
                "SELECT created_at FROM verification_codes WHERE channel=? AND target=? ORDER BY created_at DESC LIMIT 1",
                (channel, normalized),
            ).fetchone()
            if latest is not None and now - latest["created_at"] < cooldown:
                raise CodeThrottled(int(cooldown - (now - latest["created_at"])) + 1)
            hourly = db.execute(
                "SELECT COUNT(*) FROM verification_codes WHERE channel=? AND target=? AND created_at>?",
                (channel, normalized, now - 3600),
            ).fetchone()[0]
            if hourly >= _HOURLY_CODE_LIMIT:
                raise CodeThrottled(3600)
            peer_recent = db.execute(
                "SELECT COUNT(*) FROM verification_codes WHERE requester_key=? AND created_at>?",
                (remote_key, now - _LOGIN_WINDOW),
            ).fetchone()[0]
            if peer_recent >= 30:
                raise CodeThrottled(_LOGIN_WINDOW)
            db.execute(
                "UPDATE verification_codes SET consumed_at=? WHERE channel=? AND target=? AND purpose=? AND consumed_at IS NULL",
                (now, channel, normalized, purpose),
            )
            db.execute(
                """INSERT INTO verification_codes(
                    id, channel, target, purpose, code_hash, attempts, expires_at, requester_key, created_at
                ) VALUES (?,?,?,?,?,0,?,?,?)""",
                (str(uuid.uuid4()), channel, normalized, purpose, encoded, now + ttl, remote_key, now),
            )
        return normalized, code

    def verify_verification_code(self, channel: str, target: str, purpose: str, code: str, remote_key: str, *,
                                 consume: bool = True) -> str | None:
        if channel not in {"email", "sms"} or purpose not in _CODE_PURPOSES:
            return None
        try:
            normalized = normalize_email(target) if channel == "email" else normalize_phone(target)
        except ValueError:
            return None
        self._consume_login_budget(f"code:{channel}:{normalized}", remote_key)
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code.strip()):
            return None
        cleaned_code = code.strip()
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM verification_codes
                   WHERE channel=? AND target=? AND purpose=? AND consumed_at IS NULL AND expires_at>?
                   ORDER BY created_at DESC LIMIT 1""",
                (channel, normalized, purpose, now),
            ).fetchone()
            if row is None or row["attempts"] >= _MAX_CODE_ATTEMPTS:
                return None
            if not _verify_code_hash(channel, normalized, purpose, cleaned_code, row["code_hash"]):
                attempts = row["attempts"] + 1
                consumed = now if attempts >= _MAX_CODE_ATTEMPTS else None
                db.execute("UPDATE verification_codes SET attempts=?, consumed_at=? WHERE id=?",
                           (attempts, consumed, row["id"]))
                return None
            if consume:
                db.execute("UPDATE verification_codes SET consumed_at=? WHERE id=?", (now, row["id"]))
        return normalized

    def start_email_registration(self, email: str, code: str, password: str, remote_key: str, *,
                                 username: str, display_name: str | None = None, ticket_ttl: int = 900) -> dict | None:
        normalized_username = normalize_register_username(username)
        normalized_email = normalize_email(email)
        cleaned_name = _validate_display_name(display_name)
        password_hash = _hash_password(password, require_complexity=True)
        with self._db() as db:
            existing_email = db.execute("SELECT phone FROM users WHERE email=?", (normalized_email,)).fetchone()
            if existing_email is not None and existing_email["phone"] is not None:
                raise AccountConflict("email_registered", "Email is already registered")
            existing_username = db.execute("SELECT email FROM users WHERE username=?", (normalized_username,)).fetchone()
            if existing_username is not None and existing_username["email"] != normalized_email:
                raise AccountConflict("username_registered", "Username is already taken")
        verified = self.verify_verification_code("email", normalized_email, "register_email", code, remote_key)
        if verified is None:
            return None
        ticket = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM registration_tickets WHERE expires_at<=? OR consumed_at IS NOT NULL", (now,))
            db.execute(
                """INSERT INTO registration_tickets(
                    token_hash, username, email, password_hash, display_name, expires_at, created_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (hashlib.sha256(ticket.encode()).hexdigest(), normalized_username, normalized_email,
                 password_hash, cleaned_name, now + ticket_ttl, now),
            )
        return {"register_ticket": ticket, "email": normalized_email, "username": normalized_username}

    def complete_registration(self, register_ticket: str, phone: str, code: str, remote_key: str) -> tuple[dict, bool]:
        if not isinstance(register_ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", register_ticket):
            raise ValueError("Registration session expired; verify your email again")
        normalized_phone = normalize_phone(phone)
        ticket_hash = hashlib.sha256(register_ticket.encode()).hexdigest()
        now = time.time()
        with self._db() as db:
            ticket = db.execute(
                "SELECT * FROM registration_tickets WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?",
                (ticket_hash, now),
            ).fetchone()
        if ticket is None:
            raise ValueError("Registration session expired; verify your email again")
        verified_phone = self.verify_verification_code("sms", normalized_phone, "register_phone", code, remote_key)
        if verified_phone is None:
            raise PermissionError("Invalid or expired verification code")

        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            ticket = db.execute(
                "SELECT * FROM registration_tickets WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?",
                (ticket_hash, now),
            ).fetchone()
            if ticket is None:
                raise ValueError("Registration session expired; verify your email again")
            db.execute("UPDATE registration_tickets SET consumed_at=? WHERE token_hash=?", (now, ticket_hash))

            email = ticket["email"]
            phone_owner = db.execute("SELECT * FROM users WHERE phone=?", (normalized_phone,)).fetchone()
            email_owner = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if email_owner is not None and phone_owner is not None and email_owner["id"] != phone_owner["id"]:
                raise AccountConflict("email_registered", "Email and phone belong to separate existing accounts")

            if phone_owner is not None:
                if not phone_owner["enabled"]:
                    raise ValueError("Account is unavailable")
                display = phone_owner["display_name"] or ticket["display_name"]
                db.execute(
                    """UPDATE users SET email=?, email_verified_at=?, password_hash=?,
                       display_name=?, last_login_at=?, credential_version=credential_version+1
                       WHERE id=?""",
                    (email, now, ticket["password_hash"], display, now, phone_owner["id"]),
                )
                return _user(db.execute("SELECT * FROM users WHERE id=?", (phone_owner["id"],)).fetchone()), True

            if email_owner is not None:
                if not email_owner["enabled"]:
                    raise ValueError("Account is unavailable")
                display = email_owner["display_name"] or ticket["display_name"]
                db.execute(
                    """UPDATE users SET phone=?, phone_verified_at=?, password_hash=?,
                       display_name=?, last_login_at=?, credential_version=credential_version+1
                       WHERE id=?""",
                    (normalized_phone, now, ticket["password_hash"], display, now, email_owner["id"]),
                )
                return _user(db.execute("SELECT * FROM users WHERE id=?", (email_owner["id"],)).fetchone()), True

            requested_username = ticket["username"] if "username" in ticket.keys() and ticket["username"] else None
            if requested_username:
                if db.execute("SELECT 1 FROM users WHERE username=?", (requested_username,)).fetchone() is not None:
                    raise AccountConflict("username_registered", "Username is already taken")
                username = requested_username
            else:
                username = _derive_unique_username(db, email.split("@", 1)[0])
            display_name = ticket["display_name"] or username
            user_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO users(
                    id, username, display_name, password_hash, created_at, role,
                    email, email_verified_at, phone, phone_verified_at, last_login_at
                ) VALUES (?,?,?,?,?,'user',?,?,?,?,?)""",
                (user_id, username, display_name, ticket["password_hash"], now,
                 email, now, normalized_phone, now, now),
            )
            return _user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()), False

    def authenticate_code(self, target: str, code: str, remote_key: str) -> dict | None:
        channel, normalized = detect_contact_channel(target)
        verified = self.verify_verification_code(channel, normalized, "login", code, remote_key)
        if verified is None:
            return None
        column = "email" if channel == "email" else "phone"
        now = time.time()
        with self._db() as db:
            row = db.execute(f"SELECT * FROM users WHERE {column}=? AND enabled=1", (normalized,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, row["id"]))
            return _user(db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())

    def reset_password_with_code(self, target: str, code: str, new_password: str, remote_key: str) -> dict | None:
        password_hash = _hash_password(new_password, require_complexity=True)
        channel, normalized = detect_contact_channel(target)
        verified = self.verify_verification_code(channel, normalized, "reset_password", code, remote_key)
        if verified is None:
            return None
        column = "email" if channel == "email" else "phone"
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT * FROM users WHERE {column}=? AND enabled=1", (normalized,)).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE users SET password_hash=?, credential_version=credential_version+1, last_login_at=? WHERE id=?",
                (password_hash, now, row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            return _user(db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())

    def bind_contact(self, user_id: str, target: str, code: str, remote_key: str) -> dict:
        channel, normalized = detect_contact_channel(target)
        purpose = "bind_email" if channel == "email" else "bind_phone"
        verified = self.verify_verification_code(channel, normalized, purpose, code, remote_key)
        if verified is None:
            raise PermissionError("Invalid or expired verification code")
        column = "email" if channel == "email" else "phone"
        verified_col = "email_verified_at" if channel == "email" else "phone_verified_at"
        now = time.time()
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM users WHERE id=? AND enabled=1", (user_id,)).fetchone()
                if row is None:
                    raise ValueError("Unknown user")
                db.execute(f"UPDATE users SET {column}=?, {verified_col}=? WHERE id=?", (normalized, now, user_id))
                return _user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        except sqlite3.IntegrityError as error:
            conflict_code = "email_registered" if channel == "email" else "phone_registered"
            raise AccountConflict(conflict_code, "Contact is already bound to another account") from error

    def create_session(self, user_id: str, seconds: int = 3600, *, credential_version: int | None = None,
                       persistent: bool = False) -> str:
        if not 1 <= seconds <= PERSISTENT_SESSION_SECONDS:
            raise ValueError(f"Session duration must be between 1 and {PERSISTENT_SESSION_SECONDS} seconds")
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
            db.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at,created_at,persistent,duration_seconds) VALUES (?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), user_id, now + seconds, now, int(bool(persistent)), seconds),
            )
        return token

    def get_session(self, token: str | None, *, touch: bool = False) -> dict | None:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self._db() as db:
            row = db.execute("""SELECT users.*,
                    sessions.expires_at AS session_expires_at,
                    sessions.persistent AS session_persistent,
                    sessions.duration_seconds AS session_duration
                FROM sessions
                JOIN users ON users.id=sessions.user_id
                WHERE token_hash=? AND expires_at>? AND users.enabled=1""",
                             (token_hash, now)).fetchone()
            user = _user(row)
            if not user:
                return None
            expires_at = row["session_expires_at"]
            persistent = bool(row["session_persistent"])
            duration = int(row["session_duration"] or DEFAULT_SESSION_SECONDS)
            renewed = None
            if touch and persistent and duration > SLIDING_RENEWAL_INTERVAL and expires_at - now <= duration - SLIDING_RENEWAL_INTERVAL:
                expires_at = now + duration
                db.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (expires_at, token_hash))
                renewed = duration
            user["session_expires_at"] = expires_at
            user["session_persistent"] = persistent
            user["session_duration"] = duration
            user["session_renewed"] = renewed
            return user

    def count_sessions(self, user_id: str) -> int:
        with self._db() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id=? AND expires_at>?", (user_id, time.time())
            ).fetchone()[0])

    def revoke_other_sessions(self, user_id: str, keep_token: str | None) -> int:
        with self._db() as db:
            if keep_token and isinstance(keep_token, str) and len(keep_token) <= 256:
                keep_hash = hashlib.sha256(keep_token.encode()).hexdigest()
                return db.execute(
                    "DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (user_id, keep_hash)
                ).rowcount
            return db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,)).rowcount

    def revoke_session(self, token: str | None) -> None:
        if not isinstance(token, str) or len(token) > 256:
            return
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
