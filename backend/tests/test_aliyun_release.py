"""Operator workflow tests: real Git/files/SQLite, with no cloud side effects."""
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


remote = module('release_remote', 'deploy/aliyun/release_remote.py')
worker = module('release_worker', 'deploy/aliyun/release_worker.py')
client = module('release_client', 'scripts/aliyun_release.py')


def test_public_check_waits_for_gateway_readiness(monkeypatch):
    responses = iter([client.HTTPError('https://example/auth/me', 502, 'Starting', {}, None),
                      io.BytesIO(b'{"hosted":true,"authenticated":false}')])
    delays = []
    def fetch(*args, **kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(client, 'urlopen', fetch)
    monkeypatch.setattr(client.time, 'sleep', delays.append)
    client.verify_public('https://example')
    assert delays == [2]


def test_public_check_rejects_incorrect_authentication_state(monkeypatch):
    monkeypatch.setattr(client, 'urlopen', lambda *a, **k: io.BytesIO(b'{"hosted":true,"authenticated":true}'))
    with pytest.raises(ValueError, match='authentication entry check failed'):
        client.verify_public('https://example')


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as tar:
        for name, content, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))


@pytest.mark.parametrize('name,kind', [('../outside', tarfile.REGTYPE),
    ('/outside', tarfile.REGTYPE), ('backend/.venv/key', tarfile.REGTYPE),
    ('data/private', tarfile.REGTYPE), ('safe', tarfile.SYMTYPE)])
def test_archive_rejects_unsafe_members_before_extraction(tmp_path, name, kind):
    archive(tmp_path / 'bundle.tgz', [(name, b'x', kind)])
    with pytest.raises(ValueError):
        remote.extract_bundle(tmp_path / 'bundle.tgz', tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()


def test_archive_rejects_duplicate_members(tmp_path):
    archive(tmp_path / 'bundle.tgz', [('same', b'a', tarfile.REGTYPE)] * 2)
    with pytest.raises(ValueError, match='Duplicate'):
        remote.extract_bundle(tmp_path / 'bundle.tgz', tmp_path / 'stage')


def test_manifest_updates_pack_contract_without_replacing_saved_identity():
    old = {'id': 'saved-release', 'layout': {'root': 'saved'}, 'panels': [{'card_id': 'kb', 'sections': ['old']}],
           'permissions': {'kb': ['read'], 'conversation': ['chat']}, 'plugin_access': {'kb': {'actions': ['read']}},
           'plugin_versions': {'kb': '1'}, 'runtime_settings': {'agent_runtime': 'configured'}}
    new = deepcopy(old)
    new.update(id='fresh-release', plugin_versions={'kb': '2'}, runtime_settings={})
    new['plugin_access']['kb']['actions'].append('graph')
    new['panels'][0]['sections'].append('graph')
    result, changed = worker.refresh_manifest(old, new)
    assert result['id'] == old['id'] and result['runtime_settings'] == old['runtime_settings']
    assert result['plugin_access'] == new['plugin_access'] and changed == ['kb']
    for key, value in [('layout', {}), ('permissions', {'kb': ['read'], 'conversation': ['terminal']}),
                       ('plugin_access', {'kb': {}, 'other': {}})]:
        altered = {**new, key: value}
        with pytest.raises(ValueError):
            worker.refresh_manifest(old, altered)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    app, hosting, releases = (tmp_path / name for name in ('app', 'hosting', 'releases'))
    for p in (app / 'backend', hosting / 'control', hosting / 'instances', releases):
        p.mkdir(parents=True, exist_ok=True)
    identifier = '00000000-0000-4000-8000-000000000001'
    instance = hosting / 'instances' / identifier
    data = instance / 'data'
    (data / 'database').mkdir(parents=True)
    remote.write(instance / 'worker.json', {'data_root': 'data'})
    remote.write(hosting / 'control/hosting.json', {'profiles': [{'profile_id': identifier}]})
    with sqlite3.connect(hosting / 'control/deployments.sqlite3') as db:
        db.execute('CREATE TABLE hosting_jobs(status TEXT)')
    db.close()
    with sqlite3.connect(data / 'database/world.sqlite3') as db:
        db.execute('CREATE TABLE runs(status TEXT)')
    db.close()
    (data / 'document.txt').write_text('user data before release')
    (app / 'main.py').write_text('old version')
    (app / 'backend/pyproject.toml').write_text('[project]\ndependencies=[]\n')
    old_files = {p.relative_to(app).as_posix(): remote.digest(p) for p in app.rglob('*') if p.is_file()}
    remote.write(app / '.oaw-deployment.json', {'source_commit': 'a' * 40, 'source_files': old_files})
    request = {'app': str(app), 'hosting': str(hosting), 'releases': str(releases), 'service': 'oaw-hosted',
               'release': '20261026T010000Z-' + 'b' * 12 + '-12345678', 'archive_sha256': 'f' * 64}
    monkeypatch.setattr(remote, 'paths', lambda _: (app, hosting, releases))
    monkeypatch.setattr(remote, 'lock', lambda _, **kw: nullcontext())
    b = remote.release_dir(request)
    stage = b / 'stage'
    (stage / 'backend').mkdir(parents=True)
    (stage / 'operator').mkdir()
    (stage / 'new-package').mkdir()
    (stage / 'new-package/file.py').write_text('new directory')
    (stage / 'main.py').write_text('new version')
    (stage / 'backend/pyproject.toml').write_text('[project]\ndependencies=[]\n')
    for name in ('release_remote.py', 'release_worker.py'):
        (stage / 'operator' / name).write_text('# operator')
    files = {p.relative_to(stage).as_posix(): remote.digest(p) for p in stage.rglob('*') if p.is_file() and 'operator' not in p.parts}
    manifest = {'version': 1, 'source_commit': 'b' * 40, 'baseline_receipt': remote.digest(app / '.oaw-deployment.json'),
                'files': files, 'operator_files': {'operator/' + p.name: remote.digest(p) for p in (stage / 'operator').iterdir()}}
    remote.write(stage / 'release-manifest.json', manifest)
    remote.write(b / 'request.json', request)
    remote.save(b, 'ready')
    units = tmp_path / 'units'
    units.mkdir()
    (units / f'oaw-hosted-worker-{identifier}.service').write_text(
        '[Unit]\nAfter=network.target\n[Service]\nUser=oaw-user\nProtectSystem=strict\n'
        'BindReadOnlyPaths=/profile\nEnvironmentFile=/private.env\nExecStart=/old-command\nRestart=always\n')
    monkeypatch.setattr(remote, 'UNITS', units)
    monkeypatch.setattr(remote, 'MAINTENANCE', tmp_path / 'maintenance')
    monkeypatch.setattr(remote.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr=''))
    commands = []
    def run(*args, **kwargs):
        commands.append(args)
        return ''
    monkeypatch.setattr(remote, 'run', run)
    return SimpleNamespace(app=app, hosting=hosting, request=request, b=b, stage=stage, data=data,
                           manifest=manifest, commands=commands, run=run)


def test_successful_transaction_and_explicit_data_rollback(installation):
    f = installation
    assert remote.transaction(f.request)['phase'] == 'complete'
    assert (f.app / 'main.py').read_text() == 'new version'
    assert remote.read(f.app / '.oaw-deployment.json')['release_id'] == f.request['release']
    assert (f.data / 'document.txt').read_text() == 'user data before release'
    (f.data / 'document.txt').write_text('new user data')
    assert remote.transaction(f.request, rollback=True)['phase'] == 'rolled-back'
    assert (f.app / 'main.py').read_text() == 'old version'
    assert not (f.app / 'new-package/file.py').exists()
    assert (f.data / 'document.txt').read_text() == 'user data before release'
    assert next(f.b.glob('replaced-data-*/instances/*/data/document.txt')).read_text() == 'new user data'


def test_migration_failure_restores_real_files_and_data(installation, monkeypatch):
    f = installation
    def fail_migration(*args, **kwargs):
        if args[:2] == ('systemctl', 'start') and args[2].startswith('oaw-release-'):
            (f.data / 'document.txt').write_text('partial migration')
            raise ValueError('simulated migration failure')
        return f.run(*args, **kwargs)
    monkeypatch.setattr(remote, 'run', fail_migration)
    assert remote.transaction(f.request)['phase'] == 'failed-rolled-back'
    assert (f.app / 'main.py').read_text() == 'old version'
    assert (f.data / 'document.txt').read_text() == 'user data before release'
    assert next(f.b.glob('replaced-data-*/instances/*/data/document.txt')).read_text() == 'partial migration'
    assert not list(remote.UNITS.glob('oaw-release-*'))


def test_tampered_source_or_active_work_does_not_stop_services(installation):
    f = installation
    (f.app / 'main.py').write_text('unrecorded edit')
    assert remote.transaction(f.request)['phase'] == 'failed'
    assert f.commands == []
    (f.app / 'main.py').write_text('old version')
    with sqlite3.connect(f.data / 'database/world.sqlite3') as db:
        db.execute("INSERT INTO runs VALUES ('running')")
    db.close()
    with pytest.raises(ValueError, match='active runs'):
        remote.plan(f.request)


def test_tampered_backup_is_refused_before_restore(installation):
    f = installation
    remote.backup(f.request, f.b, f.manifest)
    (f.b / 'data.tar.gz').write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='Backup integrity'):
        remote.restore(f.request, f.b)
    assert (f.data / 'document.txt').read_text() == 'user data before release'


def test_old_interrupted_release_cannot_roll_back_a_newer_installation(installation):
    f = installation
    remote.save(f.b, 'recovery-required')
    value = remote.read(f.app / '.oaw-deployment.json')
    remote.write(f.app / '.oaw-deployment.json', {**value, 'release_id': 'newer', 'source_commit': 'c' * 40})
    assert remote.transaction(f.request, rollback=True)['phase'] == 'failed'
    assert f.commands == []


def test_upload_resume_and_target_pin(installation):
    f = installation
    remote.save(f.b, 'uploading')
    assert remote.dispatch({**f.request, 'action': 'init'})['phase'] == 'uploading'
    with pytest.raises(ValueError, match='another archive'):
        remote.dispatch({**f.request, 'action': 'init', 'archive_sha256': 'e' * 64})
    with pytest.raises(ValueError, match='target differs'):
        remote.dispatch({**f.request, 'action': 'status', 'service': 'other'})


def test_manual_rollback_requires_explicit_data_restore(installation, monkeypatch):
    f = installation
    monkeypatch.setattr(remote, 'unit_state', lambda _: 'inactive')
    with pytest.raises(ValueError, match='restore-data'):
        remote.dispatch({**f.request, 'action': 'rollback'})
    assert not (f.b / 'transaction.json').exists()


def test_background_submission_releases_lock_before_oneshot_startup(installation, monkeypatch):
    f = installation
    monkeypatch.setattr(remote, 'unit_state', lambda _: 'inactive')
    def systemd(*args, **kwargs):
        if args[0] == 'systemd-run' and '--no-block' not in args:
            raise TimeoutError('oneshot startup waits for transaction to acquire the held submission lock')
        return f.run(*args, **kwargs)
    monkeypatch.setattr(remote, 'run', systemd)
    assert remote.dispatch({**f.request, 'action': 'start'})['phase'] == 'queued'
    assert remote.read(f.b / 'transaction.json')['action'] == 'transaction'


def test_archive_roundtrip_and_changed_content_rejected(installation):
    f = installation
    archive_path = f.b / 'bundle.tar.gz'
    def pack():
        with tarfile.open(archive_path, 'w:gz') as tar:
            for p in f.stage.rglob('*'):
                if p.is_file():
                    tar.add(p, arcname=p.relative_to(f.stage).as_posix())
    pack()
    assert remote.extract_bundle(archive_path, f.b / 'extracted')['source_commit'] == 'b' * 40
    (f.stage / 'main.py').write_text('tampered archive member')
    pack()
    with pytest.raises(ValueError, match='integrity'):
        remote.extract_bundle(archive_path, f.b / 'rejected')


def test_migration_unit_preserves_existing_worker_boundary():
    original = '[Service]\nUser=oaw-alice\nProtectSystem=strict\nBindReadOnlyPaths=/profile\nEnvironmentFile=/secret\nExecStart=/old\nRestart=always\n'
    result = remote.worker_unit(original, '/app', '/helper.py', 'oaw-hosted')
    for line in ('User=oaw-alice', 'ProtectSystem=strict', 'BindReadOnlyPaths=/profile', 'EnvironmentFile=/secret'):
        assert line in result
    assert 'Type=oneshot' in result and 'Restart=no' in result and 'ExecStart=/old' not in result
    with pytest.raises(ValueError, match='hardened'):
        remote.worker_unit(original.replace('User=oaw-alice', 'User=root'), '/app', '/helper.py', 'oaw-hosted')


@pytest.mark.parametrize('conflict', [False, True])
def test_git_merge_leaves_developer_branch_and_dirty_files_untouched(tmp_path, monkeypatch, conflict):
    repo = tmp_path / 'repo'
    repo.mkdir()
    client.git(repo, 'init', '-b', 'dev')
    client.git(repo, 'config', 'user.email', 'test@example.invalid')
    client.git(repo, 'config', 'user.name', 'Release test')
    (repo / '.gitignore').write_text('.tmp/\n')
    (repo / 'shared').write_text('base')
    client.git(repo, 'add', '.')
    client.git(repo, 'commit', '-m', 'base')
    client.git(repo, 'branch', 'ali')
    if conflict:
        client.git(repo, 'switch', 'ali')
        (repo / 'shared').write_text('ali edit')
        client.git(repo, 'commit', '-am', 'ali change')
        client.git(repo, 'switch', 'dev')
    (repo / 'shared').write_text('dev edit')
    client.git(repo, 'commit', '-am', 'dev change')
    bare = tmp_path / 'origin.git'
    subprocess.run(['git', 'clone', '--bare', str(repo), str(bare)], check=True, capture_output=True)
    client.git(repo, 'remote', 'add', 'origin', str(bare))
    (repo / 'shared').write_text('unfinished user work')
    before = client.git(repo, 'rev-parse', 'HEAD')
    tree = repo / '.tmp/build'
    cfg = {'repository': str(repo), 'worktree': str(tree), 'branch': 'ali'}
    monkeypatch.setattr(client, 'build', lambda *a: 'built')
    args = SimpleNamespace(merge='origin/dev', resolved=False)
    if conflict:
        with pytest.raises(RuntimeError, match='Merge stopped'):
            client.prepare(cfg, args)
        assert client.git(tree, 'diff', '--name-only', '--diff-filter=U') == 'shared'
    else:
        assert client.prepare(cfg, args) == 'built'
        assert (tree / 'shared').read_text() == 'dev edit'
    assert client.git(repo, 'branch', '--show-current') == 'dev'
    assert client.git(repo, 'rev-parse', 'HEAD') == before
    assert (repo / 'shared').read_text() == 'unfinished user work'


def test_build_clean_handles_equivalent_newlines_but_preserves_real_edits(tmp_path):
    client.git(tmp_path, 'init')
    client.git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    client.git(tmp_path, 'config', 'user.name', 'Release test')
    client.git(tmp_path, 'config', 'core.autocrlf', 'true')
    source = tmp_path / 'generated.mjs'
    source.write_bytes(b'export const value = 1;\r\n')
    client.git(tmp_path, 'add', '.')
    client.git(tmp_path, 'commit', '-m', 'fixture')
    source.write_bytes(b'export const value = 1;\n')
    client.clean(tmp_path)
    assert client.git(tmp_path, 'status', '--porcelain') == ''
    source.write_bytes(b'export const value = 2;\n')
    with pytest.raises(ValueError, match='Worktree has changes'):
        client.clean(tmp_path)
    assert client.git(tmp_path, 'diff', '--cached', '--name-only') == ''
    assert b'value = 2' in source.read_bytes()


def test_routine_tests_follow_changed_behavior_without_unrelated_cloud_suites():
    changed = ['plugins/knowledge_base/src/oaw_knowledge_base/knowledge.py',
               'plugins/knowledge_base/tests/test_pipeline.py',
               'frontend/src/plugins/KnowledgeWorkspace.test.tsx']
    backend, frontend = client.test_scope(changed, routine=True)
    assert backend == {'plugins/knowledge_base/tests/test_pipeline.py'}
    assert frontend == {'src/plugins/KnowledgeWorkspace.test.tsx'}
    backend, _ = client.test_scope(['backend/hosting/policy.py'], routine=True)
    assert set(client.BACKEND_TESTS) <= backend


def test_routine_guard_rejects_core_contract_schema_and_dependency_changes():
    remote.routine_guard({'plugins/knowledge_base/src/oaw_knowledge_base/knowledge.py',
                          'plugins/knowledge_base/frontend/Knowledge.tsx'})
    for path in ['backend/hosting/policy.py', 'backend/deployments.py', 'open_agent_world/plugin_api.py',
                 'plugins/sample/oaw.pack.toml', 'plugins/sample/src/lifecycle.py', 'plugins/sample/src/plugin.py',
                 'plugins/sample/src/schema.py', 'plugins/sample/src/__init__.py']:
        with pytest.raises(ValueError, match='Routine release cannot change'):
            remote.routine_guard({path})


def test_routine_transaction_keeps_backup_and_verification_without_offline_migration(installation):
    f = installation
    f.manifest['routine'] = True
    f.manifest['files'].pop('main.py')
    f.manifest['files'].pop('new-package/file.py')
    f.manifest['files']['main.py'] = remote.digest(f.app / 'main.py')
    (f.stage / 'main.py').write_text('old version')
    remote.write(f.stage / 'release-manifest.json', f.manifest)
    result = remote.transaction(f.request)
    assert result['phase'] == 'complete'
    assert (f.b / 'backup.json').exists()
    assert not any(c[:2] == ('systemctl', 'start') and c[2].startswith('oaw-release-') for c in f.commands)
    assert any('verify' in c and '--entries-only' in c for c in f.commands)
    assert (f.data / 'document.txt').read_text() == 'user data before release'


def test_entry_verification_matches_gateway_current_route_selection():
    from backend.hosting.gateway import Gateway
    profiles = [SimpleNamespace(user_id='one', profile_id='old'), SimpleNamespace(user_id='one', profile_id='new'),
                SimpleNamespace(user_id='two', profile_id='default'), SimpleNamespace(user_id='two', profile_id='history'),
                SimpleNamespace(user_id='three', profile_id='unselected')]
    choices = {'one': 'new', 'three': 'removed'}
    gateway = SimpleNamespace(settings=SimpleNamespace(profiles=profiles),
                              deployments=SimpleNamespace(entries=lambda: choices))
    assert worker.entry_profiles(profiles, choices) == tuple(Gateway.profiles.fget(gateway).values())
    assert [p.profile_id for p in worker.entry_profiles(profiles, choices)] == ['new', 'default']


def test_npm_cache_is_invalidated_by_lockfile_or_node_change(tmp_path, monkeypatch):
    root = tmp_path / 'frontend'
    root.mkdir()
    (root / 'package.json').write_text('{}')
    (root / 'package-lock.json').write_text('first')
    for name in ('vitest/vitest.mjs', 'vite/bin/vite.js', 'typescript/bin/tsc'):
        p = root / 'node_modules' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('fixture')
    calls = []
    monkeypatch.setattr(client, 'npm', lambda *args: calls.append(args))
    monkeypatch.setattr(client, 'run', lambda *args, **kw: 'v22')
    client.prepare_npm(tmp_path)
    client.prepare_npm(tmp_path)
    assert len(calls) == 1
    (root / 'package-lock.json').write_text('second')
    client.prepare_npm(tmp_path)
    monkeypatch.setattr(client, 'run', lambda *args, **kw: 'v24')
    client.prepare_npm(tmp_path)
    assert len(calls) == 3
