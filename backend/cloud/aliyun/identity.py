"""Identity configuration shared by the cloud app and local login probe."""
import os

from backend.hosting.oidc import OidcSettings


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required cloud configuration: {name}")
    return value


def oidc_settings():
    return OidcSettings(
        issuer=require_env("OAW_OIDC_ISSUER"), discovery_url=require_env("OAW_OIDC_DISCOVERY_URL"),
        client_id=require_env("OAW_OIDC_CLIENT_ID"), client_secret=require_env("OAW_OIDC_CLIENT_SECRET"),
        session_secret=require_env("OAW_SESSION_SECRET"), public_origin=require_env("OAW_PUBLIC_ORIGIN"),
        allow_local_http=os.environ.get("OAW_ALLOW_LOCAL_HTTP") == "1",
        scopes=tuple(os.environ.get("OAW_OIDC_SCOPES", "openid profile").split()),
        token_endpoint_auth_method=os.environ.get("OAW_OIDC_TOKEN_AUTH_METHOD", "client_secret_basic"),
    )
