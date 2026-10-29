"""Prepare and deploy a pinned OAW commit without touching the developer's checkout."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen
from urllib.error import HTTPError
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / 'deploy/aliyun'
DEFAULT_CONFIG = Path.home() / '.config/oaw/aliyun-release.json'
BACKEND_TESTS = ['backend/tests/test_aliyun_release.py', 'backend/tests/test_aliyun_hosted.py',
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


def config(path):
    value = read(path)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', value['ssh_target']):
        raise ValueError('ssh_target must be user@host, without shell syntax')
    for key in ('repository', 'worktree', 'output', 'python', 'identity_file'):
        value[key] = str(Path(value[key]).expanduser().resolve())
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', value['branch']):
        raise ValueError('Invalid release branch')
    if value.get('remote', 'origin') != 'origin':
        raise ValueError('This release workflow uses the existing origin remote')
    if not value['public_origin'].startswith('https://'):
        raise ValueError('Public verification requires HTTPS')
    return value


def ssh_options(cfg):
    return ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=20',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-i', cfg['identity_file']]


def target(cfg):
    return {k: cfg[k] for k in ('app', 'hosting', 'releases', 'service')}


def target_id(cfg):
    return hashlib.sha256(json.dumps({**target(cfg), 'host': cfg['ssh_target']}, sort_keys=True).encode()).hexdigest()


def remote(cfg, action, release=None, **extra):
    request = {**target(cfg), 'action': action, **extra}
    if release:
        request['release'] = release
    source = (OPERATOR / 'release_remote.py').read_text(encoding='utf-8')
    # Both values are data literals, not interpolated shell commands.
    code = "import base64,json,os\n" + f"payload={json.dumps(base64.b64encode(source.encode()).decode())}\n"
    code += "namespace={'__name__':'release_transport'}\nexec(base64.b64decode(payload),namespace)\n"
    code += "namespace['require'](os.geteuid()==0,'Use the deployment operator root account')\n"
    code += f"print(json.dumps(namespace['dispatch'](json.loads({json.dumps(json.dumps(request))}))),flush=True)\n"
    command = shlex.quote(cfg['app'] + '/backend/.venv/bin/python') + ' -'
    output = run(['ssh', *ssh_options(cfg), cfg['ssh_target'], command], capture=True, input=code, timeout=3900)
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
            backend.add('backend/tests/test_aliyun_release.py')
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


def prepare(cfg, args):
    repository, tree = Path(cfg['repository']), Path(cfg['worktree'])
    if repository == tree or not tree.is_relative_to(repository / '.tmp'):
        raise ValueError('Configure a separate build worktree under the repository .tmp directory')
    git(repository, 'fetch', 'origin', capture=False)
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
    if args.merge:
        commit = git(repository, 'rev-parse', '--verify', '--end-of-options', args.merge + '^{commit}')
        try:
            git(tree, 'merge', '--no-ff', '--no-edit', commit, capture=False)
        except RuntimeError as error:
            raise RuntimeError(f'Merge stopped in {tree}. Resolve and commit there, then use prepare --resolved; dev was not changed') from error
    elif args.resolved:
        raise ValueError('--resolved must be handled before resetting the build worktree')
    return build(cfg, tree, base, args)


def build(cfg, tree, base, args):
    clean(tree)
    commit = git(tree, 'rev-parse', 'HEAD')
    git(tree, 'merge-base', '--is-ancestor', base, commit)
    baseline = remote(cfg, 'status')
    changed = git(tree, 'diff', '--name-only', baseline['commit'], commit).splitlines()
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
    operators = {'operator/' + name: OPERATOR / name for name in ('release_remote.py', 'release_worker.py')}
    manifest = {'version': 1, 'source_commit': commit, 'baseline_receipt': baseline['receipt'],
                'baseline_commit': baseline['commit'], 'routine': routine,
                'files': files, 'operator_files': {n: sha(p) for n, p in operators.items()}}
    write(directory / 'release-manifest.json', manifest)
    archive = directory / 'bundle.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in files:
            tar.add(tree / name, arcname=name, recursive=False)
        for name, path in operators.items():
            tar.add(path, arcname=name, recursive=False)
        tar.add(directory / 'release-manifest.json', arcname='release-manifest.json', recursive=False)
    record = {'release': release, 'commit': commit, 'target': target_id(cfg), 'archive_sha256': sha(archive),
              'pytest_paths': tests, 'frontend_tests': frontend, 'routine': routine, 'full_ci': False}
    write(directory / 'release.json', record)
    # Publish the tested integration commit without changing any checked-out branch.
    if commit != base:
        occupied = 'branch refs/heads/' + cfg['branch'] in git(Path(cfg['repository']), 'worktree', 'list', '--porcelain').splitlines()
        if occupied:
            raise ValueError('Release branch is checked out elsewhere; detach/switch that worktree before advancing it')
        git(Path(cfg['repository']), 'update-ref', 'refs/heads/' + cfg['branch'], commit, base)
    git(Path(cfg['repository']), 'push', 'origin', cfg['branch'], capture=False)
    write(Path(cfg['output']) / 'latest.json', record)
    print(json.dumps({'prepared': release, 'commit': commit, 'archive': str(archive),
                      'next': 'deploy --release ' + release + ' --check'}), flush=True)


def selected(cfg, identifier):
    if identifier is None:
        identifier = read(Path(cfg['output']) / 'latest.json')['release']
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}-[a-f0-9]{8}', identifier):
        raise ValueError('Invalid release ID')
    directory = Path(cfg['output']) / identifier
    value = read(directory / 'release.json')
    if value['target'] != target_id(cfg):
        raise ValueError('Release was prepared for a different server/installation')
    return directory, value


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


def verify_public(origin):
    deadline = time.monotonic() + 60
    while True:
        try:
            with urlopen(origin + '/auth/me', timeout=10) as response:
                value = json.load(response)
            if value.get('hosted') is not True or value.get('authenticated') is not False:
                raise ValueError('Public HTTPS authentication entry check failed; inspect status (do not replay the release)')
            return
        except HTTPError as error:
            # systemd active precedes the gateway's HTTP listener readiness.
            if error.code not in {502, 503, 504} or time.monotonic() >= deadline:
                raise
            time.sleep(2)


def deploy(cfg, args):
    directory, record = selected(cfg, args.release)
    identifier = record['release']
    archive = directory / 'bundle.tar.gz'
    if sha(archive) != record['archive_sha256']:
        raise ValueError('Local deployment archive changed')
    if not record.get('uploaded'):
        state = remote(cfg, 'init', identifier, archive_sha256=record['archive_sha256'])
        if state['phase'] == 'uploading':
            remote_path = cfg['releases'] + '/' + identifier + '/bundle.tar.gz'
            run(['scp', *ssh_options(cfg), str(archive), cfg['ssh_target'] + ':' + remote_path])
            remote(cfg, 'stage', identifier, archive_sha256=record['archive_sha256'])
        record['uploaded'] = True
        write(directory / 'release.json', record)
    state = remote(cfg, 'status', identifier)
    if state['phase'] in {'complete', 'already-current'}:
        verify_public(cfg['public_origin'])
        print(json.dumps({'deployed': state, 'public_https': True}), flush=True)
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
    verify_public(cfg['public_origin'])
    print(json.dumps({'deployed': result, 'public_https': True}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare', help='Build/test the Ali branch, optionally merging a dev ref')
    prepare_parser.add_argument('--merge', help='For example origin/dev, or an exact reviewed commit')
    prepare_parser.add_argument('--resolved', action='store_true', help='Continue after manually resolving the build worktree merge')
    prepare_parser.add_argument('--pytest-path', action='append', default=[])
    prepare_parser.add_argument('--test-plan', type=Path,
        help='Operator-selected JSON {backend: [...], frontend: [...]}; replaces automatic test selection, retaining full deployment safeguards')
    prepare_parser.add_argument('--routine', action='store_true',
        help='Operator reviewed: no schema/publication changes. Focus tests, retain contracts and verify current login entries')
    deploy_parser = commands.add_parser('deploy')
    deploy_parser.add_argument('--release')
    deploy_parser.add_argument('--check', action='store_true', help='Stage and prepare dependencies; do not restart services or migrate user data')
    status_parser = commands.add_parser('status')
    status_parser.add_argument('--release')
    rollback_parser = commands.add_parser('rollback')
    rollback_parser.add_argument('--release', required=True)
    rollback_parser.add_argument('--restore-data', action='store_true', help='Restore code AND the pre-update data snapshot; preserve replaced data privately')
    args = parser.parse_args(argv)
    cfg = config(args.config)
    if args.command == 'prepare':
        if args.resolved:
            if args.merge:
                parser.error('--resolved and --merge are mutually exclusive')
            tree = Path(cfg['worktree'])
            merge_path = Path(git(tree, 'rev-parse', '--git-path', 'MERGE_HEAD'))
            if (tree / merge_path).exists():
                raise ValueError('Finish and commit the merge first')
            build(cfg, tree, git(Path(cfg['repository']), 'rev-parse', cfg['branch']), args)
        else:
            prepare(cfg, args)
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
