"""Bounded file snapshots on ECS; all user commands execute in Alibaba Sandbox.

Only regular workspace files and directories cross the boundary. Host paths,
symlinks, special files and executable archive extraction are never accepted.
An uncertain command or snapshot keeps its durable receipt and remote identity.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
from uuid import uuid4

from backend.sandbox.files import file_operation, run_file_operation
from backend.sandbox.models import ResourceAccess, SandboxStateError, SandboxValidationError
from .sandbox import AliyunSandboxBackend

MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_JSON = MAX_BYTES * 2 + 1024 * 1024


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 1024 or '\\' in value or ':' in value
            or '\0' in value or value.startswith('/') or str(PurePosixPath(value)) != value
            or any(part in {'.', '..'} or len(part) > 255 or part.endswith(('.', ' '))
                   or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
                       *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}
                   for part in value.split('/'))):
        raise ValueError('Snapshot contains an unsafe relative path')
    return value


def validate_snapshot(value):
    if not isinstance(value, dict) or set(value) != {'directories', 'files'}:
        raise ValueError('Invalid workspace snapshot')
    directories, files = value['directories'], value['files']
    if not isinstance(directories, list) or not isinstance(files, dict) or len(directories) + len(files) > MAX_ENTRIES:
        raise ValueError('Workspace snapshot exceeds its entry limit')
    seen = set()
    for name in [*directories, *files]:
        relative_path(name)
        key = name.casefold()
        if key in seen:
            raise ValueError('Duplicate or case-colliding snapshot path')
        seen.add(key)
    file_names = {name.casefold() for name in files}
    for name in [*directories, *files]:
        if any(str(parent).casefold() in file_names for parent in PurePosixPath(name).parents):
            raise ValueError('A snapshot file cannot contain another entry')
    total = 0
    decoded = {}
    for name, entry in files.items():
        if (not isinstance(entry, dict) or set(entry) != {'data', 'executable'}
                or not isinstance(entry['data'], str) or type(entry['executable']) is not bool
                or len(entry['data']) > MAX_JSON):
            raise ValueError('Invalid workspace file')
        content = base64.b64decode(entry['data'], validate=True)
        total += len(content)
        if total > MAX_BYTES:
            raise ValueError('Workspace snapshot exceeds 32 MiB')
        decoded[name] = (content, entry['executable'])
    return directories, decoded


def read_snapshot(root):
    root = Path(root)
    value = {'directories': [], 'files': {}}
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            before = path.lstat()
            relative = relative_path(path.relative_to(root).as_posix())
            if stat.S_ISDIR(before.st_mode):
                value['directories'].append(relative)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                total += before.st_size
                if total > MAX_BYTES:
                    raise ValueError('Workspace exceeds the 32 MiB POC transfer limit')
                with path.open('rb') as stream:
                    data = stream.read(MAX_BYTES + 1)
                after = path.lstat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
                    raise ValueError('Workspace changed during snapshot collection')
                value['files'][relative] = {'data':base64.b64encode(data).decode('ascii'), 'executable':bool(before.st_mode & 0o111)}
            else:
                raise ValueError('Workspace snapshots support only regular files and directories')
            if len(value['files']) + len(value['directories']) > MAX_ENTRIES:
                raise ValueError('Workspace exceeds the POC entry limit')
    validate_snapshot(value)
    return value


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root)
        self.generations = self.root/'generations'
        self.pointer = self.root/'current.json'

    def current(self):
        if not self.pointer.exists():
            self.commit({'directories': [], 'files': {}})
        generation = json.loads(self.pointer.read_text(encoding='utf-8'))['generation']
        if not re.fullmatch('[0-9a-f]{32}', generation):
            raise ValueError('Invalid local workspace generation')
        path = self.generations/generation
        if path.is_symlink() or not path.is_dir():
            raise ValueError('Invalid local workspace directory')
        return path

    def snapshot(self):
        return read_snapshot(self.current())

    def commit(self, value):
        directories, files = validate_snapshot(value)
        self.generations.mkdir(parents=True, exist_ok=True, mode=0o700)
        previous = json.loads(self.pointer.read_text())['generation'] if self.pointer.exists() else None
        generation = uuid4().hex
        stage = self.generations/generation
        stage.mkdir(mode=0o700)
        for name in directories:
            (stage/name).mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, (content, executable) in files.items():
            path = stage/name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o700 if executable else 0o600)
        if os.name != 'nt':
            for directory, _, _ in os.walk(stage, topdown=False):
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        temporary = self.root/('current.' + generation + '.tmp')
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump({'generation':generation}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.pointer)
        if os.name != 'nt':
            for path in (self.generations, self.root):
                descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        # Retain the current and previous checkpoint. Validate every removal.
        for candidate in self.generations.iterdir():
            if (candidate.name not in {generation,previous} and re.fullmatch('[0-9a-f]{32}', candidate.name)
                    and candidate.is_dir() and not candidate.is_symlink()
                    and candidate.resolve().parent == self.generations.resolve()):
                shutil.rmtree(candidate)


def remote_command(action):
    # Ship only the standard-library snapshot codec, never application code or
    # credentials. The remaining module contains host-only imports/classes.
    source = Path(__file__).read_text(encoding='utf-8')
    codec = source[source.index('MAX_BYTES ='):source.index('\n\nclass SnapshotStore:')]
    setup = '''import base64,json,os,re,stat,sys,signal
from pathlib import Path,PurePosixPath
if os.geteuid() != 0: raise RuntimeError('Workspace transfer requires guest root')
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit(): continue
    try:
        uid=next(line for line in (entry/'status').read_text().splitlines() if line.startswith('Uid:'))
        if '1000' in uid.split()[1:]: os.kill(int(entry.name),signal.SIGKILL)
    except (FileNotFoundError,ProcessLookupError,StopIteration): pass
'''
    restore = '''
value=json.loads(sys.stdin.readline(MAX_JSON+1))
directories,files=validate_snapshot(value)
root=Path('/workspace')
if root.is_symlink(): raise ValueError('Unexpected workspace symlink')
root.mkdir(exist_ok=True)
import shutil
for entry in root.iterdir():
    if entry.is_dir() and not entry.is_symlink(): shutil.rmtree(entry)
    else: entry.unlink()
for name in directories: (root/name).mkdir(parents=True,exist_ok=True)
for name,(content,executable) in files.items():
    path=root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o700 if executable else 0o600)
for directory,dirs,names in os.walk(root):
    os.chown(directory,1000,1000)
    for name in names: os.chown(Path(directory)/name,1000,1000)
print('OAW_WORKSPACE_RESTORED')
'''
    snapshot = "print(json.dumps(read_snapshot(Path('/workspace'))))\n"
    if action not in {'restore', 'snapshot'}:
        raise ValueError('Unknown snapshot action')
    encoded = base64.b64encode((setup+codec+(restore if action=='restore' else snapshot)).encode()).decode()
    command = shlex.join(('/usr/bin/python3','-I','-S','-c',
                         'import base64;exec(base64.b64decode('+repr(encoded)+'))'))
    # Business commands use UID 1000. Stop remaining descendants before reading
    # their files or restoring a dirty idle VM; this helper runs as guest root.
    return command


class EcsWorkspaceSandboxBackend(AliyunSandboxBackend):
    def __init__(self, root, settings, event_sink=None, *, sdk=None):
        super().__init__(root, settings, event_sink, sdk=sdk)
        self.store = SnapshotStore(Path(root)/'ecs-workspace')

    async def _start(self, value):
        sandbox = await super()._start(value)
        if value.get('workspace_restored_to') != sandbox.sandbox_id:
            snapshot = await run_file_operation(self.store.snapshot)
            payload = json.dumps(snapshot) + '\n'
            async with asyncio.timeout(90):
                handle = await sandbox.commands.run(remote_command('restore'), background=True,
                    stdin=True, user='root', cwd='/', timeout=90)
                await sandbox.commands.send_stdin(handle.pid, payload)
                result = await handle.wait()
            if result.exit_code != 0 or result.stdout.strip() != 'OAW_WORKSPACE_RESTORED':
                raise SandboxStateError('Cloud workspace restore failed; no user command was submitted')
            value['workspace_restored_to'] = sandbox.sandbox_id
            self._write(value)
        return sandbox

    async def _dispose(self, value):
        if value['remote_id'] and value['command'] is not None and not value['command'].get('workspace_saved'):
            # Collect before killing the VM. Failure retains the old ECS copy
            # and the cloud receipt instead of replaying the business command.
            sandbox = await self.sdk.connect(value['remote_id'], **self.settings.connection)
            await sandbox.set_timeout(self.settings.lifetime_seconds)
            async with asyncio.timeout(90):
                result = await sandbox.commands.run(remote_command('snapshot'), user='root', cwd='/', timeout=90)
            if result.exit_code != 0 or len(result.stdout) > MAX_JSON:
                raise SandboxStateError('Cloud workspace collection failed; inspect the retained remote binding')
            await run_file_operation(self.store.commit, json.loads(result.stdout))
            value['command']['workspace_saved'] = True
            self._write(value)
        await super()._dispose(value)
        value['workspace_restored_to'] = None
        self._write(value)

    async def file_operation(self, sandbox_id, operation, **options):
        if options.get('root', 'workspace') != 'workspace':
            raise SandboxValidationError('Only the private workspace root is available')
        if self.lock.locked():
            raise SandboxStateError('Remote workspace is busy')
        async with self.lock:
            value = self._read(sandbox_id)
            if value['command'] is not None or value['state'] in {'uncertain','creating'}:
                raise SandboxStateError('Cloud result must be reconciled before accessing workspace files')
            if operation not in {'list','stat','preview','download','read','find','search'}:
                value['workspace_restored_to'] = None
                self._write(value)
            return await run_file_operation(file_operation, self.store.current(), ResourceAccess.READ_WRITE,
                                            (), operation, **options)
