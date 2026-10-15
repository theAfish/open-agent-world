import base64
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("authlib")
pytest.importorskip("itsdangerous")
from joserfc import jwt
from joserfc.jwk import RSAKey

from backend.config import Settings
from backend.hosting.identity import ProfileAccess, VerifiedIdentity
from backend.hosting.oidc import OidcSessionIdentity, OidcSettings
from backend.main import create_app
from backend.request_context import TenantScope


@pytest.fixture
def oidc(tmp_path, request):
    key = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
    state = {"nonce": "", "claims": {}, "key": key, "token_calls": 0}

    def identity_server(request):
        if request.url.path == "/discovery":
            return httpx.Response(200, json={"issuer": "https://id.example",
                "authorization_endpoint": "https://id.example/authorize",
                "token_endpoint": "https://id.example/token", "jwks_uri": "https://id.example/keys",
                "id_token_signing_alg_values_supported": ["RS256"]})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [key.as_dict(private=False)]})
        if request.url.path == "/token":
            state["token_calls"] += 1
            state["token_request"] = request
            assert b"code_verifier=" in request.content
            claims = {"iss": "https://id.example", "sub": "alice", "aud": "oaw-client",
                      "iat": int(time.time()), "exp": int(time.time()) + 600,
                      "nonce": state["nonce"], **state["claims"]}
            token = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, state["key"])
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        raise AssertionError(f"Unexpected identity endpoint: {request.url}")

    settings = OidcSettings("https://id.example", "https://id.example/discovery", "oaw-client",
                            "client-secret", "a-stable-session-secret-" * 3, "https://oaw.example")
    settings = replace(settings, **getattr(request, "param", {}))
    provider = OidcSessionIdentity(settings, transport=httpx.MockTransport(identity_server))
    scope = TenantScope("org", "alice-workspace")
    access = ProfileAccess(provider, VerifiedIdentity(settings.issuer, "alice"), scope,
                           settings.public_origin, "/auth/login")
    app = create_app(replace(Settings.for_data_root(tmp_path), tenant_scope=scope), profile_access=access)
    with TestClient(provider.wrap(app), base_url=settings.public_origin) as client:
        yield client, state


def begin(client, state):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    state["authorization_params"] = params
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["https://oaw.example/auth/callback"]
    state["nonce"] = params["nonce"][0]
    return params["state"][0]


def test_real_signed_oidc_login_session_logout(oidc):
    client, state = oidc
    assert client.get("/api/world").status_code == 401
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/auth/login"
    nonce = begin(client, state)
    response = client.get("/auth/callback", params={"state": nonce, "code": "valid"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert "httponly" in response.headers["set-cookie"].lower() and "secure" in response.headers["set-cookie"].lower()
    assert client.get("/api/world").status_code == 200
    payload = json.loads(base64.b64decode(client.cookies["oaw_identity"].split(".")[0]))
    assert set(payload) == {"identity"} and "token" not in json.dumps(payload)
    assert client.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/logout", headers={"Origin": "https://oaw.example"}).status_code == 200
    assert client.get("/api/world").status_code == 401


@pytest.mark.parametrize("claims", [
    {"iss": "https://evil.example"}, {"aud": "different-client"},
    {"exp": 1}, {"nonce": "incorrect"}, {"sub": "bob"},
])
def test_wrong_or_expired_identity_cannot_access_profile(oidc, claims):
    client, state = oidc
    nonce = begin(client, state)
    state["claims"] = claims
    response = client.get("/auth/callback", params={"state": nonce, "code": "invalid"}, follow_redirects=False)
    assert response.status_code in {401, 303}
    assert client.get("/api/world").status_code in {401, 403}


def test_callback_requires_matching_state_and_cookie_signature(oidc):
    client, state = oidc
    begin(client, state)
    assert client.get("/auth/callback?state=forged&code=valid").status_code == 401
    assert state["token_calls"] == 0
    client.cookies.set("oaw_identity", "forged-cookie")
    assert client.get("/api/world").status_code == 401


def test_forged_id_token_signature_is_rejected(oidc):
    client, state = oidc
    nonce = begin(client, state)
    state["key"] = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
    response = client.get("/auth/callback", params={"state": nonce, "code": "forged"})
    assert response.status_code == 401
    assert client.get("/api/world").status_code == 401


@pytest.mark.parametrize("oidc", [
    {},
    {"scopes": ("openid", "profile", "USER_API"), "token_endpoint_auth_method": "client_secret_post"},
], indirect=True)
def test_provider_scopes_and_client_authentication_keep_verified_login(oidc):
    client, state = oidc
    login_state = begin(client, state)
    response = client.get("/auth/callback", params={"state": login_state, "code": "valid"}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/world").status_code == 200
    token_request = state["token_request"]
    form = parse_qs(token_request.content.decode())
    scopes = state["authorization_params"]["scope"][0].split()
    if "USER_API" in scopes:
        assert scopes == ["openid", "profile", "USER_API"]
        assert "authorization" not in token_request.headers
        assert form["client_id"] == ["oaw-client"]
        assert form["client_secret"] == ["client-secret"]
    else:
        assert scopes == ["openid", "profile"]
        assert token_request.headers["authorization"].startswith("Basic ")
        assert "client_secret" not in form
    # Both modes must retain PKCE and keep all provider credentials out of cookies.
    assert form["code_verifier"]
    cookie = json.loads(base64.b64decode(client.cookies["oaw_identity"].split(".")[0]))
    assert set(cookie) == {"identity"}
    assert "client-secret" not in json.dumps(cookie)


@pytest.mark.parametrize("overrides", [
    {"scopes": ()}, {"scopes": ("profile",)}, {"scopes": ("openid", "openid")},
    {"scopes": ("openid", "profile email")}, {"scopes": ("openid", "profile\n")},
    {"token_endpoint_auth_method": "none"},
])
def test_invalid_provider_options_are_rejected(overrides):
    with pytest.raises(ValueError, match="OIDC"):
        OidcSettings("https://id.example", "https://id.example/discovery", "oaw-client",
                     "client-secret", "a-stable-session-secret-" * 3, "https://oaw.example", **overrides)
