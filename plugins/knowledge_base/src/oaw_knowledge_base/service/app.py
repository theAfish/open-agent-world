"""The HTTP front door: every operation in the table, one route, one bearer token.

The router is generic on purpose. ``POST /v1/collections/{collection}/{operation}``
looks the operation up in :mod:`oaw_knowledge_base.operations` and calls the same
handler the OAW card calls, with the action's arguments as the request body — so an
operation cannot drift between the card, MCP and HTTP, and adding one to the table
publishes it everywhere at once.

Two boundaries are enforced here rather than in the handlers:

* **The token.** One bearer token from ``KB_SERVICE_TOKEN``. Binding anything other
  than loopback without it is refused in :mod:`~oaw_knowledge_base.service.cli`.
* **Approval.** Publishing a draft as a fact is a human act. ``review`` with
  ``operation=approve`` is rejected unless a separate ``KB_ADMIN_TOKEN`` is set *and*
  presented, so an out-of-the-box service has no network path to publishing at all.

The service never holds model credentials and never calls a provider: callers take
``projection_prompt`` to their own model and bring the JSON back to
``save_projection``, exactly as an OAW Agent does.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import __version__
from ..errors import KnowledgeError, operator_message
from ..operations import BY_NAME, tool_manifest
from .store import DEFAULT_COLLECTION, Store

# Operations a plain service token may reach. Everything else in the table is either
# an agent operation (also allowed) or gated below.
ADMIN_ONLY = {("review", "approve")}


def _presented(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def create_app(store: Store, *, token: str | None = None, admin_token: str | None = None):
    app = FastAPI(title="Knowledge base", version=__version__,
                  description="Documents to markdown, markdown to JSON, JSON to a reviewed graph.")

    accepted = {value for value in (token, admin_token) if value}

    def authorize(request: Request) -> bool:
        """Returns whether this caller is an admin. Raises 401 when the token is wrong."""
        presented = _presented(request)
        if accepted and presented not in accepted:
            raise HTTPException(status_code=401, detail="A valid bearer token is required",
                                headers={"WWW-Authenticate": "Bearer"})
        return bool(admin_token) and presented == admin_token

    @app.exception_handler(KnowledgeError)
    def _knowledge_error(request: Request, error: KnowledgeError):
        # Covers the paths outside an operation, such as opening a store with no MKB.
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.get("/v1/health")
    def health():
        # Unauthenticated on purpose: a supervisor has to be able to ask if we are up.
        return {"status": "ok", "version": __version__, "store": str(store.path)}

    @app.get("/v1/tools")
    def tools(admin: bool = Depends(authorize)):
        """The agent-facing manifest, in the shape a harness wants for tool registration."""
        return {"tools": tool_manifest()}

    @app.get("/v1/collections")
    def collections(admin: bool = Depends(authorize)):
        return {"collections": store.collections()}

    @app.post("/v1/collections/{collection}/{operation}")
    def run(collection: str, operation: str, request: Request, response: Response,
            arguments: dict | None = None, confirm: bool = False,
            admin: bool = Depends(authorize)):
        entry = BY_NAME.get(operation)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"No such operation: {operation}")
        arguments = arguments or {}
        if (operation, str(arguments.get("operation"))) in ADMIN_ONLY and not admin:
            raise HTTPException(
                status_code=403,
                detail="Approving a draft publishes a permanent fact. It needs the admin "
                       "token (set KB_ADMIN_TOKEN on the service), or approve it in OAW "
                       "or with `kb approve` on the host.")
        try:
            result = store.run(operation, arguments, collection=collection,
                               actor=request.headers.get("x-kb-actor"),
                               confirmed=confirm and admin)
        except Exception as error:  # the status code is the only thing decided here
            raise _translate(error) from error
        if result.get("status") == "confirmation_required":
            response.status_code = 409
        return result

    return app


def _translate(error: Exception) -> HTTPException:
    """MKB's hierarchy and our own, mapped to a status without leaking internals."""
    message = operator_message(error)
    if message is None:
        raise error
    try:
        from mkb import exceptions
    except ImportError:  # pragma: no cover - depends on the deployment env
        return HTTPException(status_code=422, detail=message)
    for kind, status in ((exceptions.NotFoundError, 404), (exceptions.ConflictError, 409),
                         (exceptions.BackendUnavailableError, 503),
                         (exceptions.ProviderError, 502)):
        if isinstance(error, kind):
            return HTTPException(status_code=status, detail=message)
    return HTTPException(status_code=422, detail=message)
