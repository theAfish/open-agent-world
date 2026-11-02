"""Operator workflow tests: real Git/files/SQLite, with no cloud side effects."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from uuid import uuid4

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
    from backend.hosting.upgrades import refresh_manifest
    old = {'id': 'saved-release', 'layout': {'root': 'saved'}, 'panels': [{'card_id': 'kb', 'sections': ['old']}],
           'permissions': {'kb': ['read'], 'conversation': ['chat']}, 'plugin_access': {'kb': {'actions': ['read']}},
           'plugin_versions': {'kb': '1'}, 'runtime_settings': {'agent_runtime': 'configured'}}
    new = deepcopy(old)
    new.update(id='fresh-release', plugin_versions={'kb': '2'}, runtime_settings={})
    new['plugin_access']['kb']['actions'].append('graph')
    new['panels'][0]['sections'].append('graph')
    result, changed = refresh_manifest(old, new)
    assert result['id'] == old['id'] and result['runtime_settings'] == old['runtime_settings']
    assert result['plugin_access'] == new['plugin_access'] and changed == ['kb']
    for key, value in [('layout', {}), ('permissions', {'kb': ['read'], 'conversation': ['terminal']}),
                       ('plugin_access', {'kb': {}, 'other': {}})]:
        altered = {**new, key: value}
        with pytest.raises(ValueError):
            refresh_manifest(old, altered)


def test_multiple_workspace_upgrade_preserves_each_layout_and_scope():
    from backend.hosting.upgrades import refresh_manifest
    workspaces = [{'legion_id': node, 'name': node, 'layout': {'root': node},
        'panels': [{'card_id': node, 'sections': ['read']}], 'permissions': {node: ['read']},
        'plugin_access': {node: {'actions': ['read']}}} for node in ('one', 'two')]
    old = {**workspaces[0], 'id': 'stable', 'workspaces': workspaces,
        'panels': [p for w in workspaces for p in w['panels']],
        'permissions': {k: v for w in workspaces for k, v in w['permissions'].items()},
        'plugin_access': {k: v for w in workspaces for k, v in w['plugin_access'].items()}, 'plugin_versions': {'pack': '1'}}
    new = deepcopy(old)
    new['plugin_versions']['pack'] = '2'
    new['workspaces'][1]['plugin_access']['two']['actions'].append('search')
    result, _ = refresh_manifest(old, new)
    assert result['id'] == 'stable' and result['workspaces'][1]['layout'] == old['workspaces'][1]['layout']
    assert result['workspaces'][1]['plugin_access']['two']['actions'] == ['read', 'search']
    altered = deepcopy(new)
    altered['workspaces'].reverse()
    with pytest.raises(ValueError, match='workspace scope or order'):
        refresh_manifest(old, altered)
    altered = deepcopy(new)
    altered['workspaces'][1]['layout'] = {}
    with pytest.raises(ValueError, match='layout changed'):
        refresh_manifest(old, altered)


@pytest.mark.parametrize('configured_factory', [False, True])
def test_worker_migration_preserves_cli_config_factory_and_report(tmp_path, monkeypatch, configured_factory):
    from backend.hosting import upgrades
    from backend.hosting.worker import create_worker_services
    config_path = tmp_path / 'worker.json'
    profile_id = str(uuid4())
    config_path.write_text(json.dumps({'profile_id': profile_id, 'user_id': str(uuid4()),
        'application_id': 'existing-application', 'mode': 'runtime', 'public_origin': 'https://oaw.example',
        'secret': 'ab' * 32, 'data_root': 'data', 'agent_runtime': 'google.adk'}))
    factory = object()
    if configured_factory:
        from backend.cloud.aliyun import hosted
        monkeypatch.setenv('OAW_HOSTED_SERVICES_FACTORY', 'backend.cloud.aliyun.hosted:create_hosted_services')
        monkeypatch.setattr(hosted, 'create_hosted_services', factory)
    else:
        monkeypatch.delenv('OAW_HOSTED_SERVICES_FACTORY', raising=False)
    report = {'profile': profile_id, 'ready': True, 'changed_contracts': ['knowledge-card']}
    async def migrate_profile(config, *, services_factory):
        assert config.data_root == (tmp_path / 'data').resolve()
        assert config.profile_id == profile_id and config.application_id == 'existing-application'
        assert config.agent_runtime == 'google.adk'
        assert services_factory is (factory if configured_factory else create_worker_services)
        return report
    monkeypatch.setattr(upgrades, 'migrate_profile', migrate_profile)
    assert asyncio.run(worker.migrate(config_path)) is report


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
    # These transaction tests model an independently approved manual release.
    # Actual operator ownership and allowlist rejection are exercised separately.
    monkeypatch.setattr(remote, 'authorize', lambda request, **kw: {
        'archive_sha256': kw.get('archive_sha256', request.get('archive_sha256')),
        'source_commit': kw.get('source_commit') or request.get('source_commit') or 'b' * 40})
    monkeypatch.setattr(remote, 'trusted_operator', lambda: Path(remote.__file__))
    monkeypatch.setattr(remote, 'trusted_worker', lambda: ROOT / 'deploy/aliyun/release_worker.py')
    # Model the registered nonroot execution boundary here. The concrete unit,
    # identity, bundle and verifier restrictions have separate security tests.
    monkeypatch.setattr(remote, 'require_execution', lambda request, **kw:
                        {'environment': 'preview', 'sandbox_root': '/reviewed-execution-root'})
    monkeypatch.setattr(remote, 'install_bundle', lambda request, directory: None)
    monkeypatch.setattr(remote, 'restore_bundle', lambda request, directory: None)
    monkeypatch.setattr(remote, 'checked_worker_identity', lambda original: 'oaw-user')
    monkeypatch.setattr(remote, 'verify_release', lambda request, config, entries_only=False:
                        remote.run('verify', *(['--entries-only'] if entries_only else [])))
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
        '[Unit]\nAfter=network.target\n[Service]\nUser=oaw-user\nProtectSystem=strict\nRootDirectory=/reviewed-execution-root\n'
        'BindReadOnlyPaths=/profile\nEnvironmentFile=/private.env\nExecStart=/old-command\nRestart=always\n')
    monkeypatch.setattr(remote, 'UNITS', units)
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
    args = SimpleNamespace(merge='origin/dev', resolved=False)
    if conflict:
        with pytest.raises(RuntimeError, match='Merge stopped'):
            client.sync(cfg, args)
        assert client.git(tree, 'diff', '--name-only', '--diff-filter=U') == 'shared'
    else:
        commit = client.sync(cfg, args)
        assert client.git(repo, 'rev-parse', 'ali') == commit
        assert client.git(repo, 'rev-parse', 'origin/ali') == commit
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


def test_documentation_edits_do_not_select_unrelated_pack_backend_suites():
    backend, _ = client.test_scope(['plugins/matcreator/README.md', 'plugins/xrd/docs/install.md',
                                    'plugins/knowledge_base/frontend/ProjectWorkspace.tsx'])
    assert not any(path.startswith('plugins/') for path in backend)
    backend, _ = client.test_scope(['plugins/xrd/src/oaw_xrd/workbench.json'])
    assert 'plugins/xrd/tests' in backend


@pytest.mark.parametrize('mutation', ['root_version', 'dependency_version', 'wheel_hash', 'python_version'])
def test_release_allows_only_virtual_project_version_changes(installation, mutation):
    f = installation
    project = '[project]\nname="open-agent-world-backend"\nversion="0.1.0"\ndependencies=[]\n'
    lock = ('version=1\nrequires-python=">=3.11"\n[[package]]\n'
            'name="open-agent-world-backend"\nversion="0.1.0"\nsource={virtual="."}\n'
            '[[package]]\nname="dependency"\nversion="1.0"\n'
            'wheels=[{url="https://example.invalid/a.whl",hash="sha256:original"}]\n')
    (f.app / 'backend/pyproject.toml').write_text(project)
    (f.app / 'backend/uv.lock').write_text(lock)
    (f.stage / 'backend/pyproject.toml').write_text(project.replace('0.1.0', '0.6.0'))
    altered = lock.replace('0.1.0', '0.6.0')
    if mutation == 'dependency_version':
        altered = altered.replace('version="1.0"', 'version="2.0"')
    elif mutation == 'wheel_hash':
        altered = altered.replace('sha256:original', 'sha256:changed')
    elif mutation == 'python_version':
        altered = altered.replace('>=3.11', '>=3.12')
    (f.stage / 'backend/uv.lock').write_text(altered)
    old = remote.read(f.app / '.oaw-deployment.json')
    for name in ('backend/pyproject.toml', 'backend/uv.lock'):
        old['source_files'][name] = remote.digest(f.app / name)
        f.manifest['files'][name] = remote.digest(f.stage / name)
    remote.write(f.app / '.oaw-deployment.json', old)
    f.manifest['baseline_receipt'] = remote.digest(f.app / '.oaw-deployment.json')
    remote.write(f.stage / 'release-manifest.json', f.manifest)
    if mutation == 'root_version':
        assert remote.plan(f.request)['offline_migration']
    else:
        with pytest.raises(ValueError, match='uv.lock dependencies changed'):
            remote.plan(f.request)


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


@pytest.fixture
def portable_build(tmp_path, monkeypatch):
    """Real Git and archives; only npm execution is replaced by generated output."""
    repo = tmp_path / 'checkout'
    repo.mkdir()
    client.git(repo, 'init', '-b', 'feature')
    client.git(repo, 'config', 'user.email', 'test@example.invalid')
    client.git(repo, 'config', 'user.name', 'Build test')
    (repo / '.gitignore').write_text('frontend/dist/\n.tmp/\n')
    (repo / 'main.py').write_text('reviewed source\n')
    (repo / 'frontend').mkdir()
    client.git(repo, 'add', '.')
    client.git(repo, 'commit', '-m', 'source')
    plan = tmp_path / 'checks.json'
    client.write(plan, {'backend': [], 'frontend': []})
    operations = []
    original_git = client.git
    def git(path, *args, **kwargs):
        operations.append(args)
        assert args[0] not in {'merge', 'fetch', 'push', 'update-ref'}, 'A build must not synchronize Git'
        return original_git(path, *args, **kwargs)
    monkeypatch.setattr(client, 'git', git)
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline build must not read deployment configuration or contact a server')
    monkeypatch.setattr(client, 'remote', forbidden)
    monkeypatch.setattr(client, 'config', forbidden)
    monkeypatch.setattr(client, 'target_id', forbidden)
    monkeypatch.setattr(client, 'prepare_npm', lambda _: None)
    def npm(tree, *args):
        assert args == ('run', 'build')
        dist = tree / 'frontend/dist'
        dist.mkdir(parents=True)
        (dist / 'index.html').write_text('<p>compiled frontend</p>')
    monkeypatch.setattr(client, 'npm', npm)
    output = tmp_path / 'artifacts'
    client.main(['build', '--source', str(repo), '--output', str(output), '--python', sys.executable,
                 '--test-plan', str(plan)])
    record = client.read(output / 'latest.json')
    directory = output / record['release']
    return SimpleNamespace(repo=repo, output=output, directory=directory, record=record,
                           operations=operations, original_git=original_git)


def test_portable_build_is_offline_target_free_and_git_read_only(portable_build, tmp_path):
    f = portable_build
    manifest = client.verify_artifact(f.directory, f.record)
    assert manifest['version'] == 2 and manifest['kind'] == 'oaw-build'
    assert manifest['source_commit'] == f.original_git(f.repo, 'rev-parse', 'HEAD')
    assert {'main.py', 'frontend/dist/index.html'} <= manifest['files'].keys()
    assert not {'operator_files', 'baseline_commit', 'baseline_receipt', 'target'} & manifest.keys()
    assert not {'target', 'uploaded', 'baseline_receipt'} & f.record.keys()
    assert not any(name.startswith('operator/') for name in manifest['files'])
    assert f.original_git(f.repo, 'branch', '--show-current') == 'feature'
    assert f.original_git(f.repo, 'status', '--porcelain') == ''


def test_prepare_preserves_dirty_developer_checkout_in_detached_worktree(portable_build, monkeypatch):
    f = portable_build
    source = f.repo / 'main.py'
    source.write_text('unfinished developer edit')
    tree = f.repo / '.tmp/build'
    cfg = {'repository': str(f.repo), 'worktree': str(tree), 'branch': 'feature'}
    def build(settings, checkout, args):
        assert checkout == tree and client.git(checkout, 'branch', '--show-current') == ''
        assert (checkout / 'main.py').read_text() == 'reviewed source\n'
        return 'fixed artifact'
    monkeypatch.setattr(client, 'build', build)
    assert client.prepare(cfg, SimpleNamespace()) == 'fixed artifact'
    assert source.read_text() == 'unfinished developer edit'
    assert f.original_git(f.repo, 'branch', '--show-current') == 'feature'


def test_artifact_corruption_is_rejected_before_any_deployment_call(portable_build, monkeypatch):
    f = portable_build
    (f.directory / 'bundle.tar.gz').write_bytes(b'altered download')
    calls = []
    monkeypatch.setattr(client, 'remote', lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match='archive changed'):
        client.deploy({'output': str(f.output)}, SimpleNamespace(release=None, artifact=f.directory, check=True))
    assert calls == []
    assert not (f.directory / 'deployment.json').exists()


def test_deploy_binds_verified_artifact_once_without_rebuilding(portable_build, monkeypatch):
    f = portable_build
    original_record = (f.directory / 'release.json').read_bytes()
    original_archive = client.sha(f.directory / 'bundle.tar.gz')
    cfg = {'output': str(f.output), 'app': '/opt/app', 'hosting': '/srv/hosting', 'releases': '/opt/releases',
           'service': 'oaw-hosted', 'ssh_target': 'root@example.invalid', 'identity_file': 'test-key'}
    monkeypatch.setattr(client, 'target_id', lambda value: json.dumps(client.target(value), sort_keys=True))
    calls, uploads = [], []
    uploaded = False
    def remote(cfg, action, release=None, **kwargs):
        nonlocal uploaded
        calls.append((action, release, kwargs))
        if action == 'status' and release is None:
            return {'commit': 'a' * 40, 'receipt': 'c' * 64}
        if action == 'stage':
            uploaded = True
        return {'phase': {'init': 'staged' if uploaded else 'uploading', 'stage': 'staged',
                          'status': 'staged', 'check': 'ready'}[action], 'unit_active': False}
    monkeypatch.setattr(client, 'remote', remote)
    monkeypatch.setattr(client, 'run', lambda argv, **kw: uploads.append(argv))
    def no_build(*args, **kwargs):
        raise AssertionError('Deployment must not build, merge or inspect source Git')
    monkeypatch.setattr(client, 'build', no_build)
    monkeypatch.setattr(client, 'git', no_build)
    args = SimpleNamespace(release=None, artifact=f.directory, check=True)
    client.deploy(cfg, args)
    binding = client.read(f.directory / 'deployment.json')
    assert binding['baseline_commit'] == 'a' * 40 and binding['baseline_receipt'] == 'c' * 64
    assert calls[0] == ('status', None, {})
    assert next(item for item in calls if item[0] == 'init')[2]['source_commit'] == f.record['commit']
    assert len(uploads) == 1 and uploads[0][0] == 'scp'
    calls.clear()
    client.deploy(cfg, args)
    assert calls[0][0] == 'init' and calls[0][2]['archive_sha256'] == original_archive
    assert not any(release is None for _, release, _ in calls)
    assert len(uploads) == 1
    assert (f.directory / 'release.json').read_bytes() == original_record
    assert client.sha(f.directory / 'bundle.tar.gz') == original_archive
    calls.clear()
    with pytest.raises(ValueError, match='binding differs'):
        client.deploy({**cfg, 'app': '/opt/other-app'}, args)
    assert calls == []


@pytest.mark.parametrize('environment,path', [
    ('production', client.TRUSTED_OPERATOR), ('preview', client.PREVIEW_OPERATOR)])
def test_remote_transport_sends_only_data_to_an_installed_controller(monkeypatch, environment, path):
    calls = []
    monkeypatch.setattr(client, 'run', lambda argv, **kwargs: calls.append((argv, kwargs)) or '{"phase":"ready"}')
    cfg = {'app': '/opt/app', 'hosting': '/srv/hosting', 'releases': '/opt/releases', 'service': 'oaw-hosted',
           'ssh_target': 'root@example.invalid', 'identity_file': '/private/key', 'environment': environment}
    assert client.remote(cfg, 'status')['phase'] == 'ready'
    argv, kwargs = calls[0]
    assert argv[-1] == '/usr/bin/python3 -I -S ' + path
    assert json.loads(kwargs['input']) == {**client.target(cfg), 'action': 'status', 'purpose': 'trusted_manual'}
    assert 'exec(' not in kwargs['input'] and 'payload' not in kwargs['input']


def test_client_rejects_unregistered_operator_before_ssh(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('An invalid operator selection reached SSH')
    monkeypatch.setattr(client, 'run', unexpected)
    cfg = {'app': '/opt/app', 'hosting': '/srv/hosting', 'releases': '/opt/releases', 'service': 'oaw-hosted',
           'ssh_target': 'root@example.invalid', 'identity_file': '/private/key',
           'environment': '/tmp/pr-controller.py'}
    with pytest.raises(ValueError, match='environment'):
        client.remote(cfg, 'status')
    cfg.pop('environment')
    assert client.target_id(cfg) == client.target_id({**cfg, 'environment': 'production'})
    assert client.target_id(cfg) != client.target_id({**cfg, 'environment': 'preview'})


def test_seed_uses_fixed_artifact_without_baseline_or_service_start(portable_build, monkeypatch):
    f = portable_build
    cfg = {'environment': 'preview', 'app': '/opt/preview', 'hosting': '/srv/preview',
           'releases': '/opt/preview-releases', 'service': 'oaw-preview',
           'ssh_target': 'root@example.invalid', 'identity_file': 'test-key'}
    record = client.read(f.directory / 'release.json')
    calls, uploads = [], []
    def remote(cfg, action, release=None, **pinned):
        calls.append((action, release, pinned))
        return {'phase': 'uploading' if action == 'init' else 'seeded'}
    monkeypatch.setattr(client, 'remote', remote)
    monkeypatch.setattr(client, 'run', lambda argv, **kwargs: uploads.append(argv))
    monkeypatch.setattr(client, 'build', lambda *a, **k: pytest.fail('Seed attempted to rebuild'))
    client.seed(cfg, SimpleNamespace(artifact=f.directory))
    assert [call[0] for call in calls] == ['init', 'seed']
    assert all(call[1:] == (record['release'], {'source_commit': record['commit'],
               'archive_sha256': record['archive_sha256']}) for call in calls)
    assert len(uploads) == 1 and uploads[0][0] == 'scp'
    assert client.sha(f.directory / 'bundle.tar.gz') == record['archive_sha256']
    assert not (f.directory / 'deployment.json').exists()
    calls.clear()
    with pytest.raises(ValueError, match='only supported for Preview'):
        client.seed({**cfg, 'environment': 'production'}, SimpleNamespace(artifact=f.directory))
    (f.directory / 'bundle.tar.gz').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='archive changed'):
        client.seed(cfg, SimpleNamespace(artifact=f.directory))
    assert calls == []
