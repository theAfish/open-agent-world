"""Local OIDC sign-in probe; never opens an OAW profile or creates cloud resources.

uvicorn backend.cloud.aliyun.identity_probe:create_app --factory --host 127.0.0.1
  --port 8080 --env-file <operator-private-identity.env>
"""
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from backend.hosting.oidc import OidcSessionIdentity
from .identity import oidc_settings


def create_app():
    settings = oidc_settings()
    if urlsplit(settings.public_origin).hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("The identity probe requires a localhost public origin")
    provider = OidcSessionIdentity(settings)

    async def whoami(request):
        identity = await provider.authenticate(request.scope)
        if identity is None:
            return RedirectResponse("/auth/login", headers={"Cache-Control": "no-store"})
        return JSONResponse({"issuer": identity.issuer, "subject": identity.subject,
                             "display_name": identity.display_name,
                             "note": "Identity verified; no OAW profile or cloud resources were provisioned."},
                            headers={"Cache-Control": "no-store"})

    return provider.wrap(Starlette(routes=[Route("/", whoami)]))
