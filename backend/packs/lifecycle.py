"""References and process leases for host-managed Pack resources.

Only explicitly registered directories with validated identities are removable. Source
trees, host site-packages, external programs, credentials and Pack user data
never enter this ledger. OS locks, rather than PIDs or timestamps, protect live
processes across application restarts and concurrent profile instances.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import re
import shutil
import sqlite3
import stat
from uuid import uuid4

from .environments import installation_lock, installation_is_active

_PROCESS_PINS = ExitStack()
atexit.register(_PROCESS_PINS.close)
_KINDS = {
    'environment': ('environment:', 'runtime/environments'),
    'environment-stage': ('stage:', 'runtime/staging'),
    'adapter-wheel': ('adapter:', 'packs/adapter-wheels'),
    'modules': ('modules:', 'packs/cache/modules'),
}


class ManagedResourceLedger:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve()
        self.path = self.root / 'packs/resources.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS claims (
                    consumer TEXT NOT NULL, resource TEXT NOT NULL, lease TEXT,
                    PRIMARY KEY(consumer, resource));
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            ''')

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def _path(self, resource_id, path, kind):
        if kind not in _KINDS:
            raise ValueError('Unknown managed resource kind')
        prefix, directory = _KINDS[kind]
        digest = resource_id.removeprefix(prefix)
        if kind == 'environment-stage':
            fingerprint, _, name = digest.partition(':')
            valid = re.fullmatch(r'[a-f0-9]{64}', fingerprint) and re.fullmatch(
                rf'(?:{fingerprint}-(?:invalid-)?)[a-f0-9]{{32}}|[a-f0-9]{{32}}', name)
            digest = name
        else:
            valid = re.fullmatch(r'[a-f0-9]{64}', digest)
        if not resource_id.startswith(prefix) or not valid:
            raise ValueError('Managed resources require a content digest')
        expected = self.root / directory / digest
        candidate = Path(path).absolute()
        if candidate != expected or candidate.resolve() != expected:
            raise ValueError('Resource is outside its managed directory')
        for parent in (candidate, *candidate.parents):
            if parent == self.root:
                break
            if parent.exists() or parent.is_symlink():
                info = parent.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Managed resources cannot contain links or reparse points')
        return candidate

    def register(self, resource_id: str, path: Path, kind: str):
        path = self._path(resource_id, path, kind)
        relative = path.relative_to(self.root).as_posix()
        with self.connect(write=True) as db:
            existing = db.execute('SELECT * FROM resources WHERE id=?', (resource_id,)).fetchone()
            if existing and (existing['path'] != relative or existing['kind'] != kind):
                raise ValueError('Managed resource identity changed')
            db.execute('INSERT OR IGNORE INTO resources VALUES (?,?,?)', (resource_id, relative, kind))

    def claim(self, consumer: str, resource_id: str):
        lease = None
        if consumer.startswith('live:'):
            lease = consumer.split(':', 2)[1]
            if not installation_is_active(self._lease_path(lease)):
                raise ValueError('The resource operation lease is no longer active')
        with self.connect(write=True) as db:
            db.execute('INSERT OR IGNORE INTO claims VALUES (?,?,?)', (consumer, resource_id, lease))

    def next_revision(self):
        with self.connect(write=True) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='profile_revision'").fetchone()
            value = (row['value'] if row else 0) + 1
            db.execute("INSERT INTO metadata VALUES ('profile_revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
        return value

    def release_consumer(self, consumer: str):
        with self.connect(write=True) as db:
            db.execute('DELETE FROM claims WHERE consumer=? AND lease IS NULL', (consumer,))

    def replace_claims(self, prefix: str, claims: dict[str, set[str]]):
        """Atomically reconcile one authority; process leases are independent."""
        if not prefix.endswith(':') or any(not key.startswith(prefix) for key in claims):
            raise ValueError('Invalid claim namespace')
        with self.connect(write=True) as db:
            for row in db.execute('SELECT DISTINCT consumer FROM claims WHERE lease IS NULL').fetchall():
                if row['consumer'].startswith(prefix):
                    db.execute('DELETE FROM claims WHERE consumer=?', (row['consumer'],))
            db.executemany('INSERT INTO claims VALUES (?,?,NULL)',
                ((consumer, resource) for consumer, resources in claims.items() for resource in resources))

    def _lease_path(self, token):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            raise ValueError('Invalid process lease')
        return self.root / 'packs/resource-leases' / (token + '.lock')

    @contextmanager
    def lease_many(self, resources, *, label='worker'):
        resources = set(resources)
        token = uuid4().hex
        consumer = f'live:{token}:{label}'
        with installation_lock(self._lease_path(token)):
            with self.connect(write=True) as db:
                db.executemany('INSERT INTO claims VALUES (?,?,?)',
                    ((consumer, resource, token) for resource in resources))
            try:
                yield consumer
            finally:
                with self.connect(write=True) as db:
                    db.execute('DELETE FROM claims WHERE lease=?', (token,))

    def lease(self, resource_id):
        return self.lease_many((resource_id,))

    def pin_process(self, resources, *, label):
        # Imported Python modules/native libraries cannot safely be unloaded at
        # services.shutdown(). Keep this pin for the interpreter's lifetime.
        _PROCESS_PINS.enter_context(self.lease_many(resources, label=label))

    def _claims(self, db, resource):
        result = []
        for row in db.execute('SELECT * FROM claims WHERE resource=?', (resource,)).fetchall():
            if row['lease']:
                try:
                    alive = installation_is_active(self._lease_path(row['lease']))
                except OSError:
                    alive = True  # Unknown liveness must retain the resource.
                if not alive:
                    db.execute('DELETE FROM claims WHERE lease=?', (row['lease'],))
                    continue
            result.append(dict(row))
        return result

    def preview(self, resources=None, *, releasing=()):
        result = []
        releasing = set(releasing)
        with self.connect(write=True) as db:
            for row in db.execute('SELECT * FROM resources ORDER BY id').fetchall():
                if resources is not None and row['id'] not in resources:
                    continue
                claims = [claim for claim in self._claims(db, row['id']) if claim['consumer'] not in releasing]
                durable = [claim for claim in claims if not claim['lease']]
                recovery = self._recoverable_stage(row)
                preparing = installation_is_active(self._preparation_lock(row))
                action = 'retain' if durable or recovery else 'defer' if claims or preparing else 'remove'
                result.append({'id': row['id'], 'kind': row['kind'], 'action': action,
                    'reason': 'Verified installation work is retained for retry' if recovery else
                        'Referenced by another Pack' if durable else
                        'A running process still uses this resource; cleanup follows restart' if claims or preparing else
                        'No remaining Pack references',
                    'consumers': [claim['consumer'] for claim in claims]})
        return result

    def _preparation_lock(self, row):
        digest = row['id'].split(':', 1)[1]
        if row['kind'] in {'environment', 'environment-stage'}:
            digest = digest.split(':', 1)[0]
            return self.root / 'runtime/locks' / (digest + '.lock')
        if row['kind'] == 'adapter-wheel':
            return self.root / 'packs/adapter-locks' / (digest + '.lock')
        return self.root / 'packs/cache/modules' / (digest + '.lock')

    def _recoverable_stage(self, row):
        if row['kind'] != 'environment-stage':
            return False
        fingerprint = row['id'].split(':')[1]
        path = self._path(row['id'], self.root / row['path'], row['kind'])
        operation_path = self.root / 'runtime/operations' / (fingerprint + '.json')
        try:
            operation = json.loads(operation_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            operation = {}
        if not isinstance(operation, dict):
            raise ValueError('Invalid environment operation receipt')
        return (operation.get('status') != 'ready'
                and path.name in {operation.get('stage'), operation.get('recovery_stage')}
                and ((path / 'ready.json').is_file() or (path / 'payload/ready.json').is_file()))

    def collect(self, resources=None):
        """Remove unreferenced resources; partial deletion is retryable.

        Hold both the ledger write transaction and the provider lock across
        deletion. Preparation cannot publish a replacement while GC is running.
        Missing/partially removed directories remain safe to retry after a crash.
        """
        if resources is not None and not resources:
            return []
        results = []
        with self.connect() as db:
            query = 'SELECT * FROM resources'
            parameters = tuple(resources) if resources is not None else ()
            if resources is not None:
                query += ' WHERE id IN (' + ','.join('?' for _ in parameters) + ')'
            rows = [dict(row) for row in db.execute(query, parameters)]
        for row in rows:
            try:
                with self.connect(write=True) as db:
                    if self._claims(db, row['id']):
                        continue
                    with installation_lock(self._preparation_lock(row), timeout=0):
                        path = self._path(row['id'], self.root / row['path'], row['kind'])
                        # An inactive OS lock alone does not make verified,
                        # resumable operation state disposable.
                        if self._recoverable_stage(row):
                            continue
                        if path.exists():
                            # rmtree never follows internal symlinks/junctions on
                            # supported Python versions. Only the real owned root
                            # is removable; interpreter links are merely unlinked.
                            shutil.rmtree(path)
                        db.execute('DELETE FROM resources WHERE id=?', (row['id'],))
                        results.append({'id': row['id'], 'state': 'removed'})
            except (OSError, RuntimeError, ValueError) as exc:
                results.append({'id': row['id'], 'state': 'deferred', 'reason': type(exc).__name__})
        return results

    def require_idle(self, resource):
        """The provider lock prevents new workers while a damaged payload moves."""
        with self.connect(write=True) as db:
            if any(claim['lease'] for claim in self._claims(db, resource)):
                raise RuntimeError('A running process still uses this environment; stop it before repair')


@contextmanager
def managed_environment_stages(runtime_root, fingerprint):
    """Journal exact provider-owned stages and collect after releasing the lock.

    The operation receipt protects verified recovery work. Failed removal stays
    in the existing ledger for the next resource collection.
    """
    ledger = ManagedResourceLedger(Path(runtime_root).parent)
    resources = set()

    def track_stage(stage):
        if stage is None:
            return
        resource = f'stage:{fingerprint}:{stage.name}'
        ledger.register(resource, stage, 'environment-stage')
        resources.add(resource)

    try:
        yield track_stage
    finally:
        ledger.collect(resources)


def environment_resources(spec, source_root):
    """Pure recipe identities; no package import, download or preparation."""
    result = set()
    for name, declaration in spec.environments.items():
        if declaration.target != 'host-worker' or declaration.management != 'managed':
            continue
        if declaration.lock and declaration.provider != 'python':
            # Provider capability diagnostics belong to the plan. Unsupported
            # recipes have no prepared managed resource to release.
            continue
        if declaration.provider == 'python':
            from .environments import PythonEnvironmentSpec
            recipe = PythonEnvironmentSpec.from_declaration(spec.owner, name, declaration, source_root)
        elif declaration.provider == 'executable':
            from .executables import ExecutableEnvironmentSpec
            recipe = ExecutableEnvironmentSpec.from_declaration(spec.owner, name, declaration, source_root)
        else:
            continue
        result.add('environment:' + recipe.fingerprint)
    return result
