"""Host Store: remote metadata/acquisition, existing installation lifecycle."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from packaging.version import InvalidVersion, Version

from backend.api.dependencies import get_services
from backend.api.packs import checked, mutation_request
from backend.packs.marketplace import Identity, MarketplaceError, PackId

router = APIRouter(prefix="/store/packs", tags=["store"])
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=200)]


@contextmanager
def remote_errors():
    try:
        yield
    except MarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


def local_state(services, pack_id, available_version, snapshot=None):
    snapshot = snapshot or services.pack_installations.status()
    rows = [row for row in snapshot["versions"] if row["id"] == pack_id]
    selected = next((row["version"] for row in rows if row["selected"]), None)
    loaded = next((row["version"] for row in rows if row["loaded"]), None)
    bundled = False
    if not rows:
        pack = next((p for p in services.plugins.catalog(include_disabled=True).packs if p.id == pack_id), None)
        if pack and pack.plugin_id not in services.plugins.installed_packs:
            loaded = selected = next(p.version for p in services.plugins.plugins() if p.id == pack.plugin_id)
            bundled = True
    installed = selected or loaded
    try:
        update = bool(installed and available_version and Version(available_version) > Version(installed))
        can_install = bool(available_version and not bundled and
                           (selected is None or Version(available_version) > Version(selected)))
    except InvalidVersion:
        update = False
        can_install = False
    return {"installed_version": installed, "loaded_version": loaded,
            "available_version": available_version, "update_available": update,
            "restart_required": selected != loaded,
            "can_install": can_install}


def listing(services, value, snapshot=None):
    return {**value.model_dump(), **local_state(services, value.id, value.latest_version, snapshot)}


def identity(pack_id: PackId, version: Annotated[str, Path(min_length=1, max_length=64)]):
    try:
        return Identity(pack_id=pack_id, version=version)
    except ValueError:
        raise HTTPException(422, "Invalid Pack version.") from None


async def while_connected(request: Request, operation):
    """Discard an acquisition when its caller leaves; close the upstream stream."""
    async def disconnect():
        while (await request.receive())["type"] != "http.disconnect":
            pass
    task = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnect())
    try:
        done, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            raise HTTPException(499, "Store request cancelled.")
        return await task
    finally:
        task.cancel(); watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


@router.get("")
async def catalog(query: Annotated[str | None, Query(max_length=200)] = None,
                  cursor: Cursor = None, limit: Limit = 20, services=Depends(get_services)):
    with remote_errors():
        page = await services.marketplace.list_packs(query=query, cursor=cursor, limit=limit)
    snapshot = services.pack_installations.status()
    return {"items": [listing(services, item, snapshot) for item in page.items], "next_cursor": page.next_cursor}


@router.get("/{pack_id}")
async def detail(pack_id: PackId, services=Depends(get_services)):
    with remote_errors():
        return listing(services, await services.marketplace.get_pack(pack_id))


@router.get("/{pack_id}/versions")
async def versions(pack_id: PackId, cursor: Cursor = None, limit: Limit = 20, services=Depends(get_services)):
    with remote_errors():
        return (await services.marketplace.list_versions(pack_id, cursor=cursor, limit=limit)).model_dump()


@router.get("/{pack_id}/versions/{version}")
async def version(target=Depends(identity), services=Depends(get_services)):
    with remote_errors():
        metadata = await services.marketplace.get_version(target.pack_id, target.version)
    return {**metadata.model_dump(), **local_state(services, target.pack_id, target.version)}


@router.post("/{pack_id}/versions/{version}/install", dependencies=[Depends(mutation_request)])
async def install(request: Request, target=Depends(identity), services=Depends(get_services)):
    # Always fetch authoritative metadata for the explicit version, not catalog
    # state. Keep network waiting outside the world's mutation transaction.
    with remote_errors():
        metadata = await while_connected(request, services.marketplace.get_version(target.pack_id, target.version))
    async with services._node_mutation():
        rows = services.pack_installations.status()["versions"]
        retained = next((r for r in rows if (r["id"], r["version"]) == (target.pack_id, target.version)), None)
        if retained:
            if retained["digest"] != metadata.sha256:
                raise HTTPException(409, "Store artifact differs from the installed immutable version.")
            if not retained["selected"]:
                await checked(services.pack_installations.activate, target.pack_id, target.version)
            return local_state(services, target.pack_id, target.version)
    with remote_errors():
        data = await while_connected(request, services.marketplace.download(metadata))
    async with services._node_mutation():
        await checked(partial(services.pack_installations.install, data,
            expected_id=target.pack_id, expected_version=target.version, expected_sha256=metadata.sha256))
    return local_state(services, target.pack_id, target.version)
