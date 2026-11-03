"""Prepare and deploy a pinned OAW commit without touching the developer's checkout."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / 'deploy/aliyun'
DEFAULT_CONFIG = Path.home() / '.config/oaw/aliyun-release.json'
TRUSTED_OPERATOR = '/opt/oaw-release-operator/release_remote.py'
PREVIEW_OPERATOR = '/opt/oaw-release-operator-preview/release_remote.py'
BACKEND_TESTS = ['backend/tests/test_aliyun_release.py', 'backend/tests/test_aliyun_hosted.py',
    'backend/tests/test_aliyun_release_security.py',
    'backend/tests/test_aliyun_sandbox.py', 'backend/tests/test_hosted_gateway.py',
    'backend/tests/test_hosted_upload_policy.py', 'backend/tests/test_plugin_deployment.py']
FRONTEND_TESTS = ['src/auth/hostedSession.test.ts', 'src/api/fetchWithRetry.test.ts']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(argv, *, cwd=None, capture=False, input=None, timeout=None, env=None):
    result = subprocess.run([str(v) for v in argv], cwd=cwd, capture_output=capture, input=input,
                            text=True, encoding='utf-8', timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f'{Path(str(argv[0])).name} exited {result.returncode}' +
                           (': ' + result.stderr[-1500:] if capture and result.stderr else ''))
    return result.stdout.strip() if capture else ''


def git(repo, *args, capture=True):
    return run(['git', '-c', 'safe.directory=' + repo.as_posix(), '-C', repo, *args], capture=capture)


def build_config(path):
    value = read(path)
    for key in ('repository', 'worktree', 'output', 'python'):
        value[key] = str(Path(value[key]).expanduser().resolve())
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', value['branch']):
        raise ValueError('Invalid release branch')
    if value.get('remote', 'origin') != 'origin':
        raise ValueError('This release workflow uses the existing origin remote')
    return value


def config(path):
    value = build_config(path)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', value['ssh_target']):
        raise ValueError('ssh_target must be user@host, without shell syntax')
    value['identity_file'] = str(Path(value['identity_file']).expanduser().resolve())
    operator(value)
    private = value.get('allow_local_http', False)
    if private and value.get('environment') != 'preview':
        raise ValueError('Local HTTP verification is supported only for Preview')
    verification_origin(value['public_origin'], private)
    return value


def verification_origin(value, allow_local_http=False):
    if type(allow_local_http) is not bool:
        raise ValueError('allow_local_http must be a boolean')
    if not isinstance(value, str):
        raise ValueError('Verification origin must be a string')
    if allow_local_http:
        match = re.fullmatch(r'http://(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{3,4})', value)
        if not match or not 1024 <= int(match[1]) <= 65535:
            raise ValueError('Private Preview requires an exact loopback HTTP origin on port 1024-65535')
    else:
        origin = urlsplit(value)
        if (origin.scheme != 'https' or not origin.hostname or not origin.netloc
                or any((origin.path, origin.query, origin.fragment, origin.username, origin.password))
                or re.search(r'\s|\\', value) or origin.port is not None and not 1 <= origin.port <= 65535):
            raise ValueError('Public verification requires an exact HTTPS origin')


def operator(cfg):
    environment = cfg.get('environment', 'production')
    if environment not in {'production', 'preview'}:
        raise ValueError('environment must be production or preview')
    return PREVIEW_OPERATOR if environment == 'preview' else TRUSTED_OPERATOR


def ssh_options(cfg):
    return ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=20',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-i', cfg['identity_file']]


def target(cfg):
    return {k: cfg[k] for k in ('app', 'hosting', 'releases', 'service')}


def target_id(cfg):
    binding = {**target(cfg), 'host': cfg['ssh_target']}
    # Preserve existing production artifact bindings; Preview has a distinct
    # independently installed controller and must never reuse that binding.
    if operator(cfg) == PREVIEW_OPERATOR:
        binding['environment'] = 'preview'
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def remote(cfg, action, release=None, **extra):
    request = {**target(cfg), 'action': action, 'purpose': 'trusted_manual', **extra}
    if release:
        request['release'] = release
    # The server controller is independently installed and reviewed. Neither the
    # caller checkout nor the build archive supplies privileged Python source.
    command = '/usr/bin/python3 -I -S ' + operator(cfg)
    output = run(['ssh', *ssh_options(cfg), cfg['ssh_target'], command], capture=True,
                 input=json.dumps(request), timeout=3900)
    return json.loads(output.splitlines()[-1])


def npm(tree, *args):
    executable = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not executable:
        raise ValueError('Node.js and npm are required on the build machine')
    command = [executable]
    if os.name == 'nt':
        cli = Path(executable).parent / 'node_modules/npm/bin/npm-cli.js'
        if not cli.is_file():
            raise ValueError('Cannot find npm-cli.js next to npm.cmd')
        command = [shutil.which('node') or 'node', str(cli)]
    environment = os.environ.copy()
    for key in ('OPEN_AGENT_WORLD_CORE_ONLY', 'OAW_ALLOW_PACK_FAILURES', 'OAW_PACK_FAILURE_REPORT'):
        environment.pop(key, None)
    run([*command, *args], cwd=tree / 'frontend', env=environment)


def clean(tree):
    # A build may rewrite a generated file with equivalent checkout line endings.
    if (git(tree, 'status', '--porcelain') and not git(tree, 'diff', '--name-only')
            and not git(tree, 'diff', '--cached', '--name-only')):
        git(tree, 'add', '--renormalize', '--update', '--', '.')
    if git(tree, 'status', '--porcelain'):
        raise ValueError(f'Worktree has changes: {tree}; preserve/review them before releasing')


def test_scope(changed, *, routine=False):
    """Routine releases use changed test files; broader upgrades keep hosting checks."""
    backend = set() if routine else set(BACKEND_TESTS)
    frontend = set() if routine else set(FRONTEND_TESTS)
    for name in changed:
        if name.endswith('.py') and ('/tests/test_' in name):
            backend.add(name)
        if name.startswith('frontend/src/') and re.search(r'\.test\.tsx?$', name):
            frontend.add(name.removeprefix('frontend/'))
        if name.startswith(('scripts/aliyun_release', 'deploy/aliyun/release_')):
            backend.update({'backend/tests/test_aliyun_release.py', 'backend/tests/test_aliyun_release_security.py'})
        if name.startswith(('backend/hosting/', 'backend/security/', 'backend/cloud/')):
            backend.update(BACKEND_TESTS)
        parts = Path(name).parts
        if (not routine and len(parts) > 2 and parts[0] == 'plugins'
                and Path(name).suffix in {'.py', '.toml', '.json'} and parts[2] != 'frontend'):
            backend.add('plugins/' + parts[1] + '/tests')
    if any(p.startswith('plugins/knowledge_base/frontend/') for p in changed):
        frontend.add('src/plugins/KnowledgeWorkspace.test.tsx')
    return backend, frontend


def prepare_npm(tree):
    root = tree / 'frontend'
    stamp = root / 'node_modules/.oaw-release-install.json'
    inputs = {name: sha(root / name) for name in ('package.json', 'package-lock.json')}
    inputs['node'] = run(['node', '--version'], capture=True)
    if (stamp.exists() and read(stamp) == inputs
            and all((root / 'node_modules' / name).is_file() for name in ('vitest/vitest.mjs', 'vite/bin/vite.js', 'typescript/bin/tsc'))):
        print('Reusing installed frontend dependencies (unchanged lockfile and Node version)', flush=True)
        return
    npm(tree, 'ci', '--no-audit', '--no-fund')
    write(stamp, inputs)


def worktree(cfg):
    repository, tree = Path(cfg['repository']), Path(cfg['worktree'])
    if repository == tree or not tree.is_relative_to(repository / '.tmp'):
        raise ValueError('Configure a separate build worktree under the repository .tmp directory')
    base = git(repository, 'rev-parse', '--verify', cfg['branch'] + '^{commit}')
    if not tree.exists():
        git(repository, 'worktree', 'add', '--detach', str(tree), base, capture=False)
    else:
        if not (tree / '.git').is_file():
            raise ValueError('Build path is not a linked Git worktree')
        clean(tree)
        if git(tree, 'branch', '--show-current'):
            raise ValueError('Build worktree must remain detached')
        git(tree, 'switch', '--detach', base, capture=False)
    return repository, tree, base


def sync(cfg, args):
    """Explicit legacy Git synchronization; never part of a build or CI job."""
    repository = Path(cfg['repository'])
    git(repository, 'fetch', 'origin', capture=False)
    if args.resolved:
        tree = Path(cfg['worktree'])
        if repository == tree or not tree.is_relative_to(repository / '.tmp') or not (tree / '.git').is_file():
            raise ValueError('Resolve only the configured linked worktree under the repository .tmp directory')
        clean(tree)
        if git(tree, 'branch', '--show-current'):
            raise ValueError('Synchronization worktree must remain detached')
        base = git(repository, 'rev-parse', '--verify', cfg['branch'] + '^{commit}')
    else:
        repository, tree, base = worktree(cfg)
    if args.merge:
        commit = git(repository, 'rev-parse', '--verify', '--end-of-options', args.merge + '^{commit}')
        try:
            git(tree, 'merge', '--no-ff', '--no-edit', commit, capture=False)
        except RuntimeError as error:
            raise RuntimeError(f'Merge stopped in {tree}. Resolve and commit there, then use sync --resolved; dev was not changed') from error
    commit = git(tree, 'rev-parse', 'HEAD')
    git(tree, 'merge-base', '--is-ancestor', base, commit)
    if commit != base:
        occupied = 'branch refs/heads/' + cfg['branch'] in git(repository, 'worktree', 'list', '--porcelain').splitlines()
        if occupied:
            raise ValueError('Release branch is checked out elsewhere; do not advance another session\'s branch')
        git(repository, 'update-ref', 'refs/heads/' + cfg['branch'], commit, base)
    git(repository, 'push', 'origin', cfg['branch'], capture=False)
    print(json.dumps({'synchronized': commit, 'branch': cfg['branch'], 'built': False}), flush=True)
    return commit


def prepare(cfg, args):
    """Build a local detached copy; merging and publication belong to sync/PRs."""
    _, tree, _ = worktree(cfg)
    return build(cfg, tree, args)


def build(cfg, tree, args):
    if Path(git(tree, 'rev-parse', '--show-toplevel')).resolve() != tree.resolve():
        raise ValueError('Build source must be the root of its Git checkout')
    clean(tree)
    commit = git(tree, 'rev-parse', 'HEAD')
    comparison = getattr(args, 'compare_ref', None)
    if comparison:
        comparison = git(tree, 'rev-parse', '--verify', '--end-of-options', comparison + '^{commit}')
        comparison = git(tree, 'merge-base', comparison, commit)
    else:
        parents = git(tree, 'rev-list', '--parents', '-n', '1', commit).split()
        comparison = parents[1] if len(parents) > 1 else commit
    changed = (git(tree, 'diff', '--name-only', comparison, commit).splitlines()
               if comparison != commit else git(tree, 'ls-files').splitlines())
    routine = getattr(args, 'routine', False)
    if routine:
        import runpy
        runpy.run_path(str(OPERATOR / 'release_remote.py'))['routine_guard'](changed)
    tests, frontend = test_scope(changed, routine=routine)
    plan_path = getattr(args, 'test_plan', None)
    if plan_path:
        plan = read(plan_path)
        tests, frontend = set(plan.get('backend', [])), set(plan.get('frontend', []))
        for name in [*tests, *frontend]:
            relative = Path(name.split('::')[0])
            if relative.is_absolute() or '..' in relative.parts or name.startswith('-'):
                raise ValueError('Test plan paths must be relative to the build checkout')
    tests.update([*([] if plan_path else cfg.get('backend_tests', [])), *args.pytest_path])
    tests = sorted(p for p in tests if (tree / p.split('::')[0]).exists())
    tests = [p for p in tests if not any((tree / parent).is_dir() and p.startswith(parent + '/')
                                       for parent in tests)]
    if not plan_path and not tests and any(p.endswith('.py') for p in changed):
        raise ValueError('Python changes require relevant tests; add --pytest-path')
    print(json.dumps({'stage': 'testing', 'commit': commit, 'pytest_paths': tests}), flush=True)
    python = cfg['python']
    code = "from pathlib import Path;import sys;packs=[p for root in ('plugins','packs') for p in Path(root).glob('*/pyproject.toml')];sys.path[:0]=[str(Path.cwd()),*[str(p.parent/'src') for p in packs],*[str(p.parent) for p in packs]];import pytest;raise SystemExit(pytest.main(sys.argv[1:]))"
    if tests:
        run([python, '-c', code, *tests, '-q', '-o', 'addopts=', '-p', 'no:cacheprovider'], cwd=tree)
    prepare_npm(tree)
    frontend = sorted(frontend | set([] if plan_path else cfg.get('frontend_tests', [])))
    if frontend:
        run(['node', 'node_modules/vitest/vitest.mjs', 'run', '--no-cache', *frontend], cwd=tree / 'frontend')
    npm(tree, 'run', 'build')
    clean(tree)
    release = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + commit[:12] + '-' + uuid4().hex[:8]
    directory = Path(cfg['output']) / release
    directory.mkdir(parents=True)
    names = set(git(tree, 'ls-files').splitlines())
    for directory_ in [tree / 'frontend/dist', *(tree / 'plugins').glob('*/frontend/dist')]:
        names.update(p.relative_to(tree).as_posix() for p in directory_.rglob('*') if p.is_file())
    # Reuse the server's archive/path validator rather than accepting arbitrary files.
    import runpy
    rules = runpy.run_path(str(OPERATOR / 'release_remote.py'))
    for name in names:
        rules['relative'](name)
        if (tree / name).is_symlink():
            raise ValueError('Source release must not contain symlinks: ' + name)
    files = {name: sha(tree / name) for name in sorted(names)}
    if any(Path(name).parts[0] == 'operator' for name in files):
        raise ValueError('Build artifacts cannot supply privileged operator files')
    manifest = {'version': 2, 'kind': 'oaw-build', 'source_commit': commit,
                'comparison_commit': comparison, 'routine': routine, 'files': files,
                'checks': {'backend': tests, 'frontend': frontend, 'full_ci': False}}
    write(directory / 'release-manifest.json', manifest)
    archive = directory / 'bundle.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in files:
            tar.add(tree / name, arcname=name, recursive=False)
        tar.add(directory / 'release-manifest.json', arcname='release-manifest.json', recursive=False)
    record = {'version': 2, 'kind': 'oaw-build', 'release': release, 'commit': commit, 'archive_sha256': sha(archive),
              'pytest_paths': tests, 'frontend_tests': frontend, 'routine': routine, 'full_ci': False}
    write(directory / 'release.json', record)
    write(Path(cfg['output']) / 'latest.json', record)
    print(json.dumps({'prepared': release, 'commit': commit, 'archive': str(archive),
                      'next': 'deploy --release ' + release + ' --check'}), flush=True)
    return directory


def selected(cfg, identifier, artifact=None):
    if artifact is not None:
        if identifier is not None:
            raise ValueError('Choose --artifact or --release, not both')
        directory = Path(artifact).resolve()
        identifier = read(directory / 'release.json')['release']
    elif identifier is None:
        identifier = read(Path(cfg['output']) / 'latest.json')['release']
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}-[a-f0-9]{8}', identifier):
        raise ValueError('Invalid release ID')
    if artifact is None:
        directory = Path(cfg['output']) / identifier
    value = read(directory / 'release.json')
    if value.get('release', identifier) != identifier:
        raise ValueError('Build record belongs to another release')
    if value.get('version') == 2 and value.get('kind') != 'oaw-build':
        raise ValueError('Unsupported build artifact')
    if value.get('target') is not None and value['target'] != target_id(cfg):
        raise ValueError('Release was prepared for a different server/installation')
    return directory, value


def verify_artifact(directory, record):
    """Check immutable content as data before any deployment/server operation."""
    archive = directory / 'bundle.tar.gz'
    if archive.is_symlink() or not archive.is_file() or sha(archive) != record['archive_sha256']:
        raise ValueError('Local deployment archive changed')
    import runpy
    rules = runpy.run_path(str(OPERATOR / 'release_remote.py'))
    with tempfile.TemporaryDirectory(prefix='oaw-build-check-') as temporary:
        manifest = rules['extract_bundle'](archive, Path(temporary) / 'stage')
    if manifest['source_commit'] != record['commit']:
        raise ValueError('Build record and archive commits differ')
    if bool(manifest.get('routine')) != bool(record.get('routine')):
        raise ValueError('Build record and archive release modes differ')
    return manifest


def wait(cfg, identifier):
    previous = None
    while True:
        result = remote(cfg, 'status', identifier)
        phase = result['phase']
        progress = (phase, result.get('migrated_profiles'))
        if progress != previous:
            print(json.dumps({k: v for k, v in result.items() if k != 'helpers'}), flush=True)
            previous = progress
        if phase in {'complete', 'already-current', 'rolled-back'}:
            return result
        if phase in {'failed', 'failed-rolled-back', 'recovery-required'}:
            raise RuntimeError(result.get('error', phase))
        if not result['unit_active']:
            raise RuntimeError('Release task is not running; inspect status and private logs before recovery')
        time.sleep(5)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def verify_public(origin, *, allow_local_http=False):
    """Verify HTTPS, or an explicitly selected local SSH tunnel without proxies/redirects."""
    verification_origin(origin, allow_local_http)
    fetch = build_opener(ProxyHandler({}), _NoRedirect()).open if allow_local_http else urlopen
    deadline = time.monotonic() + 60
    while True:
        try:
            with fetch(origin + '/auth/me', timeout=10) as response:
                value = json.load(response)
            if value.get('hosted') is not True or value.get('authenticated') is not False:
                raise ValueError('Hosted authentication entry check failed; inspect status (do not replay the release)')
            return
        except HTTPError as error:
            # systemd active precedes the gateway's HTTP listener readiness.
            if error.code not in {502, 503, 504} or time.monotonic() >= deadline:
                raise
            time.sleep(2)
        except (URLError, ConnectionError, TimeoutError) as error:
            # Before a private gateway is ready, SSH accepts the local TCP
            # connection but cannot yet open its remote Unix socket.
            if not allow_local_http or time.monotonic() >= deadline:
                raise RuntimeError('Entry verification could not connect; check the gateway and SSH tunnel without replaying the release') from error
            time.sleep(2)


def deploy(cfg, args):
    directory, artifact = selected(cfg, args.release, getattr(args, 'artifact', None))
    manifest = verify_artifact(directory, artifact)
    deployment_path = directory / 'deployment.json'
    if deployment_path.exists():
        record = read(deployment_path)
        if any(record.get(key) != artifact[key] for key in ('release', 'commit', 'archive_sha256')) or record.get('target') != target_id(cfg):
            raise ValueError('Deployment binding differs from the fixed artifact or target')
    elif manifest['version'] == 1:
        record = {**artifact, 'target': target_id(cfg), 'baseline_commit': manifest.get('baseline_commit'),
                  'baseline_receipt': manifest['baseline_receipt']}
        write(deployment_path, record)
    else:
        baseline = remote(cfg, 'status')
        record = {**artifact, 'target': target_id(cfg), 'baseline_commit': baseline['commit'],
                  'baseline_receipt': baseline['receipt']}
        write(deployment_path, record)
    identifier = record['release']
    archive = directory / 'bundle.tar.gz'
    pinned = {key: record[key] for key in ('baseline_commit', 'baseline_receipt') if record.get(key) is not None}
    pinned.update(source_commit=record['commit'], archive_sha256=record['archive_sha256'])
    # Server-side identity is authoritative even when resuming a downloaded
    # artifact directory. Never trust a local/self-supplied "uploaded" flag.
    state = remote(cfg, 'init', identifier, **pinned)
    if state['phase'] == 'uploading':
        remote_path = cfg['releases'] + '/' + identifier + '/bundle.tar.gz'
        run(['scp', *ssh_options(cfg), str(archive), cfg['ssh_target'] + ':' + remote_path])
        remote(cfg, 'stage', identifier, **pinned)
    state = remote(cfg, 'status', identifier)
    if state['phase'] in {'complete', 'already-current'}:
        verify_public(cfg['public_origin'], allow_local_http=cfg.get('allow_local_http', False))
        print(json.dumps({'deployed': state, 'public_https': not cfg.get('allow_local_http', False)}), flush=True)
        return
    if state['unit_active']:
        print(json.dumps(wait(cfg, identifier)), flush=True)
        return
    if state['phase'] not in {'staged', 'ready'}:
        raise ValueError('Release is not ready; inspect status and prepare a new release after fixing the reported problem')
    print(json.dumps(remote(cfg, 'check', identifier)), flush=True)
    if args.check:
        return
    result = remote(cfg, 'start', identifier)
    if result['phase'] != 'already-current':
        result = wait(cfg, identifier)
    verify_public(cfg['public_origin'], allow_local_http=cfg.get('allow_local_http', False))
    print(json.dumps({'deployed': result, 'public_https': not cfg.get('allow_local_http', False)}), flush=True)


def seed(cfg, args):
    """Install the initial Preview artifact; never start it or create user data."""
    if operator(cfg) != PREVIEW_OPERATOR:
        raise ValueError('Initial seeding is only supported for Preview')
    directory, artifact = selected(cfg, None, args.artifact)
    manifest = verify_artifact(directory, artifact)
    if manifest['version'] != 2:
        raise ValueError('Preview seeding requires a portable v2 build artifact')
    pinned = {'source_commit': artifact['commit'], 'archive_sha256': artifact['archive_sha256']}
    identifier = artifact['release']
    state = remote(cfg, 'init', identifier, **pinned)
    if state['phase'] != 'uploading':
        raise ValueError('Seed is not awaiting upload; inspect its receipt/state before retrying')
    remote_path = cfg['releases'] + '/' + identifier + '/bundle.tar.gz'
    run(['scp', *ssh_options(cfg), str(directory / 'bundle.tar.gz'), cfg['ssh_target'] + ':' + remote_path])
    result = remote(cfg, 'seed', identifier, **pinned)
    print(json.dumps({'seeded': result, 'services_started': False}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest='command', required=True)
    sync_parser = commands.add_parser('sync', help='Explicit manual Git synchronization; never builds or deploys')
    selection = sync_parser.add_mutually_exclusive_group()
    selection.add_argument('--merge', help='Ref to merge in the isolated synchronization worktree')
    selection.add_argument('--resolved', action='store_true', help='Publish a manually resolved detached merge')
    prepare_parser = commands.add_parser('prepare', help='Build/test the local release branch in an isolated detached worktree')
    build_parser = commands.add_parser('build', help='Build a portable artifact from the checked-out commit, without server configuration')
    build_parser.add_argument('--source', type=Path, default=Path.cwd())
    build_parser.add_argument('--output', type=Path, required=True)
    build_parser.add_argument('--python', default=sys.executable)
    for command in (prepare_parser, build_parser):
        command.add_argument('--compare-ref', help='Local base ref for test selection; CI supplies its event base, never a server baseline')
        command.add_argument('--pytest-path', action='append', default=[])
        command.add_argument('--test-plan', type=Path, help='Operator-selected JSON {backend: [...], frontend: [...]} test selection')
        command.add_argument('--routine', action='store_true', help='Reviewed non-schema update; deployment compatibility guards still apply')
    deploy_parser = commands.add_parser('deploy')
    deploy_parser.add_argument('--release')
    deploy_parser.add_argument('--artifact', type=Path, help='Directory containing a fixed release.json and bundle.tar.gz')
    deploy_parser.add_argument('--check', action='store_true', help='Stage and prepare dependencies; do not restart services or migrate user data')
    seed_parser = commands.add_parser('seed', help='Seed a new Preview from an approved artifact; never start services')
    seed_parser.add_argument('--artifact', type=Path, required=True)
    status_parser = commands.add_parser('status')
    status_parser.add_argument('--release')
    commands.add_parser('verify', help='Recheck the configured HTTPS entry or explicit private SSH tunnel; never deploys')
    rollback_parser = commands.add_parser('rollback')
    rollback_parser.add_argument('--release', required=True)
    rollback_parser.add_argument('--restore-data', action='store_true', help='Restore code AND the pre-update data snapshot; preserve replaced data privately')
    args = parser.parse_args(argv)
    if args.command == 'build':
        tree = args.source.resolve()
        cfg = {'repository': str(tree), 'output': str(args.output.resolve()), 'python': str(Path(args.python).resolve())}
        build(cfg, tree, args)
        return
    if args.command in {'prepare', 'sync'}:
        cfg = build_config(args.config)
        if args.command == 'sync':
            sync(cfg, args)
        else:
            prepare(cfg, args)
        return
    cfg = config(args.config)
    if args.command == 'verify':
        verify_public(cfg['public_origin'], allow_local_http=cfg.get('allow_local_http', False))
        print(json.dumps({'entry_verified': True, 'public_https': not cfg.get('allow_local_http', False)}), flush=True)
    elif args.command == 'seed':
        seed(cfg, args)
    elif args.command == 'deploy':
        deploy(cfg, args)
    elif args.command == 'status':
        print(json.dumps(remote(cfg, 'status', args.release)), flush=True)
    else:
        remote(cfg, 'rollback', args.release, restore_data=args.restore_data)
        wait(cfg, args.release)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
