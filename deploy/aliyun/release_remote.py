"""Root-only release transaction for the existing native ECS installation.

Transport uses JSON on stdin; long transactions run in systemd, independent of SSH.
No account credentials, environment values or tenant contents are returned to stdout.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import tomllib
import traceback
from uuid import UUID, uuid4

UNITS = Path('/etc/systemd/system')
MAINTENANCE = Path('/opt/oaw-maintenance')

def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def relative(value):
    p = PurePosixPath(value)
    require(bool(value) and not p.is_absolute() and str(p) == value and '..' not in p.parts
            and not any(c in value for c in ('\\', ':', '\x00', '\n', '\r')),
            'Unsafe release member path')
    require(not set(p.parts).intersection({'.git', '.venv', 'node_modules'})
            and p.parts[0] not in {'data', 'logs', '.tmp', '.open-agent-world', '.oaw-hosting'}
            and p.name not in {'.env', '.oaw-deployment.json'}, 'Release contains mutable or private installation data')
    return p


def destination(root, name):
    relative(name)
    path = root / name
    require(path.resolve().is_relative_to(root) and not path.is_symlink(), 'Release path escapes installation')
    return path


def paths(request):
    result = []
    for key in ('app', 'hosting', 'releases'):
        value = request[key]
        p = Path(value)
        require(re.fullmatch(r'/(?:opt|srv|var)/[A-Za-z0-9_./-]+', value)
                and p.is_absolute() and '..' not in p.parts and str(p) == value
                and p.resolve() == p, 'Use dedicated real Linux installation paths')
        result.append(p)
    require(not any(a.is_relative_to(b) for a in result for b in result if a != b)
            and len(set(result)) == 3, 'Application, data and releases must be separate directories')
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,23}', request['service']), 'Invalid service name')
    return result


def release_dir(request):
    root = paths(request)[2]
    identifier = request['release']
    require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}-[a-f0-9]{8}', identifier), 'Invalid release ID')
    path = root / identifier
    require(path.resolve() == path, 'Release directory is a symlink')
    return path


def receipt(app):
    p = app / '.oaw-deployment.json'
    value = read(p)
    require(re.fullmatch(r'[a-f0-9]{40}', value['source_commit']), 'Invalid installed commit receipt')
    return value, digest(p)


def verify_files(root, files):
    require(isinstance(files, dict) and 0 < len(files) <= 20000, 'Invalid release inventory')
    for name, expected in files.items():
        path = destination(root, name)
        require(re.fullmatch(r'[a-f0-9]{64}', expected) and path.is_file() and digest(path) == expected,
                f'File integrity check failed: {name}')


def extract_bundle(archive, output):
    require(not output.exists(), 'Staging directory already exists')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        require(len(members) <= 20004 and sum(m.size for m in members) <= 2 * 1024**3,
                'Release archive exceeds limits')
        names = [m.name for m in members]
        require(len(names) == len(set(names)), 'Duplicate archive members')
        for member in members:
            relative(member.name)
            require(member.isfile() and member.size <= 1024**3, 'Archive contains a link or special file')
        manifest = json.load(tar.extractfile('release-manifest.json'))
        require(manifest.get('version') == 1 and re.fullmatch(r'[a-f0-9]{40}', manifest['source_commit'])
                and re.fullmatch(r'[a-f0-9]{64}', manifest['baseline_receipt']), 'Invalid release manifest')
        require(set(manifest['operator_files']) == {'operator/release_remote.py', 'operator/release_worker.py'},
                'Unexpected release operator files')
        expected = {**manifest['files'], **manifest['operator_files']}
        require(not (manifest['files'].keys() & manifest['operator_files'].keys()), 'Operator/source inventories overlap')
        require(set(names) == {*expected, 'release-manifest.json'}, 'Archive differs from declared inventory')
        tar.extractall(output, filter='data')
    verify_files(output, expected)
    return manifest


def profiles(hosting):
    rows = read(hosting / 'control/hosting.json')['profiles']
    for row in rows:
        identifier = str(UUID(row['profile_id']))
        require(identifier == row['profile_id'], 'Noncanonical profile ID')
        p = hosting / 'instances' / identifier / 'worker.json'
        require(p.resolve() == p, 'Profile configuration cannot traverse symlinks')
        value = read(p)
        raw = p.parent / value['data_root']
        root = raw.resolve()
        require(raw == root, 'Profile data cannot traverse symlinks or parent paths')
        require(root.is_relative_to(p.parent) and root != p.parent, 'Profile data escapes its instance')
        yield identifier, root


def plain_tree(root):
    require(not root.is_symlink(), 'Backup root cannot be a symlink')
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(parent) / name
            require(not p.is_symlink() and (p.is_dir() or p.is_file()) and (not p.is_file() or p.stat().st_nlink == 1),
                    'Backup contains links or special files')


def idle(hosting):
    with contextlib.closing(sqlite3.connect(f'file:{hosting}/control/deployments.sqlite3?mode=ro', uri=True)) as db:
        require(not db.execute("SELECT 1 FROM hosting_jobs WHERE status IN ('queued','preparing')").fetchone(),
                'Deployment jobs are pending; retry when idle')
    for identifier, root in profiles(hosting):
        plain_tree(root)
        databases = {root / 'database/world.sqlite3', *root.rglob('knowledge.db')}
        for path in databases:
            if not path.is_file():
                continue
            with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table, states in (('runs', ('created', 'running', 'waiting')),
                                      ('jobs', ('QUEUED', 'PENDING', 'RUNNING'))):
                    if table in tables:
                        busy = db.execute(f'SELECT 1 FROM {table} WHERE status IN (?,?,?)', states).fetchone()
                        require(not busy, f'Workspace {identifier} has active {table}; retry when idle')


def run(*command, log=None, timeout=600):
    if log:
        with Path(log).open('ab') as stream:
            result = subprocess.run(command, stdout=stream, stderr=stream, timeout=timeout)
        require(result.returncode == 0, f'Command failed; see private server log {log}')
        return ''
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f'{command[0]} failed (exit {result.returncode})')
    return result.stdout


@contextlib.contextmanager
def lock(root, *, wait=False):
    import fcntl
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / '.release.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        yield


def save(b, phase, **extra):
    state = read(b / 'state.json') if (b / 'state.json').exists() else {}
    state.update(phase=phase, updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **extra)
    write(b / 'state.json', state)
    return state


def plan(request):
    app, hosting, releases = paths(request)
    b = release_dir(request)
    manifest = read(b / 'stage/release-manifest.json')
    current, fingerprint = receipt(app)
    require(fingerprint == manifest['baseline_receipt'], 'Installed version changed; prepare a new release')
    verify_files(app, current['source_files'])
    verify_files(b / 'stage', {**manifest['files'], **manifest['operator_files']})
    for name in manifest['files'].keys() - current['source_files'].keys():
        p = destination(app, name)
        require(not p.exists() or digest(p) == manifest['files'][name], f'Unmanaged application file would be overwritten: {name}')
    for name in ('backend/uv.lock', 'backend/hosting/systemd.py'):
        require(current['source_files'].get(name) == manifest['files'].get(name),
                f'{name} changed; update the host environment/service definitions explicitly first')
    def requirements(root):
        project = tomllib.loads((root / 'backend/pyproject.toml').read_text())['project']
        return {k: project.get(k) for k in ('dependencies', 'optional-dependencies', 'requires-python')}
    require(requirements(app) == requirements(b / 'stage'), 'Host Python dependencies changed; explicit runtime upgrade required')
    idle(hosting)
    disk_needed = sum(p.stat().st_size for p in hosting.rglob('*') if p.is_file() and not p.is_symlink()) * 2 + 512 * 1024**2
    require(shutil.disk_usage(releases).free > disk_needed, 'Insufficient space for data backup and recovery')
    return {'release': request['release'], 'from_commit': current['source_commit'], 'to_commit': manifest['source_commit'],
            'profiles': len(list(profiles(hosting))), 'files': len(manifest['files']),
            'already_current': current['source_commit'] == manifest['source_commit'],
            'contracts': 'Refresh existing published Pack surfaces; retain core permissions and saved layouts'}


def worker_unit(original, app, helper, service):
    require('ProtectSystem=strict' in original and 'BindReadOnlyPaths=' in original and 'User=oaw-' in original,
            'Worker unit differs from the hardened hosted deployment')
    result = []
    for line in original.splitlines():
        if line.startswith(('Requires=', 'After=', 'RestartSec=', 'Sockets=', 'Type=', 'TimeoutStartSec=')):
            continue
        if line == '[Service]':
            result.extend([line, 'Type=oneshot', 'TimeoutStartSec=300'])
        elif line.startswith('ExecStart='):
            result.append(f'ExecStart={app}/backend/.venv/bin/python {helper} migrate --source {app} --config /var/lib/{service}-profile/worker.json')
        elif line.startswith('Restart='):
            result.append('Restart=no')
        else:
            result.append(line)
    return '\n'.join(result) + '\n'


def public_parents(path, root):
    """New code directories must be readable under the operator's private umask."""
    if path == root or path.exists():
        return
    public_parents(path.parent, root)
    path.mkdir()
    path.chmod(0o755)


def stop(request):
    _, hosting, _ = paths(request)
    name = request['service']
    units = [name + '-gateway.service', *[f'{name}-worker-{p}{suffix}' for p, _ in profiles(hosting)
                                         for suffix in ('.service', '.socket')]]
    run('systemctl', 'stop', name + '-provisioner.timer', name + '-provisioner.service')
    run('systemctl', 'stop', name + '.target', *units)


def resume(request, *, gateway=True):
    _, hosting, _ = paths(request)
    name = request['service']
    # Socket activation avoids restarting every historical workspace concurrently.
    run('systemctl', 'start', *[f'{name}-worker-{p}.socket' for p, _ in profiles(hosting)])
    if gateway:
        run('systemctl', 'start', name + '-gateway.service', name + '-provisioner.timer')


def backup(request, b, manifest):
    app, hosting, _ = paths(request)
    for name in ('control', 'instances'):
        plain_tree(hosting / name)
    with tarfile.open(b / 'data.tar.gz', 'w:gz', compresslevel=3) as tar:
        for name in ('control', 'instances'):
            tar.add(hosting / name, arcname=name)
    old, _ = receipt(app)
    names = {*manifest['files'], *old['source_files'], '.oaw-deployment.json'}
    with tarfile.open(b / 'application.tar.gz', 'w:gz', compresslevel=3) as tar:
        for name in sorted(names):
            if (app / name).is_file():
                tar.add(app / name, arcname=name, recursive=False)
    write(b / 'backup.json', {name: digest(b / name) for name in ('data.tar.gz', 'application.tar.gz')})


def restore(request, b):
    app, hosting, _ = paths(request)
    hashes = read(b / 'backup.json')
    require(all(digest(b / name) == value for name, value in hashes.items()), 'Backup integrity check failed')
    manifest = read(b / 'stage/release-manifest.json')
    with tarfile.open(b / 'application.tar.gz') as tar:
        names = {m.name for m in tar.getmembers()}
        for name in manifest['files'].keys() - names:
            destination(app, name).unlink(missing_ok=True)
        # Root-created and hash-verified backup; restore numeric ownership/modes.
        tar.extractall(app, filter='fully_trusted')
    after = b / ('replaced-data-' + str(time.time_ns()))
    after.mkdir(mode=0o700)
    for name in ('control', 'instances'):
        shutil.move(str(hosting / name), str(after / name))
    with tarfile.open(b / 'data.tar.gz') as tar:
        tar.extractall(hosting, filter='fully_trusted')
    previous, _ = receipt(app)
    verify_files(app, previous['source_files'])


def transaction(request, *, rollback=False):
    app, hosting, releases = paths(request)
    b = release_dir(request)
    helpers = []
    helper = MAINTENANCE / (request['release'] + '-worker.py')
    changed = stopped = False
    with lock(releases, wait=True):
        try:
            if rollback:
                current, fingerprint = receipt(app)
                manifest = read(b / 'stage/release-manifest.json')
                interrupted = read(b / 'state.json')['phase'] in {'stopped', 'backed-up', 'installing', 'migrating',
                                                               'verifying', 'rolling-back', 'recovery-required'}
                require(current.get('release_id') == request['release'] or (interrupted and fingerprint == manifest['baseline_receipt']),
                        'Only the currently installed release can be rolled back')
                idle(hosting)
                stopped = True
                stop(request)
                for name in read(b / 'state.json').get('helpers', []):
                    require(re.fullmatch(r'oaw-release-[a-f0-9]{8}-[a-f0-9-]{36}\.service', name), 'Invalid recovery unit')
                    if (UNITS / name).exists():
                        helpers.append(name)
                        run('systemctl', 'stop', name)
                save(b, 'rolling-back')
                restore(request, b)
                resume(request)
                return save(b, 'rolled-back')
            plan(request)
            stopped = True
            stop(request)
            save(b, 'stopped')
            idle(hosting)
            manifest = read(b / 'stage/release-manifest.json')
            backup(request, b, manifest)
            save(b, 'backed-up')
            old, _ = receipt(app)
            changed = True
            save(b, 'installing')
            for name in sorted(manifest['files'], key=lambda p: p == 'frontend/dist/index.html'):
                target = destination(app, name)
                public_parents(target.parent, app)
                shutil.copyfile(b / 'stage' / name, target)
                target.chmod(0o755 if (b / 'stage' / name).stat().st_mode & 0o111 else 0o644)
            for name in old['source_files'].keys() - manifest['files'].keys():
                if not name.startswith('frontend/dist/assets/'):
                    destination(app, name).unlink(missing_ok=True)
            helper.parent.mkdir(mode=0o755, exist_ok=True)
            helper.parent.chmod(0o755)
            shutil.copyfile(b / 'stage/operator/release_worker.py', helper)
            helper.chmod(0o644)
            units = UNITS
            for identifier, _ in profiles(hosting):
                original = (units / f"{request['service']}-worker-{identifier}.service").read_text()
                name = f'oaw-release-{request["release"][-8:]}-{identifier}.service'
                target = units / name
                require(not target.exists(), 'Migration unit already exists')
                target.write_text(worker_unit(original, app, helper, request['service']))
                target.chmod(0o644)
                helpers.append(name)
            save(b, 'migrating', helpers=helpers)
            run('systemctl', 'daemon-reload')
            for index, name in enumerate(helpers):
                try:
                    run('systemctl', 'start', name, log=b / 'migration.log', timeout=330)
                finally:
                    with (b / 'migration.log').open('a') as stream:
                        stream.write(run('journalctl', '-u', name, '--no-pager', '-o', 'cat'))
                save(b, 'migrating', migrated_profiles=index + 1)
            verify_files(app, manifest['files'])
            resume(request, gateway=False)
            save(b, 'verifying')
            run(str(app / 'backend/.venv/bin/python'), str(helper), 'verify', '--source', str(app), '--hosting', str(hosting),
                log=b / 'verification.log', timeout=3600)
            new = {'source_commit': manifest['source_commit'], 'source_files': manifest['files'],
                   'release_id': request['release'], 'previous_source_commit': old['source_commit'], 'backup': str(b)}
            write(app / '.oaw-deployment.json', new)
            resume(request)
            run('systemctl', 'is-active', '--quiet', request['service'] + '-gateway.service')
            return save(b, 'complete', commit=manifest['source_commit'], verification='worker bootstrap and authorization checks passed')
        except BaseException as error:
            with (b / 'failure.log').open('a') as stream:
                traceback.print_exc(file=stream)
            if changed:
                try:
                    for name in helpers:
                        run('systemctl', 'stop', name)
                    stop(request)
                    save(b, 'rolling-back')
                    restore(request, b)
                    resume(request)
                    return save(b, 'failed-rolled-back', error='Deployment failed; previous code and data restored', log=str(b / 'failure.log'))
                except BaseException:
                    with (b / 'failure.log').open('a') as stream:
                        traceback.print_exc(file=stream)
                    return save(b, 'recovery-required', error='Automatic recovery failed; services remain stopped', log=str(b / 'failure.log'))
            if not rollback and stopped:
                resume(request)
            if rollback and stopped:
                return save(b, 'recovery-required', error='Recovery failed; services remain stopped', log=str(b / 'failure.log'))
            return save(b, 'failed', error=str(error), log=str(b / 'failure.log'))
        finally:
            for name in helpers:
                subprocess.run(['systemctl', 'stop', name], capture_output=True, timeout=60)
                (UNITS / name).unlink(missing_ok=True)
            if helpers:
                run('systemctl', 'daemon-reload')
            helper.unlink(missing_ok=True)


def dispatch(request):
    app, hosting, releases = paths(request)
    action = request['action']
    if action == 'status' and not request.get('release'):
        value, fingerprint = receipt(app)
        return {'commit': value['source_commit'], 'receipt': fingerprint, 'release': value.get('release_id'),
                'profiles': len(list(profiles(hosting)))}
    b = release_dir(request)
    if action == 'init':
        releases.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not b.exists():
            b.mkdir(mode=0o700)
            write(b / 'request.json', request)
            return save(b, 'uploading', release=request['release'])
    pinned = read(b / 'request.json')
    require(all(request[k] == pinned[k] for k in ('app', 'hosting', 'releases', 'service', 'release')),
            'Release target differs from its staged request')
    if action == 'init':
        require(request['archive_sha256'] == pinned['archive_sha256'], 'Release ID belongs to another archive')
        return read(b / 'state.json')
    if action == 'stage':
        require(read(b / 'state.json')['phase'] == 'uploading', 'Release is not awaiting an upload')
        require(request['archive_sha256'] == pinned['archive_sha256'], 'Release upload differs from its pinned archive')
        require(digest(b / 'bundle.tar.gz') == request['archive_sha256'], 'Uploaded archive checksum differs')
        if not (b / 'stage').exists():
            temporary = b / ('unpack-' + uuid4().hex)
            extract_bundle(b / 'bundle.tar.gz', temporary)
            temporary.replace(b / 'stage')
        result = plan(request)
        return save(b, 'staged', plan=result)
    if action == 'check':
        with lock(releases):
            require(read(b / 'state.json')['phase'] in {'staged', 'ready'}, 'Release is not ready for checks')
            result = plan(request)
            helper = b / 'stage/operator/release_worker.py'
            env = os.environ.copy()
            os.environ['PATH'] = '/opt/oaw-tools/bin:' + env.get('PATH', '')
            try:
                run(str(app / 'backend/.venv/bin/python'), str(helper), 'prepare', '--source', str(b / 'stage'),
                    '--previous', str(app), '--scratch', str(b / 'dependencies'), log=b / 'prepare.log', timeout=3600)
            finally:
                os.environ['PATH'] = env.get('PATH', '')
            return save(b, 'ready', plan=result)
    if action == 'status':
        state = read(b / 'state.json')
        unit = f'oaw-release-{request["release"]}.service'
        active = unit_state(unit)
        state['unit_active'] = active in {'active', 'activating', 'deactivating'}
        return state
    if action in {'start', 'rollback'}:
        with lock(releases):
            return start_job(request)
    if action == 'transaction':
        return transaction(request, rollback=request.get('rollback', False))
    raise ValueError('Unknown release operation')


def unit_state(unit):
    result = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'],
                            capture_output=True, text=True, timeout=30)
    require(result.returncode == 0 or result.stdout.strip() == 'inactive', 'Cannot query release task state')
    return result.stdout.strip()


def start_job(request):
    b = release_dir(request)
    action = request['action']
    unit = 'oaw-release-' + request['release']
    active = unit_state(unit + '.service')
    require(active not in {'active', 'activating', 'deactivating'}, 'Release task is already running; use status')
    state = read(b / 'state.json')
    if action == 'start':
        require(state['phase'] == 'ready', 'Run deployment checks first')
        result = plan(request)
        if result['already_current']:
            return save(b, 'already-current', commit=result['to_commit'])
    else:
        require(request.get('restore_data') is True, 'Rollback restores a data snapshot; pass --restore-data explicitly')
        require((b / 'backup.json').exists(), 'This release has no rollback backup')
    request = {**request, 'action': 'transaction', 'rollback': action == 'rollback'}
    write(b / 'transaction.json', request)
    # The caller still owns the submission lock. Waiting for oneshot startup here
    # deadlocks with transaction(), which must acquire that lock before proceeding.
    run('systemd-run', '--no-block', '--unit=' + unit, '--collect', '--property=Type=oneshot', '--property=UMask=0077',
        '--property=TimeoutStartSec=infinity', sys.executable, str(b / 'stage/operator/release_remote.py'),
        str(b / 'transaction.json'))
    return {'phase': 'queued', 'release': request['release']}


if __name__ == '__main__':
    try:
        request = read(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
        require(os.geteuid() == 0, 'Release operations require the deployment operator root account')
        print(json.dumps(dispatch(request)), flush=True)
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        sys.exit(1)
