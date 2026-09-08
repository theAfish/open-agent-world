"""Immutable versions in managed assets, with publication/deletion intent in SQLite."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
from contextlib import asynccontextmanager, aclosing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from backend.errors import ConflictError, NotFoundError, PermissionDeniedError, ResourceValidationError
from backend.sandbox.files import parts, pinned, run_file_operation
from backend.sandbox.transfers import CHUNK_BYTES


def now():
    return datetime.now(UTC).isoformat()


class ArtifactStore:
    def __init__(self, database, root):
        self.database = database
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_version_bytes = int(os.environ.get('OPEN_AGENT_WORLD_ARTIFACT_MAX_BYTES', 1024 ** 3))
        self.max_storage_bytes = int(os.environ.get('OPEN_AGENT_WORLD_ARTIFACT_STORAGE_BYTES', 10 * 1024 ** 3))
        if min(self.max_version_bytes, self.max_storage_bytes) <= 0:
            raise ValueError('Artifact size and storage limits must be positive byte counts')
        self.consumers = {}
        self.source_leases = {}
        self._lock = asyncio.Lock()

    def path(self, version_id, *, staging=False):
        key = str(UUID(version_id))
        return self.root / (key + '.staging' if staging else key)

    def get(self, version_id):
        with self.database.locked() as db:
            row = db.execute('SELECT record_json FROM artifact_versions WHERE version_id=?', (version_id,)).fetchone()
        if row is None:
            raise NotFoundError('Artifact version not found')
        return json.loads(row[0])

    def all(self):
        with self.database.locked() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT record_json FROM artifact_versions ORDER BY rowid')]

    def retained(self):
        return [record for record in self.all()
                if record['state'] == 'ready' and record.get('retention', {}).get('retained') is True]

    def remove_reference(self, services, collection_id, version_id, agent_id=None):
        self.authorize(services, collection_id, agent_id, 'artifact.manage', version_id)
        with self.database.transaction(immediate=True) as db:
            db.execute('DELETE FROM artifact_references WHERE collection_id=? AND version_id=?', (collection_id, version_id))
        return {'removed': True, 'retained': self.get(version_id)['retention']['retained']}

    def add_reference(self, services, collection_id, version_id, agent_id=None, source_collection_id=None):
        self.authorize(services, collection_id, agent_id, 'artifact.manage')
        if agent_id is not None:
            if not source_collection_id:
                raise PermissionDeniedError('Adding a reference requires an authorized source collection')
            self.authorize(services, source_collection_id, agent_id, 'artifact.read', version_id)
        record = self.get(version_id)
        with self.database.transaction(immediate=True) as db:
            db.execute('INSERT OR IGNORE INTO artifact_references VALUES (?,?)', (collection_id, version_id))
        return record

    def run_references(self, run_id):
        if run_id is None:
            return []
        with self.database.locked() as db:
            rows = db.execute("SELECT record_json FROM artifact_versions WHERE json_extract(record_json, '$.provenance.run_id')=? ORDER BY rowid", (run_id,)).fetchall()
        return [{key: record.get(key) for key in ('artifact_id', 'version_id', 'collection_id', 'name', 'state')}
                for row in rows for record in [json.loads(row[0])]]

    def save(self, record):
        with self.database.transaction(immediate=True) as db:
            db.execute('UPDATE artifact_versions SET state=?, record_json=? WHERE version_id=?',
                       (record['state'], json.dumps(record), record['version_id']))

    def authorize(self, services, collection_id, agent_id, kind='artifact.read', version_id=None):
        node = services.world.get_card(collection_id)
        if 'core.artifact-collection' not in services.plugins.node_type(node.type).traits:
            raise ResourceValidationError('Choose an artifact collection')
        if agent_id is not None:
            services.capabilities.capability_for_id(agent_id, f'{kind}:{collection_id}')
        if version_id is not None:
            with self.database.locked() as db:
                row = db.execute('SELECT 1 FROM artifact_references WHERE collection_id=? AND version_id=?',
                                 (collection_id, version_id)).fetchone()
            if row is None:
                raise PermissionDeniedError('This collection does not grant access to that version')

    def listing(self, services, collection_id, agent_id=None):
        self.authorize(services, collection_id, agent_id)
        with self.database.locked() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT v.record_json FROM artifact_versions v JOIN artifact_references r USING(version_id) WHERE r.collection_id=? ORDER BY v.rowid', (collection_id,))]

    def assert_source_idle(self, node_id):
        if self.source_leases.get(node_id):
            raise ConflictError('Resource is in use by an artifact transfer; retry after it finishes')

    @asynccontextmanager
    async def workspace(self, services, sandbox_id, agent_id, *, reserved=False):
        if not reserved:
            async with services._node_mutation():
                self.reserve_source(services, sandbox_id, agent_id)
        try:
            yield
        finally:
            self.source_leases.pop(sandbox_id, None)

    def reserve_source(self, services, sandbox_id, agent_id):
        self.check_source(services, sandbox_id, agent_id)
        services.summoning.assert_admission(sandbox_id)
        if sandbox_id in services._sandbox_commands or sandbox_id in services._sandbox_stopping:
            raise ConflictError('Finalize files and wait for Sandbox execution and cleanup before transfer')
        self.assert_source_idle(sandbox_id)
        self.source_leases[sandbox_id] = 1

    def check_source(self, services, sandbox_id, agent_id):
        services._require_card_type(sandbox_id, 'sandbox')
        if agent_id is not None:
            services.capabilities.require_sandbox_execute(agent_id, sandbox_id)

    def notify(self, services, record):
        from backend.events.models import RuntimeEvent, EventType
        services.events.publish_event_nowait(RuntimeEvent(type=EventType.ARTIFACT_UPDATED,
            run_id=record['provenance']['run_id'], node_id=record.get('collection_id'),
            payload={key: record.get(key) for key in ('artifact_id', 'version_id', 'collection_id', 'state', 'cleanup')}))

    async def publish(self, services, collection_id, request, agent_id=None):
        self.authorize(services, collection_id, agent_id, 'artifact.publish')
        if not request.finalized:
            raise ResourceValidationError('Publishing requires explicitly finalized inputs; pause all external writers first')
        canonical = json.dumps({'collection_id': collection_id, **request.model_dump()}, sort_keys=True)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        owner = agent_id or 'user'
        async with self._lock, services._node_mutation():
            self.authorize(services, collection_id, agent_id, 'artifact.publish')
            with self.database.locked() as db:
                previous = db.execute('SELECT request_hash, record_json FROM artifact_versions WHERE request_owner=? AND request_key=?',
                                      (owner, request.request_key)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise ConflictError('Request key was already used with different publication inputs')
                record = json.loads(previous[1])
                self.authorize(services, collection_id, agent_id, 'artifact.publish', record['version_id'])
                if record['state'] == 'staging':
                    raise ConflictError('Publication is still in progress; inspect this collection before retrying')
                return record
            if request.artifact_id:
                if not any(r['artifact_id'] == request.artifact_id for r in self.listing(services, collection_id, agent_id)):
                    raise PermissionDeniedError('A new version requires current access to that artifact in this collection')
            version_id = str(uuid4())
            context = services.run_manager.current_context
            inputs = []
            for item in request.inputs:
                self.authorize(services, item.collection_id, agent_id, version_id=item.version_id)
                source_version = self.get(item.version_id)
                if source_version['state'] != 'ready':
                    raise ResourceValidationError('Provenance inputs must refer to ready versions')
                inputs.append({key: source_version[key] for key in ('artifact_id', 'version_id', 'content_sha256')})
            source = services.world.get_card(request.sandbox_id)
            record = {'version_id': version_id, 'artifact_id': request.artifact_id or str(uuid4()),
                'collection_id': collection_id,
                'name': request.name, 'state': 'staging', 'created_at': now(), 'size_bytes': 0,
                'manifest': [], 'retention': {'owner': 'user', 'reason': 'explicit publication', 'retained': True},
                'provenance': {'agent_id': agent_id, 'agent_name': services.world.get_card(agent_id).name if agent_id else None,
                    'run_id': context.run_id if context else None, 'sandbox_id': source.id, 'sandbox_name': source.name,
                    'selected_paths': request.paths, 'finalized_inputs': True, 'inputs': inputs},
                'error': None}
            self.check_source(services, request.sandbox_id, agent_id)
            self.reserve_source(services, request.sandbox_id, agent_id)
            try:
                with self.database.transaction(immediate=True) as db:
                    db.execute('INSERT INTO artifact_versions VALUES (?,?,?,?,?,?,?)',
                               (version_id, record['artifact_id'], owner, request.request_key, fingerprint, 'staging', json.dumps(record)))
                    db.execute('INSERT INTO artifact_references VALUES (?,?)', (collection_id, version_id))
            except BaseException:
                self.source_leases.pop(request.sandbox_id, None)
                raise
        committed = False
        try:
            async with self.workspace(services, request.sandbox_id, agent_id, reserved=True):
                backend = services._require_sandbox_backend()
                manifest = await backend.file_operation(request.sandbox_id, 'capture_manifest', paths=request.paths)
                size = sum(item['size'] for item in manifest)
                async with self._lock:
                    if size > self.max_version_bytes:
                        raise ResourceValidationError(f'Artifact exceeds configured version limit of {self.max_version_bytes} bytes')
                    if sum(r['size_bytes'] for r in self.all() if r['state'] in {'ready', 'staging', 'deleting'} or
                           (r['state'] == 'failed' and r.get('cleanup') != 'complete')) + size > self.max_storage_bytes:
                        raise ResourceValidationError(f'Artifact storage limit of {self.max_storage_bytes} bytes exceeded')
                    record['size_bytes'] = size
                    self.save(record)
                staging = self.path(version_id, staging=True)
                await run_file_operation(staging.mkdir)
                captured = []
                for entry in manifest:
                    target = staging.joinpath(*parts(entry['path']))
                    await run_file_operation(target.parent.mkdir, parents=True, exist_ok=True)
                    if entry['directory']:
                        await run_file_operation(target.mkdir, exist_ok=True)
                        captured.append({'path': entry['path'], 'directory': True, 'size': 0})
                        continue
                    digest = hashlib.sha256()
                    with target.open('xb') as output:
                        offset = 0
                        while offset < entry['size'] or offset == 0:
                            self.check_source(services, request.sandbox_id, agent_id)
                            self.authorize(services, collection_id, agent_id, 'artifact.publish')
                            chunk = await backend.file_operation(request.sandbox_id, 'read_chunk', path=entry['path'],
                                                                 offset=offset, expected=entry['signature'])
                            content = base64.b64decode(chunk['data'], validate=True)
                            if len(content) > CHUNK_BYTES or offset + len(content) > entry['size']:
                                raise ResourceValidationError('Source changed during capture')
                            if not content and offset != entry['size']:
                                raise ResourceValidationError('Source was truncated during capture')
                            await run_file_operation(output.write, content)
                            digest.update(content)
                            offset += len(content)
                            if offset == entry['size']:
                                break
                        await run_file_operation(output.flush)
                        await run_file_operation(os.fsync, output.fileno())
                    captured.append({'path': entry['path'], 'directory': False, 'size': offset, 'sha256': digest.hexdigest()})
                if await backend.file_operation(request.sandbox_id, 'capture_manifest', paths=request.paths) != manifest:
                    raise ResourceValidationError('Selected inputs changed during publication; no consistent bundle was published')
                record['manifest'] = captured
                record['content_sha256'] = hashlib.sha256(json.dumps(captured, sort_keys=True).encode()).hexdigest()
                # Flush metadata intent before the atomic directory rename. Restart never promotes staging intent.
                self.save(record)
                await run_file_operation(staging.rename, self.path(version_id))
                async with services._node_mutation():
                    self.check_source(services, request.sandbox_id, agent_id)
                    self.authorize(services, collection_id, agent_id, 'artifact.publish', version_id)
                    for item in request.inputs:
                        self.authorize(services, item.collection_id, agent_id, version_id=item.version_id)
                    record.update(state='ready', ready_at=now())
                    self.save(record)
                    committed = True
                    self.notify(services, record)
                return record
        except BaseException as error:
            if committed:
                raise
            async with self._lock:
                # Preserve intent and diagnostic even if staging cleanup itself fails.
                record.update(state='failed', error=f'{type(error).__name__}: {error}'[:4096], cleanup='pending')
                self.save(record)
                self.notify(services, record)
                try:
                    await self.remove_bytes(record)
                except Exception as cleanup_error:
                    record['cleanup_error'] = str(cleanup_error)[:4096]
                    self.save(record)
                raise

    async def remove_bytes(self, record):
        for staging in (True, False):
            path = self.path(record['version_id'], staging=staging)
            if path.exists():
                await run_file_operation(shutil.rmtree, path)
        record['cleanup'] = 'complete'
        record.pop('cleanup_error', None)
        if record['state'] == 'deleting':
            record['state'] = 'deleted'
        self.save(record)

    async def recover(self, *, interrupted=True):
        async with self._lock:
            for record in self.all():
                # Ready versions have no recovery intent. Full verification is explicit
                # (and streamed consumption still checks file sizes and checksums).
                if record['state'] == 'staging':
                    if not interrupted:
                        continue
                    record.update(state='failed', error='Backend interrupted publication; use a new request key', cleanup='pending')
                    self.save(record)
                if record['state'] == 'deleting' or (record['state'] == 'failed' and record.get('cleanup') in {'pending', 'failed'}):
                    try:
                        await self.remove_bytes(record)
                    except Exception as error:
                        record.update(cleanup='failed', cleanup_error=str(error)[:4096])
                        self.save(record)

    async def verify(self, services, collection_id, version_id):
        async with self.consume(services, collection_id, version_id) as record:
            try:
                await run_file_operation(self.validate, record)
            except ResourceValidationError as error:
                return {'version_id': version_id, 'integrity': 'corrupt', 'error': str(error)}
            except OSError as error:
                return {'version_id': version_id, 'integrity': 'unavailable', 'error': str(error)}
            return {'version_id': version_id, 'integrity': 'verified'}

    def validate(self, record):
        for entry in record['manifest']:
            target = self.path(record['version_id']).joinpath(*parts(entry['path']))
            with pinned(target, directory=entry['directory']) as fd:
                if entry['directory']:
                    continue
                if os.fstat(fd).st_size != entry['size']:
                    raise ResourceValidationError('Stored file size differs from the committed manifest')
                digest = hashlib.sha256()
                while data := os.read(fd, CHUNK_BYTES):
                    digest.update(data)
                if digest.hexdigest() != entry['sha256']:
                    raise ResourceValidationError('Stored file checksum differs from the committed manifest')

    @asynccontextmanager
    async def consume(self, services, collection_id, version_id, agent_id=None):
        async with self._lock:
            self.authorize(services, collection_id, agent_id, version_id=version_id)
            record = self.get(version_id)
            if record['state'] != 'ready':
                raise ConflictError('Only ready artifact versions can be consumed')
            self.consumers[version_id] = self.consumers.get(version_id, 0) + 1
        try:
            yield record
        finally:
            self.consumers[version_id] -= 1

    async def release(self, services, collection_id, version_id, agent_id=None):
        if agent_id is not None:
            raise PermissionDeniedError('Only the trusted user control plane can release retained content')
        async with self._lock:
            self.authorize(services, collection_id, agent_id, 'artifact.manage', version_id)
            record = self.get(version_id)
            if record.get('retention', {}).get('owner') != 'user':
                raise PermissionDeniedError('Content release requires its retention owner authority')
            if record['state'] == 'deleted':
                return record
            if record['state'] == 'staging' or self.consumers.get(version_id):
                raise ConflictError('Artifact is in use; retry release when publication or consumption finishes')
            record.update(state='deleting', cleanup='pending', released_at=now())
            record['retention']['retained'] = False
            self.save(record)
            try:
                await self.remove_bytes(record)
            except BaseException as error:
                record.update(cleanup='failed', cleanup_error=str(error)[:4096])
                self.save(record)
                raise
            self.notify(services, record)
            return record

    async def read(self, services, collection_id, version_id, path, agent_id=None):
        async with self.consume(services, collection_id, version_id, agent_id) as record:
            entry = next((e for e in record['manifest'] if e['path'] == path and not e['directory']), None)
            if entry is None:
                raise NotFoundError('File is not in this artifact manifest')
            target = self.path(version_id).joinpath(*parts(path))
            with pinned(target) as fd:
                if os.fstat(fd).st_size != entry['size']:
                    raise ResourceValidationError('Stored artifact size differs from the manifest')
                digest = hashlib.sha256()
                while True:
                    self.authorize(services, collection_id, agent_id, version_id=version_id)
                    data = await run_file_operation(os.read, fd, CHUNK_BYTES)
                    if not data:
                        break
                    digest.update(data)
                    self.authorize(services, collection_id, agent_id, version_id=version_id)
                    yield data
                if digest.hexdigest() != entry['sha256']:
                    raise ResourceValidationError('Stored artifact checksum differs from the manifest')

    async def preview(self, services, collection_id, version_id, path, agent_id=None):
        content = bytearray()
        async with aclosing(self.read(services, collection_id, version_id, path, agent_id)) as stream:
            async for chunk in stream:
                content.extend(chunk[:65536 - len(content)])
                if len(content) == 65536:
                    break
        try:
            text = content.decode('utf-8')
            if '\0' in text:
                raise ValueError()
            return {'state': 'text', 'text': text, 'limit': 65536, 'truncated': len(content) == 65536}
        except (UnicodeError, ValueError):
            return {'state': 'binary', 'limit': 65536}

    async def materialize(self, services, collection_id, version_id, request, agent_id=None):
        async with self.consume(services, collection_id, version_id, agent_id) as record, self.workspace(services, request.sandbox_id, agent_id):
            backend = services._require_sandbox_backend()
            prefix = '/'.join(parts(request.destination))
            if not prefix:
                raise ResourceValidationError('Choose a new destination directory')
            await backend.file_operation(request.sandbox_id, 'transfer_mkdir', path=prefix)
            directories = {prefix}
            for entry in record['manifest']:
                target = prefix + '/' + entry['path']
                parent_parts = parts(target) if entry['directory'] else parts(target)[:-1]
                for length in range(1, len(parent_parts) + 1):
                    directory = '/'.join(parent_parts[:length])
                    if directory not in directories:
                        self.authorize(services, collection_id, agent_id, version_id=version_id)
                        self.check_source(services, request.sandbox_id, agent_id)
                        await backend.file_operation(request.sandbox_id, 'transfer_mkdir', path=directory)
                        directories.add(directory)
                if entry['directory']:
                    continue
                digest = hashlib.sha256()
                offset = 0
                async for content in self.read(services, collection_id, version_id, entry['path'], agent_id):
                    self.check_source(services, request.sandbox_id, agent_id)
                    await backend.file_operation(request.sandbox_id, 'write_chunk', path=target, offset=offset,
                                                 data=base64.b64encode(content).decode())
                    digest.update(content)
                    offset += len(content)
                if offset == 0:
                    await backend.file_operation(request.sandbox_id, 'write_chunk', path=target, offset=0, data='')
                if offset != entry['size'] or digest.hexdigest() != entry['sha256']:
                    raise ResourceValidationError('Artifact integrity verification failed; destination is incomplete')
            self.authorize(services, collection_id, agent_id, version_id=version_id)
            self.check_source(services, request.sandbox_id, agent_id)
            return {'version_id': version_id, 'destination': prefix, 'state': 'copied',
                    'note': 'Mutable working copy; publish a new version after editing. Revocation does not erase existing copies.'}
