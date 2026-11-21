from contextlib import AsyncExitStack
import sqlite3
import time
from uuid import uuid4

import httpx
import pytest
from starlette.testclient import TestClient

from backend.hosting.accounts import PERSISTENT_SESSION_SECONDS, AccountStore
from backend.hosting.configuration import GatewaySettings, HostedProfile
from backend.hosting.gateway import create_gateway
from backend.hosting.verification import VerificationDispatcher, VerificationSettings
from backend.hosting.worker import WorkerConfig, create_worker_app

ORIGIN = "https://oaw.example"
PASSWORD = "PassWord1"


class CapturingDispatcher(VerificationDispatcher):
    def __init__(self):
        super().__init__(VerificationSettings(email_mode="log", sms_mode="log"))
        self.codes: list[tuple[str, str, str, str]] = []

    async def send(self, channel: str, target: str, code: str, purpose: str) -> None:
        self.codes.append((channel, target, code, purpose))

    def latest_code(self, channel: str, purpose: str) -> str:
        for ch, _, code, purp in reversed(self.codes):
            if ch == channel and purp == purpose:
                return code
        raise AssertionError(f"No {channel} code recorded for {purpose}")


def test_schema_migration_preserves_legacy_users_and_sessions(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                password_hash TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                credential_version INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            );
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        db.execute(
            "INSERT INTO users(id, username, display_name, enabled, credential_version, created_at, role) VALUES (?,?,?,?,?,?,?)",
            ("00000000-0000-4000-8000-000000000001", "legacy", "Legacy User", 1, 1, 1000.0, "admin"),
        )
    store = AccountStore(path)
    legacy = store.get_user("00000000-0000-4000-8000-000000000001")
    assert legacy["username"] == "legacy" and legacy["role"] == "admin" and legacy["email"] is None
    token = store.create_session(legacy["id"], persistent=True, seconds=PERSISTENT_SESSION_SECONDS)
    loaded = store.get_session(token)
    assert loaded["id"] == legacy["id"] and loaded["session_persistent"] is True


@pytest.mark.asyncio
async def test_two_step_registration_auto_provisions_isolated_worker_and_enforces_user_data_boundary(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    dispatcher = CapturingDispatcher()
    workers: dict[str, object] = {}
    stack = AsyncExitStack()

    async def auto_provision(user: dict):
        idx = len(workers)
        profile = HostedProfile(
            str(uuid4()), user["id"], "studio", f"{user['username']} workspace",
            "builder", f"http://worker-{idx}:8000", f"{idx + 11:02x}" * 32,
        )
        worker_app = create_worker_app(WorkerConfig(
            profile.profile_id, profile.user_id, profile.application_id, profile.mode,
            ORIGIN, profile.secret, tmp_path / f"worker-{idx}", agent_runtime="core.mock",
        ))
        await stack.enter_async_context(worker_app.router.lifespan_context(worker_app))
        workers[profile.upstream] = worker_app
        gateway.app.state.gateway.settings = GatewaySettings(
            ORIGIN, store.path, (*gateway.app.state.gateway.settings.profiles, profile)
        )

    async def transport_dispatch(request):
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        return await httpx.ASGITransport(app=workers[origin]).handle_async_request(request)

    gateway = create_gateway(
        GatewaySettings(ORIGIN, store.path, ()),
        frontend,
        store=store,
        verification=dispatcher,
        auto_provision=auto_provision,
        transport=httpx.MockTransport(transport_dispatch),
    )

    async with stack:
        await stack.enter_async_context(gateway.app.router.lifespan_context(gateway.app))
        alice_client = await stack.enter_async_context(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN)
        )
        bob_client = await stack.enter_async_context(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN)
        )
        headers = {"Origin": ORIGIN}

        me_anon = (await alice_client.get("/auth/me")).json()
        assert me_anon["registration_enabled"] is True and "code" in me_anon["login_methods"]

        # Cannot skip Step 1 with a forged registration ticket
        assert (await alice_client.post(
            "/auth/register/complete",
            json={"register_ticket": "a" * 43, "phone": "13800001111", "code": "123456"},
            headers=headers,
        )).status_code == 422

        # Register Alice: Step 1 (Email) -> Step 2 (Phone)
        assert (await alice_client.post(
            "/auth/code/send", json={"target": "Alice@Example.com", "purpose": "register_email"}, headers=headers
        )).status_code == 200
        email_code = dispatcher.latest_code("email", "register_email")

        # Weak passwords (<8 chars or missing upper/lower/digit) do not consume the valid email verification code
        for weak in ("Ab12345", "lowercase1", "UPPERCASE1", "NoDigitsAb"):
            assert (await alice_client.post(
                "/auth/register/email",
                json={"username": "alice_user", "email": "Alice@Example.com", "code": email_code, "password": weak},
                headers=headers,
            )).status_code == 422
        step1 = await alice_client.post(
            "/auth/register/email",
            json={"username": "alice_user", "email": "Alice@Example.com", "code": email_code, "password": PASSWORD},
            headers=headers,
        )
        assert step1.status_code == 200
        ticket = step1.json()["register_ticket"]

        assert (await alice_client.post(
            "/auth/code/send", json={"target": "138 0000 1111", "purpose": "register_phone"}, headers=headers
        )).status_code == 200
        sms_code = dispatcher.latest_code("sms", "register_phone")

        # Cross-purpose code reuse (using email code for SMS) fails
        assert (await alice_client.post(
            "/auth/register/complete",
            json={"register_ticket": ticket, "phone": "13800001111", "code": email_code},
            headers=headers,
        )).status_code == 401

        step2 = await alice_client.post(
            "/auth/register/complete",
            json={"register_ticket": ticket, "phone": "13800001111", "code": sms_code, "remember_me": True},
            headers=headers,
        )
        assert step2.status_code == 200 and step2.json()["merged"] is False
        assert f"Max-Age={PERSISTENT_SESSION_SECONDS}" in step2.headers["set-cookie"]

        alice_me = (await alice_client.get("/auth/me")).json()
        alice_instance = alice_me["instance"]["id"]
        assert alice_me["user"]["email"] == "alice@example.com"
        assert alice_me["user"]["phone_masked"] == "+86 138****1111"

        # Register Bob into a separate isolated workspace (verify duplicate username is rejected without burning OTP)
        await bob_client.post("/auth/code/send", json={"target": "bob@example.com", "purpose": "register_email"}, headers=headers)
        bob_email_code = dispatcher.latest_code("email", "register_email")
        dup_username = await bob_client.post(
            "/auth/register/email",
            json={"username": "ALICE_USER", "email": "bob@example.com", "code": bob_email_code, "password": PASSWORD},
            headers=headers,
        )
        assert dup_username.status_code == 409 and dup_username.json()["error"]["code"] == "username_registered"

        b_step1 = await bob_client.post(
            "/auth/register/email",
            json={"username": "bob_user", "email": "bob@example.com", "code": bob_email_code, "password": PASSWORD},
            headers=headers,
        )
        await bob_client.post("/auth/code/send", json={"target": "13900002222", "purpose": "register_phone"}, headers=headers)
        await bob_client.post(
            "/auth/register/complete",
            json={"register_ticket": b_step1.json()["register_ticket"], "phone": "13900002222",
                  "code": dispatcher.latest_code("sms", "register_phone")},
            headers=headers,
        )
        bob_me = (await bob_client.get("/auth/me")).json()
        assert bob_me["instance"]["id"] != alice_instance

        # Alice writes private data; Bob cannot read it or forge Alice's instance/user_id
        created = await alice_client.post(
            "/api/nodes",
            json={"type": "text", "name": "Alice secret", "content": "private"},
            headers={**headers, "X-OAW-Instance": alice_instance},
        )
        assert created.status_code == 201
        node_id = created.json()["id"]
        assert (await bob_client.get(f"/api/nodes/{node_id}?user_id={alice_me['user']['id']}")).status_code == 404
        assert (await bob_client.post(
            "/api/nodes", json={"type": "text"}, headers={**headers, "X-OAW-Instance": alice_instance}
        )).status_code == 409

        # Alice can sign in with email+password, phone+password, and SMS code
        for identifier in ("alice@example.com", "13800001111"):
            res = await alice_client.post(
                "/auth/login", json={"username": identifier, "password": PASSWORD, "remember_me": True}, headers=headers
            )
            assert res.status_code == 200 and res.json()["user"]["id"] == alice_me["user"]["id"]


def test_identity_auto_merge_and_conflict_rejection(tmp_path, monkeypatch):
    from backend.hosting import accounts
    clock = [10_000.0]
    monkeypatch.setattr(accounts.time, "time", lambda: clock[0])

    store = AccountStore(tmp_path / "accounts.sqlite3")
    existing = store.create_user("zyc_phone", "ZYC", PASSWORD, phone="13800008801")
    other = store.create_user("other_user", "Other", PASSWORD, email="taken@example.com", phone="13700009999")
    dispatcher = CapturingDispatcher()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    profile = HostedProfile(str(uuid4()), existing["id"], "studio", "ZYC studio", "builder", "http://w:8000", "ab" * 32)
    gateway = create_gateway(GatewaySettings(ORIGIN, store.path, (profile,)), frontend, store=store, verification=dispatcher)

    with TestClient(gateway, base_url=ORIGIN) as client:
        headers = {"Origin": ORIGIN}
        # Registering already-bound email is rejected upfront with 409
        assert client.post(
            "/auth/code/send", json={"target": "taken@example.com", "purpose": "register_email"}, headers=headers
        ).status_code == 409

        # Verify new email zyc@example.com, then bind existing phone 13800008801 -> auto-merges into existing user_id!
        assert client.post(
            "/auth/code/send", json={"target": "zyc@example.com", "purpose": "register_email"}, headers=headers
        ).status_code == 200
        t1 = client.post(
            "/auth/register/email",
            json={"username": "zyc_new", "email": "zyc@example.com", "code": dispatcher.latest_code("email", "register_email"), "password": "NewPass99"},
            headers=headers,
        ).json()["register_ticket"]

        assert client.post(
            "/auth/code/send", json={"target": "13800008801", "purpose": "register_phone"}, headers=headers
        ).status_code == 200
        merged = client.post(
            "/auth/register/complete",
            json={"register_ticket": t1, "phone": "13800008801", "code": dispatcher.latest_code("sms", "register_phone")},
            headers=headers,
        )
        assert merged.status_code == 200
        assert merged.json()["merged"] is True
        assert merged.json()["user"]["id"] == existing["id"]

        me = client.get("/auth/me").json()
        assert me["user"]["id"] == existing["id"]
        assert me["user"]["email"] == "zyc@example.com"
        assert me["instance"]["id"] == profile.profile_id
        assert other["id"] != existing["id"]


def test_verification_code_security_and_aliyun_sms_signing(tmp_path, monkeypatch):
    from backend.hosting import accounts
    clock = [50_000.0]
    monkeypatch.setattr(accounts.time, "time", lambda: clock[0])

    store = AccountStore(tmp_path / "accounts.sqlite3")
    sms_requests = []

    def mock_aliyun(request: httpx.Request) -> httpx.Response:
        sms_requests.append(request)
        return httpx.Response(200, json={"Code": "OK", "Message": "OK"})

    dispatcher = VerificationDispatcher(
        VerificationSettings(
            email_mode="log",
            sms_mode="aliyun",
            aliyun_access_key_id="ak-test",
            aliyun_access_key_secret="sk-secret",
            aliyun_sign_name="OAW",
            aliyun_template_code="SMS_10001",
        ),
        transport=httpx.MockTransport(mock_aliyun),
    )
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    gateway = create_gateway(GatewaySettings(ORIGIN, store.path), frontend, store=store, verification=dispatcher)

    with TestClient(gateway, base_url=ORIGIN) as client:
        headers = {"Origin": ORIGIN}
        res = client.post("/auth/code/send", json={"target": "13812345678", "purpose": "register_phone"}, headers=headers)
        assert res.status_code == 200
        assert len(sms_requests) == 1
        body_text = sms_requests[0].content.decode("utf-8")
        assert "PhoneNumbers=13812345678" in body_text and "Signature=" in body_text

        # Cooldown within 60s returns 429 with Retry-After
        throttled = client.post("/auth/code/send", json={"target": "+86 138-1234-5678", "purpose": "register_phone"}, headers=headers)
        assert throttled.status_code == 429 and int(throttled.headers["retry-after"]) > 0

        # Plaintext OTP is never stored in SQLite
        clock[0] += 65
        _, raw_code = store.issue_verification_code("email", "sec@example.com", "login", "peer-1")
        assert raw_code.encode() not in store.path.read_bytes()

        # 5 wrong attempts burn the OTP immediately
        for _ in range(5):
            assert store.verify_verification_code("email", "sec@example.com", "login", "000000", "peer-1") is None
        assert store.verify_verification_code("email", "sec@example.com", "login", raw_code, "peer-1") is None


def test_persistent_session_sliding_renewal_and_device_management(tmp_path, monkeypatch):
    from backend.hosting import accounts
    clock = [100_000.0]
    monkeypatch.setattr(accounts.time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "time", lambda: clock[0])

    store = AccountStore(tmp_path / "accounts.sqlite3")
    user = store.create_user("zyc", "ZYC", PASSWORD, email="zyc@example.com", phone="13800008801")
    dispatcher = CapturingDispatcher()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    gateway = create_gateway(GatewaySettings(ORIGIN, store.path), frontend, store=store, verification=dispatcher)

    with TestClient(gateway, base_url=ORIGIN) as dev1, TestClient(gateway, base_url=ORIGIN) as dev2:
        headers = {"Origin": ORIGIN}
        r1 = dev1.post("/auth/login", json={"username": "zyc@example.com", "password": PASSWORD, "remember_me": True}, headers=headers)
        assert r1.status_code == 200 and f"Max-Age={PERSISTENT_SESSION_SECONDS}" in r1.headers["set-cookie"]

        # Second device signs in via verification code
        dev2.post("/auth/code/send", json={"target": "13800008801", "purpose": "login"}, headers=headers)
        r2 = dev2.post(
            "/auth/login/code",
            json={"target": "13800008801", "code": dispatcher.latest_code("sms", "login"), "remember_me": False},
            headers=headers,
        )
        assert r2.status_code == 200 and "Max-Age=86400" in r2.headers["set-cookie"]

        # Advance 2 days: persistent session on dev1 slides forward to a full 30 days, while dev2's 24h session expired!
        clock[0] += 2 * 86400
        me1 = dev1.get("/auth/me")
        assert me1.json()["authenticated"] is True
        assert f"Max-Age={PERSISTENT_SESSION_SECONDS}" in me1.headers["set-cookie"]
        assert dev1.get("/auth/me").json()["user"]["session_expires_at"] == pytest.approx(clock[0] + PERSISTENT_SESSION_SECONDS)
        assert dev2.get("/auth/me").json()["authenticated"] is False

        # Sign dev2 back in, check active_sessions == 2, then revoke other devices from dev1
        dev2.post("/auth/login", json={"username": "zyc", "password": PASSWORD, "remember_me": True}, headers=headers)
        assert dev1.get("/auth/account").json()["user"]["active_sessions"] == 2
        rev = dev1.post("/auth/account/sessions/revoke-others", headers=headers)
        assert rev.status_code == 200 and rev.json()["active_sessions"] == 1
        assert dev2.get("/auth/me").json()["authenticated"] is False
        assert dev1.get("/auth/me").json()["authenticated"] is True

        # Password reset via code revokes dev1's session and issues a new one
        clock[0] += 65
        dev2.post("/auth/code/send", json={"target": "zyc@example.com", "purpose": "reset_password"}, headers=headers)
        reset = dev2.post(
            "/auth/password/reset",
            json={"target": "zyc@example.com", "code": dispatcher.latest_code("email", "reset_password"), "password": "BrandNew88"},
            headers=headers,
        )
        assert reset.status_code == 200
        assert dev1.get("/auth/me").json()["authenticated"] is False
        assert dev2.get("/auth/me").json()["authenticated"] is True


def test_admin_user_management_authorization_and_session_revocation(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    admin = store.create_user("admin", "Admin", PASSWORD, role="admin", email="admin@example.com")
    bob = store.create_user("bob", "Bob", PASSWORD, email="bob@example.com", phone="13900001234")
    profile = HostedProfile(str(uuid4()), bob["id"], "app", "Bob Workspace", "runtime", "http://w:8000", "cd" * 32)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    gateway = create_gateway(
        GatewaySettings(ORIGIN, store.path, (profile,)),
        frontend,
        store=store,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(401) if req.url.path == "/api/deployment" else httpx.Response(200, json={"ok": True})
        ),
    )

    with TestClient(gateway, base_url=ORIGIN) as admin_client, TestClient(gateway, base_url=ORIGIN) as bob_client:
        headers = {"Origin": ORIGIN}
        bob_client.post("/auth/login", json={"username": "bob", "password": PASSWORD}, headers=headers)
        assert bob_client.get("/api/world").status_code == 200

        # Non-admin cannot access or mutate user management
        assert bob_client.get("/hosting/admin/users", headers=headers).status_code == 403
        assert bob_client.patch(f"/hosting/admin/users/{bob['id']}", json={"role": "admin"}, headers=headers).status_code == 403

        admin_client.post("/auth/login", json={"username": "admin", "password": PASSWORD}, headers=headers)
        assert admin_client.get("/hosting/admin/users").status_code == 200
        matched = admin_client.get("/hosting/admin/users?q=1234&status=enabled").json()["users"]
        assert len(matched) == 1 and matched[0]["id"] == bob["id"] and matched[0]["instance"]["id"] == profile.profile_id

        # Admin cannot disable or demote their own account
        assert admin_client.patch(
            f"/hosting/admin/users/{admin['id']}", json={"enabled": False}, headers=headers
        ).status_code == 409
        assert admin_client.patch(
            f"/hosting/admin/users/{admin['id']}", json={"role": "user"}, headers=headers
        ).status_code == 409

        # Admin creates a new user with a unique username
        created = admin_client.post(
            "/hosting/admin/users",
            json={"username": "carol", "email": "carol@example.com", "phone": "13711112222", "password": PASSWORD},
            headers=headers,
        )
        assert created.status_code == 201 and created.json()["user"]["username"] == "carol"
        assert created.json()["user"]["phone_masked"] == "+86 137****2222"

        # Admin disables Bob -> Bob's active session is immediately rejected on private API
        disabled = admin_client.patch(f"/hosting/admin/users/{bob['id']}", json={"enabled": False}, headers=headers)
        assert disabled.status_code == 200 and disabled.json()["user"]["enabled"] is False
        assert bob_client.get("/api/world").status_code == 401
