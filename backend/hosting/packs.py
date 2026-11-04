"""Administrator-approved Pack versions, using the normal nonroot installer.

The gateway retains verified archives as data. The privileged reconciler may copy
them, but preparation and plugin imports belong to a private worker UID.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import time

from pydantic import BaseModel, ConfigDict, Field
from backend.packs.metadata import PACK_ID


class PackLock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=PACK_ID, max_length=120)
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,63}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def pack_locks(value):
    if not isinstance(value, (list, tuple)) or len(value) > 32:
        raise ValueError("Hosted releases support at most 32 installed Packs")
    result = [PackLock.model_validate(item).model_dump() for item in value]
    if len({item['id'] for item in result}) != len(result):
        raise ValueError("Hosted Pack locks require unique distribution IDs")
    return sorted(result, key=lambda item:item['id'])


def management_path(path, method):
    path = path.rstrip('/')
    if method in {'GET','HEAD'}:
        return path == '/api/packs' or bool(re.fullmatch(r'/api/packs/[^/]+/uninstall-plan',path))
    if method == 'POST':
        return (path in {'/api/packs/inspect','/api/packs/install','/api/packs/environment/retry','/api/application/restart'}
            or bool(re.fullmatch(r'/api/store/packs/[^/]+/versions/[^/]+/install',path))
            or bool(re.fullmatch(r'/api/packs/[^/]+/(?:activate|restore)',path)))
    return method == 'DELETE' and bool(re.fullmatch(r'/api/packs/[^/]+(?:/versions/[^/]+)?',path))


def management_grant(secret, profile_id, method, path, timestamp=None):
    issued = int(time.time()) if timestamp is None else timestamp
    message = f'{profile_id}:{method}:{path}:{issued}'.encode()
    signature = hmac.digest(bytes.fromhex(secret),b'oaw-pack-management-v1\0'+message,'sha256').hex()
    return f'{issued}.{signature}'


def authorized(scope, config):
    if not config.pack_management or config.mode != 'builder' or scope['type'] != 'http':
        return False
    path,method = scope['path'].rstrip('/'),scope['method']
    if not management_path(path,method):
        return False
    values = [v.decode('ascii',errors='replace') for k,v in scope.get('headers',[]) if k.lower()==b'x-oaw-pack-authority']
    try:
        issued = int(values[0].split('.')[0])
        return (len(values)==1 and time.time()-30 < issued <= time.time()+5
            and hmac.compare_digest(values[0],management_grant(config.secret,config.profile_id,method,path,issued)))
    except (ValueError,IndexError):
        return False


def selected_locks(services):
    state = services.pack_installations.status()
    selected = [row for row in state['versions'] if row['selected']]
    if any(not row['loaded'] for row in selected):
        raise ValueError("Restart the administrator workspace before publishing installed Packs")
    return pack_locks([{'id':row['id'],'version':row['version'],'sha256':row['digest']} for row in selected])


def load_approved(registry, settings):
    approvals = pack_locks(settings.hosted_pack_versions)
    if not settings.hosted_pack_management and not approvals:
        return
    from backend.packs.installation import PackInstallationManager
    from backend.plugins.loader import load_installed_packs
    manager = PackInstallationManager(settings.data_root, registry)
    if not settings.hosted_pack_management:
        rows = manager.status()['versions']
        actual = pack_locks([{'id':row['id'],'version':row['version'],'sha256':row['digest']} for row in rows if row['selected']])
        if actual != approvals:
            raise ValueError("Installed Packs differ from the administrator-approved release")
    load_installed_packs(registry,settings.data_root,strict=True)


def _regular(path, limit):
    if path.is_symlink() or path.parent.is_symlink() or path.resolve(strict=True) != path.absolute():
        raise ValueError("Pack archives must not traverse symlinks")
    descriptor = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    stream = os.fdopen(descriptor,'rb')
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        stream.close()
        raise ValueError("Expected a bounded regular Pack archive")
    return stream


def retain_archive(directory, data):
    """Inspect data only: no plugin factory, dependency install, or author import."""
    from backend.packs.archive import inspect_archive, MAX_ARCHIVE_BYTES
    pack = inspect_archive(data)
    directory = Path(directory)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("Pack cache must not use symlinks")
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    path = directory/(pack.digest+'.oawpack')
    if path.exists() or path.is_symlink():
        with _regular(path,MAX_ARCHIVE_BYTES) as source:
            if hashlib.file_digest(source,'sha256').hexdigest() != pack.digest:
                raise ValueError("Retained Pack archive failed verification")
        return pack
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory,delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return pack


def stage_archives(cache, instance, versions, *, group=None):
    """Fixed data copy for the root reconciler. Never prepare or import a Pack."""
    import shutil
    versions = pack_locks(versions)
    if not versions:
        return
    destination = Path(instance)/'pack-inputs'
    if destination.exists() or destination.is_symlink():
        raise ValueError("Pack inputs already exist; inspect the prior provisioning attempt")
    destination.mkdir(mode=0o750)
    if group is not None:
        os.chown(destination,0,group)
    for lock in versions:
        filename = lock['sha256']+'.oawpack'
        path = destination/filename
        # Keep privileged validation independent of candidate SDK/plugin imports.
        with _regular(Path(cache)/filename,128*1024*1024) as source, path.open('xb') as output:
            shutil.copyfileobj(source,output)
        with path.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=lock['sha256']:
                raise ValueError("Approved Pack archive failed verification")
        path.chmod(0o640)
        if group is not None:
            os.chown(path,0,group)


async def install_staged(config, versions, registry):
    from functools import partial
    from zipfile import ZipFile
    from backend.packs.manifest import PackManifest
    from backend.packs.installation import PackInstallationManager
    from backend.sandbox.python_runtime import finish_thread
    versions = pack_locks(versions)
    if versions != pack_locks(config.approved_packs):
        raise ValueError("Template Packs differ from the operator-bound release")
    manager = PackInstallationManager(config.data_root,registry)
    pending = {}
    for lock in versions:
        path = config.data_root.parent/'pack-inputs'/(lock['sha256']+'.oawpack')
        with _regular(path,128*1024*1024) as source, ZipFile(source) as archive, archive.open('manifest.json') as metadata:
            raw = metadata.read(1024*1024+1)
            if len(raw)>1024*1024:
                raise ValueError('Pack manifest exceeds its metadata limit')
            manifest = PackManifest.model_validate_json(raw)
        if (manifest.id,manifest.version)!=(lock['id'],lock['version']):
            raise ValueError('Staged Pack identity differs from its approved release')
        pending[lock['id']] = (lock,manifest,path)
    providers = {provided:manifest.id for _,manifest,_ in pending.values() for provided in manifest.provided_pack_ids}
    while pending:
        ready = [key for key,(_,manifest,_) in pending.items()
                 if not any(providers.get(dep.id) in pending for dep in manifest.dependencies.packs)]
        if not ready:
            raise ValueError('Hosted release contains cyclic Pack dependencies')
        for key in ready:
            lock,_,path = pending.pop(key)
            with _regular(path,128*1024*1024) as source:
                data=source.read()
            await finish_thread(partial(manager.install,data,expected_id=lock['id'],expected_version=lock['version'],expected_sha256=lock['sha256']))


async def acquire_for_gateway(gateway, request):
    """Return a verified body for existing install routes, preserving their API."""
    path = request.url.path.rstrip('/')
    match = re.fullmatch(r'/api/store/packs/([^/]+)/versions/([^/]+)/install',path)
    if request.method!='POST' or not (match or path=='/api/packs/install'):
        return None
    if match:
        from backend.packs.marketplace import MarketplaceClient
        from backend.official_marketplace import OFFICIAL_MARKETPLACE_URL
        client = MarketplaceClient(OFFICIAL_MARKETPLACE_URL)
        metadata = await client.get_version(*match.groups())
        data = await client.download(metadata)
    else:
        from backend.api.packs import archive_body
        data = await archive_body(request)
    pack = await asyncio.to_thread(retain_archive,gateway.settings.accounts_path.parent/'pack-archives',data)
    if match and (pack.manifest.id,pack.manifest.version,pack.digest)!=(metadata.pack_id,metadata.version,metadata.sha256):
        raise ValueError("Store artifact identity differs from the approved version")
    return data
