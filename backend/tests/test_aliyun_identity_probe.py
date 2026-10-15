import base64
import json
import time

import pytest

pytest.importorskip("authlib")
pytest.importorskip("itsdangerous")
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from backend.cloud.aliyun.identity_probe import create_app, ram_settings


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


def test_probe_uses_only_verified_session_identity_and_never_needs_a_profile(probe_settings, tmp_path):
    receipt = tmp_path / "private" / "verified-identity.json"
    with TestClient(create_app(identity_output=receipt), base_url="http://localhost:8080") as client:
        response = client.get("/", follow_redirects=False)
        assert response.headers["location"] == "/auth/login"
        payload = {"identity": {"issuer": "https://id.example", "subject": "ram-subject",
                                "expires_at": time.time() + 300, "display_name": "Owner"}}
        unsigned = base64.b64encode(json.dumps(payload).encode())
        client.cookies.set("oaw_identity", unsigned.decode())
        assert client.get("/", follow_redirects=False).headers["location"] == "/auth/login"
        assert not receipt.exists()
        signed = TimestampSigner(probe_settings["OAW_SESSION_SECRET"]).sign(unsigned).decode()
        client.cookies.set("oaw_identity", signed)
        response = client.get("/")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["subject"] == "ram-subject"
        assert "client-secret" not in response.text
        assert json.loads(receipt.read_text()) == {"issuer": "https://id.example", "subject": "ram-subject"}
        assert client.get("/api/world").status_code == 404


def test_probe_refuses_public_origin(probe_settings, monkeypatch):
    monkeypatch.setenv("OAW_PUBLIC_ORIGIN", "https://oaw.example")
    with pytest.raises(ValueError, match="localhost"):
        create_app()


def test_ram_env_mapping_keeps_secret_literal_and_uses_ephemeral_session_key():
    values = {"ALIYUN_OAUTH_CLIENT_ID": "client", "ALIYUN_OAUTH_CLIENT_SECRET": "literal-${SECRET}",
              "ALIYUN_OAUTH_REDIRECT_URI": "http://localhost:8080/auth/callback"}
    first, second = ram_settings(values), ram_settings(values)
    assert first.client_secret == values["ALIYUN_OAUTH_CLIENT_SECRET"]
    assert first.public_origin == "http://localhost:8080"
    assert first.issuer == "https://oauth.aliyun.com"
    assert first.session_secret != second.session_secret
    assert first.scopes == ("openid", "profile")
    assert "literal-" not in repr(first)
    assert "OAW_SESSION_SECRET" not in values


def test_ram_cli_loads_private_file_literally_and_disables_callback_access_logs(monkeypatch, tmp_path):
    import uvicorn
    from backend.cloud.aliyun import identity_probe

    private_file = tmp_path / "oauth.env"
    private_file.write_text(
        "ALIYUN_OAUTH_CLIENT_ID=client\n"
        "ALIYUN_OAUTH_CLIENT_SECRET=literal-${SECRET}\n"
        "ALIYUN_OAUTH_REDIRECT_URI=http://localhost:8081/auth/callback\n",
        encoding="utf-8-sig",
    )
    original = private_file.read_bytes()
    receipt = tmp_path / "verified.json"
    monkeypatch.setenv("SECRET", "must-not-substitute")
    monkeypatch.setattr("sys.argv", ["identity_probe", "--env-file", str(private_file),
                                    "--identity-output", str(receipt)])
    captured = {}
    app = object()

    def capture_app(**kwargs):
        captured.update(kwargs)
        return app

    def capture_server(application, **kwargs):
        captured["application"] = application
        captured["server"] = kwargs

    monkeypatch.setattr(identity_probe, "create_app", capture_app)
    monkeypatch.setattr(uvicorn, "run", capture_server)
    identity_probe.main()

    assert captured["settings"].client_secret == "literal-${SECRET}"
    assert captured["settings"].client_id == "client"
    assert captured["identity_output"] == receipt
    assert captured["application"] is app
    assert captured["server"] == {"host": "127.0.0.1", "port": 8081,
                                  "access_log": False, "proxy_headers": False}
    assert private_file.read_bytes() == original
    assert not receipt.exists()


@pytest.mark.parametrize("redirect", [
    "http://example.com:8080/auth/callback", "http://localhost:8080/wrong",
    "http://localhost:8080/auth/callback?target=remote", "http://user:password@localhost:8080/auth/callback",
    "http://localhost/auth/callback",
])
def test_ram_probe_rejects_unusable_or_remote_callbacks(redirect):
    with pytest.raises(ValueError):
        ram_settings({"ALIYUN_OAUTH_CLIENT_ID": "client", "ALIYUN_OAUTH_CLIENT_SECRET": "secret",
                      "ALIYUN_OAUTH_REDIRECT_URI": redirect})
