"""Host-local management of trusted Pack files."""
from __future__ import annotations

import mimetypes
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from backend.api.dependencies import get_services
from backend.api.card_library import plugin_usage
from backend.card_library import LibraryEdit
from backend.errors import ConflictError, ResourceValidationError
from backend.events.models import EventType, RuntimeEvent
from backend.packs.archive import MAX_ARCHIVE_BYTES
from backend.sandbox.python_runtime import finish_thread

router = APIRouter(prefix="/packs", tags=["packs"])


async def mutation_request(request: Request):
    # Browser forms cannot submit trusted-code installation. Cross-origin
    # script requests need the existing host CORS/control-plane authorization.
    if request.headers.get("x-oaw-pack-install") != "1":
        raise HTTPException(403, "Pack management requires an explicit host request")


async def archive_body(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";")[0] != "application/vnd.oaw.pack":
        raise HTTPException(415, "Expected a local .oawpack file")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_ARCHIVE_BYTES:
            raise HTTPException(413, "Pack exceeds the 128 MiB upload limit")
        body.extend(chunk)
    return bytes(body)


def status(services):
    result = services.pack_installations.status()
    environments = {r['id']: r for r in services.plugin_bootstrap.records()}
    for row in result['versions']:
        row['environment'] = environments.get(row['id']) if row['loaded'] else None
    return result


async def checked(function, *args):
    try:
        return await finish_thread(function, *args)
    except (ValueError, KeyError, BadZipFile, OSError) as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.get("")
async def installations(services=Depends(get_services)):
    return status(services)


@router.post("/inspect", dependencies=[Depends(mutation_request)])
async def inspect(request: Request, services=Depends(get_services)):
    pack = await checked(services.pack_installations.inspect, await archive_body(request))
    return {"manifest": pack.manifest.model_dump(mode="json"), "sha256": pack.digest,
            "restart_required": True, "trusted_code": True}


@router.post("/install", status_code=201, dependencies=[Depends(mutation_request)])
async def install(request: Request, services=Depends(get_services)):
    data = await archive_body(request)
    async with services._node_mutation():
        await checked(services.pack_installations.install, data)
    return status(services)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str


@router.post("/{pack_id}/activate", dependencies=[Depends(mutation_request)])
async def activate(pack_id: str, request: VersionRequest, services=Depends(get_services)):
    async with services._node_mutation():
        await checked(services.pack_installations.activate, pack_id, request.version)
    return status(services)


@router.delete("/{pack_id}", dependencies=[Depends(mutation_request)])
async def uninstall(pack_id: str, services=Depends(get_services)):
    async with services._node_mutation():
        usages = plugin_usage(services, pack_id)
        if usages:
            raise ConflictError("Pack is in use by " + ", ".join(usages[:6]))
        for descriptor in services.plugins.plugins():
            if pack_id in descriptor.requires_plugins:
                raise ConflictError(f"Pack is required by {descriptor.name or descriptor.id}")
        await checked(services.pack_installations.uninstall, pack_id)
        # Block new world admissions while keeping loaded objects until restart.
        if services.plugins.has_plugin(pack_id):
            services.card_library.edit(LibraryEdit(
                action="set_plugin_enabled", id=pack_id, enabled=False,
                expected_revision=services.card_library.read().revision,
            ))
    await services.events.publish_event(RuntimeEvent(type=EventType.CARD_LIBRARY_UPDATED,
        payload={"revision": services.card_library.snapshot()["revision"]}))
    return status(services)


@router.delete("/{pack_id}/versions/{version}", dependencies=[Depends(mutation_request)])
async def remove_version(pack_id: str, version: str, services=Depends(get_services)):
    async with services._node_mutation():
        await checked(services.pack_installations.remove_version, pack_id, version)
    return status(services)


@router.post("/environment/retry", dependencies=[Depends(mutation_request)])
async def retry(services=Depends(get_services)):
    services.plugin_bootstrap.enqueue()
    return status(services)


@router.get("/{pack_id}/versions/{version}/{asset_path:path}")
async def frontend_asset(pack_id: str, version: str, asset_path: str, services=Depends(get_services)):
    try:
        path = services.pack_installations.frontend_asset(pack_id, version, asset_path)
    except (ValueError, OSError):
        raise HTTPException(404, "Pack asset unavailable") from None
    media_type = "text/javascript" if path.suffix == ".js" else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={
        "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    })
