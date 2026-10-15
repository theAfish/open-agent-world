import base64
import json
import time

import pytest

pytest.importorskip("authlib")
pytest.importorskip("itsdangerous")
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from backend.cloud.aliyun.identity_probe import create_app


@pytest.fixture
def probe_settings(monkeypatch):
    values = {"OAW_OIDC_ISSUER": "https://id.example", "OAW_OIDC_DISCOVERY_URL": "https://id.example/discovery",
              "OAW_OIDC_CLIENT_ID": "client", "OAW_OIDC_CLIENT_SECRET": "client-secret",
              "OAW_SESSION_SECRET": "local-probe-stable-secret-" * 3,
              "OAW_PUBLIC_ORIGIN": "http://localhost:8080", "OAW_ALLOW_LOCAL_HTTP": "1"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPEN_AGENT_WORLD_DATA_ROOT", raising=False)
    monkeypatch.delenv("OAW_ALIYUN_SANDBOX_API_KEY", raising=False)
    return values


def test_probe_uses_only_verified_session_identity_and_never_needs_a_profile(probe_settings):
    with TestClient(create_app(), base_url="http://localhost:8080") as client:
        response = client.get("/", follow_redirects=False)
        assert response.headers["location"] == "/auth/login"
        payload = {"identity": {"issuer": "https://id.example", "subject": "ram-subject",
                                "expires_at": time.time() + 300, "display_name": "Owner"}}
        unsigned = base64.b64encode(json.dumps(payload).encode())
        client.cookies.set("oaw_identity", unsigned.decode())
        assert client.get("/", follow_redirects=False).headers["location"] == "/auth/login"
        signed = TimestampSigner(probe_settings["OAW_SESSION_SECRET"]).sign(unsigned).decode()
        client.cookies.set("oaw_identity", signed)
        response = client.get("/")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["subject"] == "ram-subject"
        assert "client-secret" not in response.text
        assert client.get("/api/world").status_code == 404


def test_probe_refuses_public_origin(probe_settings, monkeypatch):
    monkeypatch.setenv("OAW_PUBLIC_ORIGIN", "https://oaw.example")
    with pytest.raises(ValueError, match="localhost"):
        create_app()
