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
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
import traceback
from uuid import UUID, uuid4

UNITS = Path('/etc/systemd/system')
OPERATOR = (Path('/opt/oaw-release-operator-preview')
            if Path(__file__).absolute().parent == Path('/opt/oaw-release-operator-preview')
            else Path('/opt/oaw-release-operator'))
TARGET_KEYS = ('app', 'hosting', 'releases', 'service')
PROTOCOL_VERSION = 2

def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def trusted_metadata(metadata, *, directory=False, private=False):
    require(metadata.st_uid == 0 and not metadata.st_mode & 0o022,
            'Deployment operator files must be root-owned and not writable by other users')
    require(stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode),
            'Deployment operator paths must be real directories or regular files')
    if not directory:
        require(metadata.st_nlink == 1, 'Deployment operator files cannot be hard links')
    if private:
        require(not metadata.st_mode & 0o077, 'Deployment trust policy must be private to root')


def trusted_file(path, *, private=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path,
            'Deployment operator paths cannot traverse links')
    trusted_metadata(path.lstat(), private=private)
    for parent in path.parents:
        trusted_metadata(parent.lstat(), directory=True)
    return path


def trusted_operator():
    path = OPERATOR / 'release_remote.py'
    require(Path(__file__).absolute() == path,
            'Use the independently installed deployment operator')
    return trusted_file(path)


def trusted_worker():
    return trusted_file(OPERATOR / 'release_worker.py')


def trusted_verifier():
    return trusted_file(OPERATOR / 'release_verify.py')


def execution_config(request):
    """This installation registration is independent of artifacts and requests."""
    value = read(trusted_file(OPERATOR / 'execution.json', private=True))
    require(isinstance(value, dict) and type(value.get('version')) is int and value['version'] == 1
            and set(value) == {'version', 'environment', 'target', 'sandbox_root', 'python', 'build_user', 'verify_user'}
            and value.get('environment') in {'production', 'preview'}, 'Invalid operator execution registration')
    require(value['target'] == {key: request[key] for key in TARGET_KEYS},
            'Execution registration belongs to another installation')
    for key in ('build_user', 'verify_user'):
        require(isinstance(value[key], str) and re.fullmatch(r'oaw-[a-z0-9-]{1,27}', value[key]),
                'Release execution requires dedicated nonroot identities')
    require(value['build_user'] != value['verify_user'], 'Build and verification identities must differ')
    root = Path(value['sandbox_root'])
    require(re.fullmatch(r'/(?:opt|srv)/[A-Za-z0-9_./-]+', str(root)) and root.resolve(strict=True) == root
            and '..' not in root.parts, 'Use an independently prepared real release sandbox root')
    for path in (root, *root.parents):
        trusted_metadata(path.lstat(), directory=True)
    require(not any(root.is_relative_to(path) or path.is_relative_to(root)
                    for path in (*paths(request), OPERATOR)), 'Release sandbox must be outside installation and operator paths')
    python = PurePosixPath(value['python'])
    require(re.fullmatch(r'/[A-Za-z0-9_./-]+', str(python)) and str(python) == value['python']
            and '..' not in python.parts, 'Invalid sandbox Python path')
    trusted_file(root / str(python).lstrip('/'))
    import pwd
    accounts = [pwd.getpwnam(value[key]) for key in ('build_user', 'verify_user')]
    require(all(account.pw_uid > 0 and account.pw_gid > 0 for account in accounts)
            and accounts[0].pw_uid != accounts[1].pw_uid
            and accounts[0].pw_gid != accounts[1].pw_gid,
            'Build and verification identities must have distinct nonroot UIDs and groups')
    return value


def require_execution(request, *, deploying=False):
    config = execution_config(request)
    if deploying:
        # The current production provisioner imports the application as root.
        # A digest or a boolean approval does not close that transitive boundary.
        require(config['environment'] == 'preview',
                'Production deployment is blocked until the root provisioner and its complete import closure '
                'are replaced by an independently installed trusted broker')
        for suffix in ('.timer', '.service'):
            unit = request['service'] + '-provisioner' + suffix
            state = run('systemctl', 'show', unit,
                        '--property=LoadState', '--value').strip()
            require(state in {'masked', 'not-found'}, 'Preview application provisioner must be absent or masked')
            active = run('systemctl', 'show', unit, '--property=ActiveState', '--value').strip()
            require(active in {'inactive', 'failed'}, 'Preview application provisioner must not be running')
    return config


def sandbox_command(request, config, mode, *, writable=None, readonly=()):
    """Root submits a fixed systemd boundary; only the child executes application code."""
    require(mode in {'prepare', 'verify'}, 'Unsupported release sandbox operation')
    user = config['build_user' if mode == 'prepare' else 'verify_user']
    unit = f'oaw-release-{request["service"]}-{request["release"][-8:]}-{mode}'
    helper = trusted_worker() if mode == 'prepare' else trusted_verifier()
    properties = ['Type=oneshot', f'User={user}', f'Group={user}', f'RootDirectory={config["sandbox_root"]}',
        'WorkingDirectory=/workspace', 'MountAPIVFS=yes', 'ProtectSystem=strict', 'ProtectHome=yes',
        'PrivateTmp=yes', 'PrivateDevices=yes', 'PrivateNetwork=yes', 'PrivateIPC=yes',
        'NoNewPrivileges=yes', 'CapabilityBoundingSet=', 'AmbientCapabilities=', 'RestrictSUIDSGID=yes',
        'ProtectKernelTunables=yes', 'ProtectKernelModules=yes', 'ProtectControlGroups=yes',
        'ProtectProc=invisible', 'ProcSubset=pid', 'LockPersonality=yes', 'RestrictAddressFamilies=AF_UNIX',
        'UMask=0077', 'MemoryMax=' + ('1G' if mode == 'prepare' else '256M'), 'MemorySwapMax=0',
        'CPUQuota=100%', 'TasksMax=128', 'KillMode=control-group', 'TimeoutStartSec=3600',
        'Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp PATH=/usr/local/bin:/usr/bin:/bin',
        f'BindReadOnlyPaths={helper}:/workspace/{helper.name}']
    properties.extend(f'BindReadOnlyPaths={source}:{target}' for source, target in readonly)
    if writable:
        properties.extend([f'BindPaths={writable}:/workspace/scratch', 'ReadWritePaths=/workspace/scratch'])
    command = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--unit=' + unit,
               *['--property=' + value for value in properties], config['python'], '-I',
               *(['-S'] if mode == 'verify' else []),
               '/workspace/' + helper.name, *(['prepare'] if mode == 'prepare' else [])]
    return unit + '.service', command


def run_sandbox(request, config, mode, arguments, *, writable=None, readonly=(), log):
    unit, command = sandbox_command(request, config, mode, writable=writable, readonly=readonly)
    try:
        run(*command, *arguments, log=log, timeout=3630)
    finally:
        # Wait for cgroup teardown before root inspects or promotes writable output.
        run('systemctl', 'stop', unit, timeout=60)


def bundle_tree(root, *, immutable=False):
    require(root.is_absolute() and root.resolve(strict=True) == root, 'Runtime bundle cannot traverse links')
    plain_tree(root)
    result, size = [], 0
    for parent, directories, files in os.walk(root, followlinks=False):
        for path in [Path(parent), *[Path(parent) / name for name in files]]:
            metadata = path.lstat()
            if immutable:
                trusted_metadata(metadata, directory=path.is_dir())
            size += metadata.st_size if path.is_file() else 0
            result.append(path)
            require(len(result) <= 200000 and size <= 4 * 1024**3, 'Prepared runtime bundle exceeds limits')
    return result


def prepare_bundle(request, config):
    import pwd
    app, _, _ = paths(request)
    b = release_dir(request)
    current = app / 'backend/.venv/oaw-runtime-bundle'
    require(current.is_dir(), 'Prepare and review the initial immutable runtime bundle before release checks')
    bundle_tree(current, immutable=True)
    scratch = b / ('prepare-' + uuid4().hex)
    scratch.mkdir(mode=0o700)
    bundle = scratch / 'bundle'
    shutil.copytree(current, bundle)
    account = pwd.getpwnam(config['build_user'])
    for path in [scratch, *bundle_tree(bundle)]:
        os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
        path.chmod(0o700 if path.is_dir() else 0o600 | (path.stat().st_mode & 0o100))
    # Previous declarations are sufficient for offline wheel reuse. Never expose
    # the previous application's environment files, data or site-packages.
    previous = b / ('previous-' + uuid4().hex)
    previous.mkdir(mode=0o755)
    for declaration in sorted((app / 'plugins').glob('*/oaw.pack.toml')):
        target = previous / declaration.relative_to(app)
        public_parents(target.parent, previous)
        shutil.copyfile(declaration, target)
        target.chmod(0o644)
    try:
        run_sandbox(request, config, 'prepare', ['--source', '/workspace/source', '--previous', '/workspace/previous',
            '--scratch', '/workspace/scratch', '--bundle', '/workspace/scratch/bundle',
            '--environment', config['environment']], writable=scratch,
            readonly=((b / 'stage', '/workspace/source'), (previous, '/workspace/previous')), log=b / 'prepare.log')
        tree = bundle_tree(bundle)
        for path in tree:
            os.chown(path, 0, 0, follow_symlinks=False)
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
        prepared = b / 'prepared-runtime-bundle'
        if prepared.exists():
            bundle_tree(prepared, immutable=True)
            shutil.rmtree(prepared)
        bundle.replace(prepared)
    finally:
        # A rejected child output may contain arbitrary links: unlink rather
        # than traverse them. shutil.rmtree does not follow directory symlinks.
        shutil.rmtree(scratch)
        shutil.rmtree(previous)


def install_bundle(request, b):
    bundle = paths(request)[0] / 'backend/.venv/oaw-runtime-bundle'
    prepared = b / 'prepared-runtime-bundle'
    require(prepared.is_dir(), 'Run isolated runtime preparation before deployment')
    bundle_tree(prepared, immutable=True)
    previous = b / 'previous-runtime-bundle'
    require(not previous.exists(), 'Release already has a runtime bundle backup')
    inventory = bundle_inventory(bundle)
    authority = read(b / 'backup.json')
    require('runtime-bundle.json' not in authority, 'Release already has a runtime bundle inventory')
    write(b / 'runtime-bundle.json', inventory)
    write(b / 'backup.json', {**authority, 'runtime-bundle.json': digest(b / 'runtime-bundle.json')})
    bundle.replace(previous)
    prepared.replace(bundle)


def bundle_inventory(root):
    result = {}
    for path in bundle_tree(root, immutable=True):
        metadata = path.lstat()
        result[path.relative_to(root).as_posix()] = {'mode': stat.S_IMODE(metadata.st_mode),
            **({'sha256': digest(path)} if path.is_file() else {'directory': True})}
    return result


def restore_bundle(request, b):
    previous = b / 'previous-runtime-bundle'
    if previous.exists():
        authority = read(b / 'backup.json')
        require('runtime-bundle.json' in authority
                and digest(b / 'runtime-bundle.json') == authority['runtime-bundle.json'],
                'Runtime bundle backup inventory integrity check failed')
        require(bundle_inventory(previous) == read(b / 'runtime-bundle.json'),
                'Runtime bundle backup integrity check failed')
        bundle = paths(request)[0] / 'backend/.venv/oaw-runtime-bundle'
        if bundle.exists():
            bundle_tree(bundle, immutable=True)
            bundle.replace(b / ('replaced-runtime-bundle-' + str(time.time_ns())))
        previous.replace(bundle)


def seed(request):
    """Install the first approved Preview source into a runtime-only directory."""
    config = require_execution(request, deploying=True)
    require(config['environment'] == 'preview', 'Initial source seeding is supported only for Preview')
    app, _, _ = paths(request)
    b = release_dir(request)
    require(read(b / 'state.json')['phase'] == 'uploading', 'Initial seed is not awaiting an approved upload')
    pinned = read(b / 'request.json')
    approval = authorize(pinned, archive_sha256=pinned['archive_sha256'], source_commit=pinned.get('source_commit'))
    require(digest(b / 'bundle.tar.gz') == approval['archive_sha256'], 'Uploaded archive checksum differs')
    backend, runtime = app / 'backend', app / 'backend/.venv'
    require(app.is_dir() and backend.is_dir() and runtime.is_dir()
            and set(app.iterdir()) == {backend} and set(backend.iterdir()) == {runtime},
            'Initial Preview application must contain only its separately prepared backend/.venv runtime')
    for directory in (app, backend, *app.parents):
        require(directory.resolve(strict=True) == directory, 'Initial Preview application cannot traverse links')
        trusted_metadata(directory.lstat(), directory=True)
    bundle_tree(runtime, immutable=True)
    require((runtime / 'bin/python').is_file() and (runtime / 'bin/python').stat().st_mode & 0o111
            and (runtime / 'oaw-runtime-bundle').is_dir(),
            'Prepare the independent immutable Python and runtime bundle before initial seeding')
    stage = b / 'stage'
    manifest = extract_bundle(b / 'bundle.tar.gz', stage, source_commit=approval['source_commit'])
    require(manifest['version'] == 2, 'Initial seeding requires a portable version-two build')
    save(b, 'seed-installing')
    for name in sorted(manifest['files']):
        target = destination(app, name)
        require(not target.exists(), 'Initial seeding cannot overwrite application files')
        public_parents(target.parent, app)
        shutil.copyfile(stage / name, target)
        target.chmod(0o755 if (stage / name).stat().st_mode & 0o111 else 0o644)
    verify_files(app, manifest['files'])
    write(app / '.oaw-deployment.json', {'source_commit': manifest['source_commit'], 'source_files': manifest['files'],
        'release_id': request['release'], 'archive_sha256': approval['archive_sha256'], 'environment': 'preview',
        'operator_protocol': PROTOCOL_VERSION, 'operator_sha256': digest(trusted_operator()), 'seed': True})
    return save(b, 'seeded', commit=manifest['source_commit'])


def verify_release(request, config, *, entries_only=False):
    import pwd
    _, hosting, _ = paths(request)
    b = release_dir(request)
    rows = read(hosting / 'control/hosting.json')['profiles']
    require(isinstance(rows, list) and 0 < len(rows) <= 5000, 'Invalid verification profile inventory')
    choices = {}
    if entries_only:
        with contextlib.closing(sqlite3.connect(f'file:{hosting}/control/deployments.sqlite3?mode=ro', uri=True)) as db:
            choices = dict(db.execute('SELECT user_id,profile_id FROM hosting_entries'))
    selected = {}
    for row in rows:
        for key in ('profile_id', 'user_id'):
            require(str(UUID(row[key])) == row[key], 'Invalid verification identity')
        require(row['mode'] in {'builder', 'runtime'} and re.fullmatch(r'(?:[0-9a-fA-F]{2}){32,64}', row['secret'])
                and row['upstream'] == f'unix:///run/{request["service"]}/{row["profile_id"]}.sock',
                'Release verification requires exact installation-owned private sockets')
        if not entries_only:
            selected[row['profile_id']] = row
        elif row['user_id'] not in choices:
            selected.setdefault(row['user_id'], row)
        elif choices[row['user_id']] == row['profile_id']:
            selected[row['user_id']] = row
    require(selected, 'No login entries available for release verification')
    snapshot = b / 'verification-input.json'
    write(snapshot, {'profiles': [{key: row[key] for key in ('profile_id', 'user_id', 'mode', 'secret')}
                                 for row in selected.values()]})
    account = pwd.getpwnam(config['verify_user'])
    os.chown(snapshot, 0, account.pw_gid, follow_symlinks=False)
    snapshot.chmod(0o640)
    try:
        run_sandbox(request, config, 'verify', ['--snapshot', '/workspace/verification.json'],
            readonly=((snapshot, '/workspace/verification.json'),
                      (Path('/run') / request['service'], '/workspace/sockets')), log=b / 'verification.log')
    finally:
        snapshot.unlink()


def authorize(request, *, archive_sha256=None, source_commit=None):
    """Approval is operator-owned state, never a claim in a downloaded bundle."""
    require(request.get('purpose', 'trusted_manual') == 'trusted_manual',
            'Only trusted_manual deployment is supported; untrusted Preview is not implemented')
    policy = read(trusted_file(OPERATOR / 'trusted-artifacts.json', private=True))
    require(isinstance(policy, dict) and type(policy.get('version')) is int and policy['version'] == 1
            and policy.get('purpose') == 'trusted_manual', 'Invalid deployment trust policy')
    target = policy.get('target')
    require(isinstance(target, dict) and set(target) == set(TARGET_KEYS)
            and all(request.get(key) == target[key] for key in TARGET_KEYS),
            'Deployment target is not approved by the operator policy')
    approved = policy.get('approved_artifacts')
    require(isinstance(approved, list) and all(isinstance(item, dict)
            and set(item) == {'archive_sha256', 'source_commit'}
            and isinstance(item['archive_sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', item['archive_sha256'])
            and isinstance(item['source_commit'], str) and re.fullmatch(r'[a-f0-9]{40}', item['source_commit'])
            for item in approved), 'Invalid deployment artifact approvals')
    if archive_sha256 is None:
        require(source_commit is None, 'Artifact approval requires its archive digest')
        return None
    require(isinstance(archive_sha256, str) and re.fullmatch(r'[a-f0-9]{64}', archive_sha256),
            'Invalid deployment archive digest')
    matches = {item['source_commit'] for item in approved if item['archive_sha256'] == archive_sha256}
    require(len(matches) == 1, 'Deployment artifact has not been approved by the operator')
    commit = matches.pop()
    require(source_commit is None or source_commit == commit,
            'Deployment artifact commit differs from the operator approval')
    return {'archive_sha256': archive_sha256, 'source_commit': commit}


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
    require(not any(p.is_relative_to(OPERATOR) or OPERATOR.is_relative_to(p) for p in result),
            'Deployment operator must be outside application, hosting and release directories')
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


def payload_files(manifest):
    require(isinstance(manifest, dict) and type(manifest.get('version')) is int
            and manifest['version'] in {1, 2}
            and isinstance(manifest.get('source_commit'), str)
            and re.fullmatch(r'[a-f0-9]{40}', manifest['source_commit']), 'Invalid release manifest')
    files = manifest.get('files')
    require(isinstance(files, dict) and 0 < len(files) <= 20000, 'Invalid release inventory')
    require(all(isinstance(name, str) for name in files), 'Invalid release member name')
    require(type(manifest.get('routine', False)) is bool, 'Invalid routine release flag')
    if manifest['version'] == 1:
        require(isinstance(manifest.get('baseline_receipt'), str)
                and re.fullmatch(r'[a-f0-9]{64}', manifest['baseline_receipt']), 'Invalid release baseline')
        operators = manifest.get('operator_files')
        require(isinstance(operators, dict)
                and set(operators) == {'operator/release_remote.py', 'operator/release_worker.py'},
                'Unexpected release operator files')
        require(not (files.keys() & operators.keys()), 'Operator/source inventories overlap')
    else:
        require(manifest.get('kind') == 'oaw-build'
                and isinstance(manifest.get('comparison_commit'), str)
                and re.fullmatch(r'[a-f0-9]{40}', manifest['comparison_commit'])
                and type(manifest.get('routine')) is bool
                and not set(manifest).intersection({'target', 'baseline_receipt', 'baseline_commit', 'operator_files'}),
                'Invalid portable build manifest')
        operators = {}
        require(not any(PurePosixPath(name).parts[:1] == ('operator',) for name in files),
                'Portable builds cannot contain operator files')
    expected = {**files, **operators}
    for name, expected_digest in expected.items():
        require(isinstance(name, str), 'Invalid release member name')
        relative(name)
        require(name != 'release-manifest.json' and isinstance(expected_digest, str)
                and re.fullmatch(r'[a-f0-9]{64}', expected_digest), 'Invalid release member digest')
    return expected


def verify_payload(root, manifest):
    verify_files(root, payload_files(manifest))


def baseline(request, manifest):
    if manifest['version'] == 1:
        return manifest.get('baseline_commit'), manifest['baseline_receipt']
    require(isinstance(request.get('baseline_commit'), str)
            and re.fullmatch(r'[a-f0-9]{40}', request['baseline_commit'])
            and isinstance(request.get('baseline_receipt'), str)
            and re.fullmatch(r'[a-f0-9]{64}', request['baseline_receipt']),
            'Portable build deployment requires a pinned installation baseline')
    return request['baseline_commit'], request['baseline_receipt']


def approved_release(request, manifest):
    authorize(request)
    pinned = read(release_dir(request) / 'request.json')
    require(all(request.get(key) == pinned.get(key) for key in (*TARGET_KEYS, 'release')),
            'Release target differs from its staged request')
    authorize(pinned, archive_sha256=pinned['archive_sha256'], source_commit=manifest['source_commit'])
    require(pinned.get('source_commit', manifest['source_commit']) == manifest['source_commit'],
            'Release source differs from its staged request')
    if 'manifest_sha256' in pinned:
        require(digest(release_dir(request) / 'stage/release-manifest.json') == pinned['manifest_sha256'],
                'Staged release manifest changed')
    return pinned


def extract_bundle(archive, output, *, source_commit=None, request=None):
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
        require('release-manifest.json' in names
                and tar.getmember('release-manifest.json').size <= 8 * 1024**2, 'Invalid release manifest size')
        manifest = json.load(tar.extractfile('release-manifest.json'))
        expected = payload_files(manifest)
        require(source_commit is None or manifest['source_commit'] == source_commit,
                'Archive source differs from its operator approval')
        if request is not None:
            baseline(request, manifest)
        require(set(names) == {*expected, 'release-manifest.json'}, 'Archive differs from declared inventory')
        for name, expected_digest in expected.items():
            with tar.extractfile(name) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected_digest,
                        f'File integrity check failed: {name}')
        tar.extractall(output, filter='data')
    verify_payload(output, manifest)
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


def routine_guard(changed):
    """Catch known incompatible changes; the operator must also review handler diffs."""
    for name in changed:
        if name.startswith(('backend/tests/', 'frontend/', 'docs/', 'scripts/', 'deploy/', '.github/')):
            continue
        p = PurePosixPath(name)
        if name.startswith('plugins/'):
            if '/frontend/' in name or '/tests/' in name or p.suffix in {'.md', '.css', '.svg', '.png'}:
                continue
            if (p.suffix == '.py' and p.name != '__init__.py'
                    and not re.search(r'plugin|lifecycle|schema|migrat|database|client|preset|model|operation', p.stem)):
                continue
        require(False, f'Routine release cannot change {name}; use the full release path')


def locked_dependencies(root):
    """Ignore only the virtual root project's version, never resolved dependencies."""
    path = root / 'backend/uv.lock'
    if not path.exists():
        return None
    value = tomllib.loads(path.read_text(encoding='utf-8'))
    project = tomllib.loads((root / 'backend/pyproject.toml').read_text(encoding='utf-8'))['project']
    for package in value.get('package', []):
        if package.get('name') == project.get('name') and package.get('source') == {'virtual': '.'}:
            require(package.get('version') == project.get('version'), 'Root package version differs from its lockfile')
            package.pop('version')
    return value


def plan(request):
    app, hosting, releases = paths(request)
    b = release_dir(request)
    manifest = read(b / 'stage/release-manifest.json')
    payload_files(manifest)
    pinned = approved_release(request, manifest)
    baseline_commit, baseline_receipt = baseline(pinned, manifest)
    current, fingerprint = receipt(app)
    require(fingerprint == baseline_receipt
            and (baseline_commit is None or baseline_commit == current['source_commit']),
            'Installed version changed; prepare a new release')
    verify_files(app, current['source_files'])
    verify_payload(b / 'stage', manifest)
    if manifest.get('routine'):
        routine_guard({n for n in current['source_files'].keys() | manifest['files'].keys()
                       if current['source_files'].get(n) != manifest['files'].get(n)})
    for name in manifest['files'].keys() - current['source_files'].keys():
        p = destination(app, name)
        require(not p.exists() or digest(p) == manifest['files'][name], f'Unmanaged application file would be overwritten: {name}')
    for name in ('backend/hosting/systemd.py',):
        require(current['source_files'].get(name) == manifest['files'].get(name),
                f'{name} changed; update the host environment/service definitions explicitly first')
    require(locked_dependencies(app) == locked_dependencies(b / 'stage'),
            'backend/uv.lock dependencies changed; update the host environment explicitly first')
    def requirements(root):
        project = tomllib.loads((root / 'backend/pyproject.toml').read_text())['project']
        return {k: project.get(k) for k in ('dependencies', 'optional-dependencies', 'requires-python')}
    require(requirements(app) == requirements(b / 'stage'), 'Host Python dependencies changed; explicit runtime upgrade required')
    idle(hosting)
    disk_needed = sum(p.stat().st_size for p in hosting.rglob('*') if p.is_file() and not p.is_symlink()) * 2 + 512 * 1024**2
    require(shutil.disk_usage(releases).free > disk_needed, 'Insufficient space for data backup and recovery')
    return {'release': request['release'], 'from_commit': current['source_commit'], 'to_commit': manifest['source_commit'],
            'profiles': len(list(profiles(hosting))), 'files': len(manifest['files']),
            'routine': bool(manifest.get('routine')), 'offline_migration': not manifest.get('routine', False),
            'already_current': current['source_commit'] == manifest['source_commit'],
            'contracts': 'Refresh existing published Pack surfaces; retain core permissions and saved layouts'}


def checked_worker_identity(original):
    import pwd
    users = [line.removeprefix('User=') for line in original.splitlines() if line.startswith('User=')]
    require(len(users) == 1 and re.fullmatch(r'oaw-[a-z0-9-]+', users[0]), 'Expected one dedicated worker identity')
    account = pwd.getpwnam(users[0])
    require(account.pw_uid > 0 and account.pw_gid > 0, 'Worker migration identity must have a nonroot UID and group')
    return users[0]


def worker_unit(original, app, helper, service, *, preview=False):
    lines = original.splitlines()
    root_directories = [line for line in lines if line.startswith('RootDirectory=')]
    users = [line for line in lines if line.startswith('User=')]
    require(len(users) == 1 and re.fullmatch(r'User=oaw-[a-z0-9-]+', users[0])
            and [line for line in lines if line.startswith('ProtectSystem=')] == ['ProtectSystem=strict']
            and any(line.startswith('BindReadOnlyPaths=') and line.removeprefix('BindReadOnlyPaths=') for line in lines)
            and not any(line.startswith(('ExecStartPre=', 'ExecStartPost=', 'ExecStop=', 'ExecStopPost=',
                                         'ExecReload=', 'ExecCondition=', 'PermissionsStartOnly=')) for line in lines),
            'Worker unit differs from the hardened hosted deployment')
    require(len(root_directories) <= 1, 'Worker unit has ambiguous filesystem isolation')
    mapped_helper = '/workspace/release_worker.py' if root_directories else str(helper)
    result = []
    for line in original.splitlines():
        if line.startswith(('Requires=', 'After=', 'RestartSec=', 'Sockets=', 'Type=', 'TimeoutStartSec=')):
            continue
        if line == '[Service]':
            result.extend([line, 'Type=oneshot', 'TimeoutStartSec=300'])
            if root_directories:
                result.append(f'BindReadOnlyPaths={helper}:{mapped_helper}')
        elif line.startswith('ExecStart='):
            result.append(f'ExecStart={app}/backend/.venv/bin/python -I {mapped_helper} migrate --source {app} '
                          f'--config /var/lib/{service}-profile/worker.json'
                          + (' --environment preview' if preview else ' --environment production'))
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


def stop(request, *, config=None):
    _, hosting, _ = paths(request)
    name = request['service']
    units = [name + '-gateway.service', name + '-gateway.socket', *[f'{name}-worker-{p}{suffix}' for p, _ in profiles(hosting)
                                         for suffix in ('.service', '.socket')]]
    config = config or require_execution(request, deploying=True)
    if config['environment'] != 'preview':
        run('systemctl', 'stop', name + '-provisioner.timer', name + '-provisioner.service')
    run('systemctl', 'stop', name + '.target', *units)


def resume(request, *, gateway=True, config=None):
    _, hosting, _ = paths(request)
    name = request['service']
    config = config or require_execution(request, deploying=True)
    # Socket activation avoids restarting every historical workspace concurrently.
    run('systemctl', 'start', *[f'{name}-worker-{p}.socket' for p, _ in profiles(hosting)])
    if gateway:
        run('systemctl', 'start', name + '-gateway.socket', name + '-gateway.service')
        require(config['environment'] == 'preview', 'Production provisioner broker is not installed')


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
    restore_bundle(request, b)
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
    helper = trusted_worker()
    manifest = read(b / 'stage/release-manifest.json')
    payload_files(manifest)
    pinned = approved_release(request, manifest)
    _, baseline_receipt = baseline(pinned, manifest)
    changed = stopped = False
    with lock(releases, wait=True):
        try:
            config = require_execution(request, deploying=True)
            migrations = {}
            for identifier, _ in profiles(hosting):
                original = (UNITS / f'{request["service"]}-worker-{identifier}.service').read_text()
                checked_worker_identity(original)
                require([line for line in original.splitlines() if line.startswith('RootDirectory=')]
                        == [f'RootDirectory={config["sandbox_root"]}'],
                        'Preview workers must use the independently prepared registered filesystem boundary')
                migrations[identifier] = worker_unit(original, app, helper, request['service'],
                                                     preview=config['environment'] == 'preview')
            if rollback:
                current, fingerprint = receipt(app)
                manifest = read(b / 'stage/release-manifest.json')
                interrupted = read(b / 'state.json')['phase'] in {'stopped', 'backed-up', 'installing', 'migrating',
                                                               'verifying', 'rolling-back', 'recovery-required'}
                require(current.get('release_id') == request['release'] or (interrupted and fingerprint == baseline_receipt),
                        'Only the currently installed release can be rolled back')
                idle(hosting)
                stopped = True
                stop(request, config=config)
                for name in read(b / 'state.json').get('helpers', []):
                    require(re.fullmatch(r'oaw-release-[a-f0-9]{8}-[a-f0-9-]{36}\.service', name), 'Invalid recovery unit')
                    if (UNITS / name).exists():
                        helpers.append(name)
                        run('systemctl', 'stop', name)
                save(b, 'rolling-back')
                restore(request, b)
                resume(request, config=config)
                return save(b, 'rolled-back')
            plan(request)
            stopped = True
            stop(request, config=config)
            save(b, 'stopped')
            idle(hosting)
            manifest = read(b / 'stage/release-manifest.json')
            backup(request, b, manifest)
            save(b, 'backed-up')
            old, _ = receipt(app)
            changed = True
            save(b, 'installing')
            install_bundle(request, b)
            for name in sorted(manifest['files'], key=lambda p: p == 'frontend/dist/index.html'):
                target = destination(app, name)
                public_parents(target.parent, app)
                shutil.copyfile(b / 'stage' / name, target)
                target.chmod(0o755 if (b / 'stage' / name).stat().st_mode & 0o111 else 0o644)
            for name in old['source_files'].keys() - manifest['files'].keys():
                if not name.startswith('frontend/dist/assets/'):
                    destination(app, name).unlink(missing_ok=True)
            units = UNITS
            for identifier, _ in ([] if manifest.get('routine') else profiles(hosting)):
                name = f'oaw-release-{request["release"][-8:]}-{identifier}.service'
                target = units / name
                require(not target.exists(), 'Migration unit already exists')
                target.write_text(migrations[identifier])
                target.chmod(0o644)
                helpers.append(name)
            if helpers:
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
            resume(request, gateway=False, config=config)
            save(b, 'verifying')
            verify_release(request, config, entries_only=bool(manifest.get('routine')))
            new = {'source_commit': manifest['source_commit'], 'source_files': manifest['files'],
                   'release_id': request['release'], 'previous_source_commit': old['source_commit'], 'backup': str(b),
                   'archive_sha256': pinned['archive_sha256'], 'environment': config['environment'],
                   'operator_protocol': PROTOCOL_VERSION, 'operator_sha256': digest(trusted_operator())}
            write(app / '.oaw-deployment.json', new)
            resume(request, config=config)
            run('systemctl', 'is-active', '--quiet', request['service'] + '-gateway.service')
            return save(b, 'complete', commit=manifest['source_commit'],
                        verification=('login entry workers' if manifest.get('routine') else 'all workers') + ' bootstrap and authorization checks passed')
        except BaseException as error:
            with (b / 'failure.log').open('a') as stream:
                traceback.print_exc(file=stream)
            if changed:
                try:
                    for name in helpers:
                        run('systemctl', 'stop', name)
                    stop(request, config=config)
                    save(b, 'rolling-back')
                    restore(request, b)
                    resume(request, config=config)
                    return save(b, 'failed-rolled-back', error='Deployment failed; previous code and data restored', log=str(b / 'failure.log'))
                except BaseException:
                    with (b / 'failure.log').open('a') as stream:
                        traceback.print_exc(file=stream)
                    return save(b, 'recovery-required', error='Automatic recovery failed; services remain stopped', log=str(b / 'failure.log'))
            if not rollback and stopped:
                resume(request, config=config)
            if rollback and stopped:
                return save(b, 'recovery-required', error='Recovery failed; services remain stopped', log=str(b / 'failure.log'))
            return save(b, 'failed', error=str(error), log=str(b / 'failure.log'))
        finally:
            for name in helpers:
                subprocess.run(['systemctl', 'stop', name], capture_output=True, timeout=60)
                (UNITS / name).unlink(missing_ok=True)
            if helpers:
                run('systemctl', 'daemon-reload')


def dispatch(request):
    trusted_operator()
    app, hosting, releases = paths(request)
    authorize(request)
    action = request['action']
    if action == 'status' and not request.get('release'):
        value, fingerprint = receipt(app)
        return {'commit': value['source_commit'], 'receipt': fingerprint, 'release': value.get('release_id'),
                'profiles': len(list(profiles(hosting))), 'archive_sha256': value.get('archive_sha256'),
                'environment': value.get('environment'), 'operator_protocol': PROTOCOL_VERSION,
                'operator_sha256': digest(trusted_operator())}
    b = release_dir(request)
    if action == 'init':
        approval = authorize(request, archive_sha256=request['archive_sha256'], source_commit=request.get('source_commit'))
        releases.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not b.exists():
            b.mkdir(mode=0o700)
            write(b / 'request.json', {**request, **approval, 'purpose': 'trusted_manual'})
            return save(b, 'uploading', release=request['release'])
    pinned = read(b / 'request.json')
    require(all(request[k] == pinned[k] for k in ('app', 'hosting', 'releases', 'service', 'release')),
            'Release target differs from its staged request')
    for key in ('purpose', 'source_commit', 'baseline_commit', 'baseline_receipt'):
        require(key not in request or key not in pinned or request[key] == pinned[key],
                'Release request differs from its staged approval or baseline')
    if action == 'init':
        require(request['archive_sha256'] == pinned['archive_sha256'], 'Release ID belongs to another archive')
        return read(b / 'state.json')
    if action == 'seed':
        with lock(releases):
            return seed(request)
    if action == 'stage':
        require(read(b / 'state.json')['phase'] == 'uploading', 'Release is not awaiting an upload')
        require(request['archive_sha256'] == pinned['archive_sha256'], 'Release upload differs from its pinned archive')
        require(digest(b / 'bundle.tar.gz') == request['archive_sha256'], 'Uploaded archive checksum differs')
        approval = authorize(pinned, archive_sha256=pinned['archive_sha256'], source_commit=pinned.get('source_commit'))
        if not (b / 'stage').exists():
            temporary = b / ('unpack-' + uuid4().hex)
            extract_bundle(b / 'bundle.tar.gz', temporary, source_commit=approval['source_commit'], request=pinned)
            temporary.replace(b / 'stage')
        pinned['manifest_sha256'] = digest(b / 'stage/release-manifest.json')
        write(b / 'request.json', pinned)
        result = plan(request)
        return save(b, 'staged', plan=result)
    if action == 'check':
        with lock(releases):
            require(read(b / 'state.json')['phase'] in {'staged', 'ready'}, 'Release is not ready for checks')
            result = plan(request)
            prepare_bundle(request, require_execution(request))
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
    operator = trusted_operator()
    b = release_dir(request)
    manifest = read(b / 'stage/release-manifest.json')
    payload_files(manifest)
    approved_release(request, manifest)
    require_execution(request, deploying=True)
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
        '--property=TimeoutStartSec=infinity', sys.executable, '-I', '-S', str(operator),
        str(b / 'transaction.json'))
    return {'phase': 'queued', 'release': request['release']}


if __name__ == '__main__':
    try:
        request = read(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
        require(os.geteuid() == 0, 'Release operations require the deployment operator root account')
        require(sys.flags.isolated and sys.flags.no_site, 'Run the trusted operator with Python -I -S')
        print(json.dumps(dispatch(request)), flush=True)
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        sys.exit(1)
