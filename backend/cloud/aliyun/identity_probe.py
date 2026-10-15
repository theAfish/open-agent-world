"""Local OIDC sign-in probe; never opens an OAW profile or creates cloud resources.

python -m backend.cloud.aliyun.identity_probe --env-file <private-ram-oauth.env>
  --identity-output <private-verified-identity.json>
"""
import argparse
import json
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from backend.hosting.oidc import OidcSessionIdentity, OidcSettings
from .identity import oidc_settings


def create_app(*, settings=None, identity_output=None):
    settings = settings or oidc_settings()
    if urlsplit(settings.public_origin).hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("The identity probe requires a localhost public origin")
    provider = OidcSessionIdentity(settings)

    async def whoami(request):
        identity = await provider.authenticate(request.scope)
        if identity is None:
            return RedirectResponse("/auth/login", headers={"Cache-Control": "no-store"})
        if identity_output is not None:
            destination = Path(identity_output)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Only verified owner identifiers, never cookies or provider tokens.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             prefix=".oaw-identity-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"issuer": identity.issuer, "subject": identity.subject}, stream)
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return JSONResponse({"issuer": identity.issuer, "subject": identity.subject,
                             "display_name": identity.display_name,
                             "note": "Identity verified; no OAW profile or cloud resources were provisioned."},
                            headers={"Cache-Control": "no-store"})

    return provider.wrap(Starlette(routes=[Route("/", whoami)]))


def ram_settings(values):
    """Map the operator's RAM export without modifying their private file."""
    def required(name):
        value = values.get(name)
        if not value or not value.strip():
            raise ValueError(f"Missing RAM OAuth configuration: {name}")
        return value.strip()

    redirect = urlsplit(required("ALIYUN_OAUTH_REDIRECT_URI"))
    if (redirect.scheme != "http" or redirect.hostname not in {"localhost", "127.0.0.1"}
            or redirect.username or redirect.password or redirect.path != "/auth/callback"
            or redirect.query or redirect.fragment or not redirect.port):
        raise ValueError("RAM login probe requires http://localhost:<port>/auth/callback or 127.0.0.1")
    return OidcSettings(
        issuer="https://oauth.aliyun.com",
        discovery_url="https://oauth.aliyun.com/.well-known/openid-configuration",
        client_id=required("ALIYUN_OAUTH_CLIENT_ID"), client_secret=required("ALIYUN_OAUTH_CLIENT_SECRET"),
        session_secret=secrets.token_urlsafe(48), public_origin=f"http://{redirect.netloc}",
        allow_local_http=True, scopes=("openid", "profile"),
    )


def main():
    from dotenv import dotenv_values
    import uvicorn

    parser = argparse.ArgumentParser(description="Verify RAM login using a private OAuth env file")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--identity-output", type=Path,
                        help="Optional private file for verified issuer and subject; contains no tokens")
    arguments = parser.parse_args()
    if not arguments.env_file.is_file():
        parser.error("The OAuth environment file is not readable")
    settings = ram_settings(dotenv_values(arguments.env_file, encoding="utf-8-sig", interpolate=False))
    app = create_app(settings=settings, identity_output=arguments.identity_output)
    # OAuth callback URLs contain a short-lived code. Do not write access logs.
    uvicorn.run(app, host="127.0.0.1", port=urlsplit(settings.public_origin).port,
                access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
