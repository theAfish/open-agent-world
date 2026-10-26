"""Host-local management of trusted Pack files."""
from __future__ import annotations

import mimetypes
import subprocess
from functools import partial
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict

from backend.api.dependencies import get_services
from backend.api.card_library import plugin_usage
from backend.api.pack_factory import router as factory_router
from backend.api.pack_projects import router as projects_router
from backend.card_library import LibraryEdit
from backend.errors import ConflictError, ResourceValidationError
from backend.events.models import EventType, RuntimeEvent
from backend.packs.archive import MAX_ARCHIVE_BYTES
from backend.packs.content import CreatorRequest, export_archive, prepare_export
from backend.sandbox.python_runtime import finish_thread

router = APIRouter(prefix="/packs", tags=["packs"])


async def mutation_request(request: Request):
    # Browser forms cannot submit trusted-code installation. Cross-origin
    # script requests need the existing host CORS/control-plane authorization.
    if request.headers.get("x-oaw-pack-install") != "1":
        raise HTTPException(403, "Pack management requires an explicit host request")


router.include_router(factory_router, dependencies=[Depends(mutation_request)])
router.include_router(projects_router, dependencies=[Depends(mutation_request)])


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
        row['environment'] = environments.get(row.get('owner', row['id'])) if row['loaded'] else None
    from backend.packs.planning import inventory
    result['inventory'] = inventory(services.pack_installations, environments)
    result['operations'] = services.pack_installations.operations()
    return result


async def checked(function, *args):
    try:
        return await finish_thread(function, *args)
    except (ValueError, KeyError, BadZipFile, OSError) as exc:
        raise ResourceValidationError(str(exc)) from exc
    except (RuntimeError, subprocess.SubprocessError) as exc:
        # Process commands/output can contain authenticated package-index URLs.
        raise ResourceValidationError(f"Pack environment preparation failed ({type(exc).__name__}); retry after checking its configuration") from None


async def prepare_and_install(services, data, **expected):
    try:
        await checked(partial(services.pack_installations.prepare, data, **expected))
        async with services._node_mutation():
            await checked(partial(services.pack_installations.install, data, **expected, prepare_environments=False))
            await services.activate_passive_packs()
    except BaseException:
        await finish_thread(services.pack_installations.interrupt_preparation, data)
        raise


@router.get("")
async def installations(services=Depends(get_services)):
    return status(services)


@router.post("/inspect", dependencies=[Depends(mutation_request)])
async def inspect(request: Request, services=Depends(get_services)):
    data = await archive_body(request)
    from backend.packs.archive import inspect_archive
    pack = await checked(inspect_archive, data)
    plan = await checked(services.pack_installations.plan, data)
    return {"manifest": pack.manifest.model_dump(mode="json"), "sha256": pack.digest,
            "plan": plan, "restart_required": plan['restart_required'], "trusted_code": pack.manifest.kind == "plugin"}


@router.post("/creator/inspect", dependencies=[Depends(mutation_request)])
async def inspect_creation(request: CreatorRequest, services=Depends(get_services)):
    record = services.legions.get(request.legion_id)
    result, _ = await checked(prepare_export, record, request, services.plugins)
    return result


@router.post("/creator/export", dependencies=[Depends(mutation_request)])
async def export_creation(request: CreatorRequest, services=Depends(get_services)):
    record = services.legions.get(request.legion_id)
    result, files = await checked(prepare_export, record, request, services.plugins)
    if not result["can_export"]:
        raise ResourceValidationError("Resolve the publication checks before exporting")
    data = await checked(export_archive, files)
    return Response(data, media_type="application/vnd.oaw.pack", headers={
        "Content-Disposition": f'attachment; filename="{result["manifest"]["id"]}-{result["manifest"]["version"]}.oawpack"',
        "Cache-Control": "no-store",
    })


@router.post("/install", status_code=201, dependencies=[Depends(mutation_request)])
async def install(request: Request, services=Depends(get_services)):
    data = await archive_body(request)
    await prepare_and_install(services, data)
    return status(services)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str


@router.post("/{pack_id}/activate", dependencies=[Depends(mutation_request)])
async def activate(pack_id: str, request: VersionRequest, services=Depends(get_services)):
    previously_selected = pack_id in services.pack_installations.selected()
    try:
        await checked(services.pack_installations.prepare_activation, pack_id, request.version)
        async with services._node_mutation():
            await checked(partial(services.pack_installations.activate, pack_id, request.version, prepare=False))
            if not previously_selected:
                services.card_library.restore_plugin(services.pack_installations.selected()[pack_id].owner_id)
            await services.activate_passive_packs()
    finally:
        await finish_thread(services.pack_installations.interrupt_activation, pack_id, request.version)
    return status(services)


@router.delete("/{pack_id}", dependencies=[Depends(mutation_request)])
async def uninstall(pack_id: str, services=Depends(get_services)):
    async with services._node_mutation():
        plan = await checked(services.pack_installations.uninstall_plan, pack_id)
        owner = plan['owner']
        usages = plugin_usage(services, owner)
        if usages:
            raise ConflictError("Pack is in use by " + ", ".join(usages[:6]))
        if plan['blockers']:
            raise ConflictError('; '.join(plan['blockers']))
        await checked(services.pack_installations.uninstall, pack_id)
        # Block new world admissions while keeping loaded objects until restart.
        if services.plugins.has_plugin(owner):
            services.card_library.edit(LibraryEdit(
                action="set_plugin_enabled", id=owner, enabled=False,
                expected_revision=services.card_library.read().revision,
            ))
    await services.events.publish_event(RuntimeEvent(type=EventType.CARD_LIBRARY_UPDATED,
        payload={"revision": services.card_library.snapshot()["revision"]}))
    return status(services)


@router.get("/{pack_id}/uninstall-plan")
async def uninstall_plan(pack_id: str, services=Depends(get_services)):
    result = await checked(services.pack_installations.uninstall_plan, pack_id)
    result['blockers'].extend('In use by ' + usage for usage in plugin_usage(services, result['owner']))
    result['can_uninstall'] = not result['blockers']
    return result


@router.post("/{pack_id}/restore", dependencies=[Depends(mutation_request)])
async def restore_source(pack_id: str, services=Depends(get_services)):
    await checked(services.pack_installations.restore_source, pack_id)
    spec = services.pack_installations.source_specs()[pack_id]
    async with services._node_mutation():
        services.card_library.restore_plugin(spec.owner)
    await services.events.publish_event(RuntimeEvent(type=EventType.CARD_LIBRARY_UPDATED,
        payload={'revision': services.card_library.snapshot()['revision']}))
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
    is_script = path.suffix.lower() in {".js", ".mjs"}
    media_type = "text/javascript" if is_script else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # Native workers inherit their script response policy. A sandbox gives the
    # worker an opaque origin, which blocks even its own verified chunks/WASM.
    # Script responses are non-HTML with nosniff; other public assets retain the
    # document sandbox so navigating to Pack HTML/SVG cannot execute it.
    policy = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; worker-src 'self'"
              if is_script else "default-src 'none'; style-src 'unsafe-inline'; sandbox")
    return FileResponse(path, media_type=media_type, headers={
        "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": policy,
    })
