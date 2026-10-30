import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.hosting.accounts import AccountStore
from backend.hosting.auth import HostedAuth


ORIGIN = "https://oaw.example"
PASSWORD = "a long unique test password"


@pytest.fixture
def hosted_auth(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    store.create_user("alice", "Alice", PASSWORD)
    auth = HostedAuth(store, ORIGIN)
    with TestClient(Starlette(routes=auth.routes), base_url=ORIGIN) as client:
        yield store, auth, client


def login(client, username="alice", password=PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password}, headers={"Origin": ORIGIN})


def test_password_login_cookie_and_server_revocation(hosted_auth):
    store, _, client = hosted_auth
    assert client.get("/auth/me").json() == {"hosted": True, "authenticated": False, "user": None,
                                             "login_methods": ["password"]}
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    token = client.cookies["oaw_session"]
    me = client.get("/auth/me")
    assert me.headers["cache-control"] == "no-store"
    assert set(me.json()["user"]) == {"id", "username", "display_name"}
    assert me.json()["user"]["username"] == "alice"
    assert client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
    assert store.get_session(token) is None
    client.cookies.set("oaw_session", token)
    assert client.get("/auth/me").json()["authenticated"] is False


def test_login_rotates_existing_session_and_invalid_credentials_are_uniform(hosted_auth):
    store, _, client = hosted_auth
    assert login(client).status_code == 200
    first = client.cookies["oaw_session"]
    assert login(client).status_code == 200
    assert client.cookies["oaw_session"] != first
    assert store.get_session(first) is None
    wrong = login(client, password="wrong password")
    unknown = login(client, username="unknown")
    store.set_enabled(store.list_users()[0]["id"], False)
    disabled = login(client)
    assert wrong.status_code == unknown.status_code == disabled.status_code == 401
    assert wrong.json() == unknown.json() == disabled.json()


def test_password_session_survives_restart_for_a_day_without_extending_expiry(hosted_auth, monkeypatch):
    store, _, client = hosted_auth
    started = time.time()
    response = login(client)
    assert "Max-Age=86400" in response.headers["set-cookie"]
    token = client.cookies["oaw_session"]
    restarted = HostedAuth(AccountStore(store.path), ORIGIN)
    with TestClient(Starlette(routes=restarted.routes), base_url=ORIGIN) as browser:
        browser.cookies.set("oaw_session", token)
        monkeypatch.setattr(time, "time", lambda: started + 7200)
        assert browser.get("/auth/me").json()["authenticated"] is True
        monkeypatch.setattr(time, "time", lambda: started + 86401)
        assert browser.get("/auth/me").json()["authenticated"] is False


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"},
                                    [("Origin", ORIGIN), ("Origin", "https://evil.example")]])
def test_login_and_logout_require_exact_single_origin(hosted_auth, headers):
    _, _, client = hosted_auth
    assert client.post("/auth/login", json={"username": "alice", "password": PASSWORD}, headers=headers).status_code == 403
    assert client.post("/auth/logout", headers=headers).status_code == 403


def test_only_small_json_requests_and_no_registration(hosted_auth):
    _, _, client = hosted_auth
    assert client.post("/auth/login", data={"username": "alice", "password": PASSWORD},
                       headers={"Origin": ORIGIN}).status_code == 415
    assert client.post("/auth/login", content="x" * 8193,
                       headers={"Origin": ORIGIN, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/auth/login", json=["alice", PASSWORD], headers={"Origin": ORIGIN}).status_code == 400
    assert client.post("/auth/login", content="[" * 1500 + "]" * 1500,
                       headers={"Origin": ORIGIN, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/auth/register", json={"username": "bob", "password": PASSWORD}).status_code == 404
    # JSON permits escaped lone surrogates; malformed UTF-8 strings must not
    # cause a server exception or skip the expensive invalid-password path.
    for value in ({"username": "alice", "password": "\ud800"}, {"username": "\ud800", "password": PASSWORD}):
        assert client.post("/auth/login", content=json.dumps(value),
                           headers={"Origin": ORIGIN, "Content-Type": "application/json"}).status_code == 401


def test_forged_cookie_and_throttling(hosted_auth):
    store, _, client = hosted_auth
    client.cookies.set("oaw_session", "forged-session")
    assert client.get("/auth/me").json()["authenticated"] is False
    for _ in range(10):
        store._consume_login_budget("alice", "peer")
    response = login(client)
    assert response.status_code == 429 and response.headers["retry-after"] == "900"


def test_get_login_can_be_served_by_gateway_shell(tmp_path):
    auth = HostedAuth(AccountStore(tmp_path / "accounts.sqlite3"), ORIGIN)
    async def shell(_):
        return PlainTextResponse("app shell")
    with TestClient(Starlette(routes=[*auth.routes, Route("/{path:path}", shell)]), base_url=ORIGIN) as client:
        assert client.get("/auth/login").text == "app shell"


@pytest.mark.parametrize("origin", ["http://oaw.example", "https://oaw.example/path", "https://user@oaw.example",
                                   "https://oaw.example?other=1", "//oaw.example"])
def test_invalid_public_origins_are_rejected(tmp_path, origin):
    with pytest.raises(ValueError, match="origin"):
        HostedAuth(AccountStore(tmp_path / "accounts.sqlite3"), origin)


def test_http_is_only_allowed_explicitly_on_loopback(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    with pytest.raises(ValueError):
        HostedAuth(store, "http://127.0.0.1:8000")
    assert HostedAuth(store, "http://127.0.0.1:8000", allow_local_http=True).secure is False
    with pytest.raises(ValueError):
        HostedAuth(store, "http://0.0.0.0:8000", allow_local_http=True)


@pytest.fixture
def hosted_oidc(tmp_path):
    pytest.importorskip("authlib")
    from joserfc import jwt
    from joserfc.jwk import RSAKey
    from backend.hosting.oidc import OidcSettings
    key = RSAKey.generate_key(2048, parameters={"kid": "key"})
    state = {"nonce": "", "claims": {}, "key": key, "token_calls": 0}

    def server(request):
        if request.url.path == "/discovery":
            return httpx.Response(200, json={"issuer": "https://id.example", "authorization_endpoint": "https://id.example/authorize",
                "token_endpoint": "https://id.example/token", "jwks_uri": "https://id.example/keys",
                "id_token_signing_alg_values_supported": ["RS256"]})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [key.as_dict(private=False)]})
        if request.url.path == "/token":
            state["token_calls"] += 1
            assert b"code_verifier=" in request.content
            claims = {"iss": "https://id.example", "sub": "alice-external", "aud": "oaw-client",
                      "iat": int(time.time()), "exp": int(time.time()) + 600, "nonce": state["nonce"], **state["claims"]}
            token = jwt.encode({"alg": "RS256", "kid": "key"}, claims, state["key"])
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        raise AssertionError(str(request.url))

    store = AccountStore(tmp_path / "accounts.sqlite3")
    alice = store.create_user("alice", password=PASSWORD)
    store.link_identity(alice["id"], "https://id.example", "alice-external")
    settings = OidcSettings("https://id.example", "https://id.example/discovery", "oaw-client", "client-secret",
                            "a-stable-unique-test-session-secret-" * 2, ORIGIN)
    auth = HostedAuth(store, ORIGIN, oidc_settings=settings, transport=httpx.MockTransport(server))
    with TestClient(auth.wrap(Starlette(routes=auth.routes)), base_url=ORIGIN) as client:
        yield store, client, state


def begin_oidc(client, state):
    response = client.get("/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == [ORIGIN + "/auth/oidc/callback"]
    state["nonce"] = params["nonce"][0]
    return params["state"][0]


def test_oidc_maps_prelinked_user_into_same_server_sessions(hosted_oidc):
    store, client, state = hosted_oidc
    assert login(client).status_code == 200
    previous = client.cookies["oaw_session"]
    login_state = begin_oidc(client, state)
    response = client.get("/auth/oidc/callback", params={"state": login_state, "code": "valid"}, follow_redirects=False)
    assert response.status_code == 303
    assert store.get_session(previous) is None
    me = client.get("/auth/me").json()
    assert me["user"]["id"] == store.list_users()[0]["id"]
    assert me["login_methods"] == ["password", "oidc"]
    assert "oaw_oidc" not in client.cookies
    assert "id_token" not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize("claims", [{"iss": "https://wrong.example"}, {"aud": "wrong-client"}, {"exp": 1},
                                    {"nonce": "wrong-nonce"}, {"sub": "unknown", "email": "alice"}])
def test_oidc_rejects_invalid_claims_or_unlinked_identity(hosted_oidc, claims):
    _, client, state = hosted_oidc
    login_state = begin_oidc(client, state)
    state["claims"] = claims
    assert client.get("/auth/oidc/callback", params={"state": login_state, "code": "invalid"}).status_code == 401
    assert client.get("/auth/me").json()["authenticated"] is False


def test_oidc_rejects_forged_state_and_token_signature(hosted_oidc):
    from joserfc.jwk import RSAKey
    _, client, state = hosted_oidc
    begin_oidc(client, state)
    assert client.get("/auth/oidc/callback?state=forged&code=valid").status_code == 401
    assert state["token_calls"] == 0
    login_state = begin_oidc(client, state)
    state["key"] = RSAKey.generate_key(2048, parameters={"kid": "key"})
    assert client.get("/auth/oidc/callback", params={"state": login_state, "code": "invalid"}).status_code == 401
    assert client.get("/auth/me").json()["authenticated"] is False
