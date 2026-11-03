"""Execution-boundary regressions; host systemd acceptance is a separate check."""
import ast
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy/aliyun' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote = load('release_remote')
worker = load('release_worker')
verifier = load('release_verify')


@pytest.fixture
def registration():
    return {'version': 1, 'environment': 'preview',
        'target': {'app': '/opt/oaw-preview', 'hosting': '/srv/oaw-preview',
                   'releases': '/var/lib/oaw-preview-releases', 'service': 'oaw-preview'},
        'sandbox_root': '/opt/oaw-preview-execution', 'python': '/usr/bin/python3.12',
        'build_user': 'oaw-preview-build', 'verify_user': 'oaw-preview-verify'}


@pytest.fixture
def release_request(registration):
    return {**registration['target'], 'release': '20261101T010000Z-' + 'a' * 12 + '-12345678'}


def test_privileged_operator_and_verifier_have_only_stdlib_imports():
    for name in ('release_remote', 'release_verify'):
        tree = ast.parse((ROOT / 'deploy/aliyun' / (name + '.py')).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module]
            else:
                continue
            assert all(name.split('.')[0] in sys.stdlib_module_names or name == '__future__' for name in names)


def test_application_helper_rejects_root_before_adding_source_to_import_path(tmp_path, monkeypatch):
    source = tmp_path / 'artifact'
    source.mkdir()
    monkeypatch.setattr(os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(sys, 'argv', ['release_worker.py', 'prepare', '--source', str(source)])
    previous = list(sys.path)
    with pytest.raises(ValueError, match='cannot run as root'):
        worker.main()
    assert sys.path == previous


def test_standalone_verifier_ignores_application_and_cwd_import_hooks(tmp_path):
    (tmp_path / 'backend').mkdir()
    (tmp_path / 'backend/__init__.py').write_text("raise RuntimeError('artifact code executed')")
    (tmp_path / 'http.py').write_text("raise RuntimeError('cwd hook executed')")
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'deploy/aliyun/release_verify.py'), '--help'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--snapshot' in result.stdout


def test_build_and_verify_are_distinct_readonly_rootdirectory_services(registration, release_request, monkeypatch):
    monkeypatch.setattr(remote, 'trusted_worker', lambda: Path('/opt/oaw-release-operator-preview/release_worker.py'))
    monkeypatch.setattr(remote, 'trusted_verifier', lambda: Path('/opt/oaw-release-operator-preview/release_verify.py'))
    _, build = remote.sandbox_command(release_request, registration, 'prepare', writable=Path('/private/release/scratch'),
        readonly=((Path('/private/release/stage'), '/workspace/source'),))
    _, verify = remote.sandbox_command(release_request, registration, 'verify',
        readonly=((Path('/private/release/verification.json'), '/workspace/verification.json'),))
    for command, user in ((build, registration['build_user']), (verify, registration['verify_user'])):
        assert '--property=User=' + user in command
        assert '--property=Group=' + user in command
        assert '--property=RootDirectory=' + registration['sandbox_root'] in command
        for property in ('ProtectSystem=strict', 'NoNewPrivileges=yes', 'PrivateNetwork=yes',
                         'CapabilityBoundingSet=', 'KillMode=control-group'):
            assert '--property=' + property in command
        assert registration['python'] in command and '-I' in command
        assert not any('/backend/.venv' in value or '/srv/oaw-preview' in value for value in command)
    assert '--property=BindPaths=' + str(Path('/private/release/scratch')) + ':/workspace/scratch' in build
    assert not any(value.startswith('--property=BindPaths=') for value in verify)
    assert '/workspace/release_verify.py' in verify
    assert '--source' not in verify


def test_failed_prepare_stops_entire_unit_before_output_can_be_promoted(registration, release_request, monkeypatch):
    monkeypatch.setattr(remote, 'trusted_worker', lambda: Path('/opt/oaw-release-operator-preview/release_worker.py'))
    calls = []
    def run(*command, **kwargs):
        calls.append(command)
        if command[0] == 'systemd-run':
            raise ValueError('preparation failed')
        return 'loaded' if '--property=LoadState' in command else ''
    monkeypatch.setattr(remote, 'run', run)
    with pytest.raises(ValueError, match='preparation failed'):
        remote.run_sandbox(release_request, registration, 'prepare', [], log=Path('/private/log'))
    assert calls[-1][:2] == ('systemctl', 'stop')
    assert calls[-1][2].endswith('-prepare.service')


@pytest.mark.parametrize('active', ['inactive', 'active'])
def test_collected_sandbox_requires_inactive_state_before_promoting_output(registration, release_request, monkeypatch, active):
    monkeypatch.setattr(remote, 'trusted_worker', lambda: Path('/opt/oaw-release-operator-preview/release_worker.py'))
    calls=[]
    def run(*command, **kwargs):
        calls.append(command)
        if '--property=LoadState' in command:
            return 'not-found'
        if '--property=ActiveState' in command:
            return active
        if command[:2] == ('systemctl', 'stop'):
            pytest.fail('Collected transient units cannot be stopped again')
        return ''
    monkeypatch.setattr(remote, 'run', run)
    if active == 'inactive':
        remote.run_sandbox(release_request, registration, 'prepare', [], log=Path('/private/log'))
    else:
        with pytest.raises(ValueError, match='collected release sandbox must be inactive'):
            remote.run_sandbox(release_request, registration, 'prepare', [], log=Path('/private/log'))


def test_execution_registration_rejects_runtime_approval_shortcuts(registration, release_request, tmp_path, monkeypatch):
    registration['broker_reviewed'] = True
    monkeypatch.setattr(remote, 'trusted_file', lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(remote, 'read', lambda _: registration)
    with pytest.raises(ValueError, match='Invalid operator execution registration'):
        remote.execution_config(release_request)


def test_production_deploy_refuses_unclosed_provisioner_before_service_operations(registration, release_request, monkeypatch):
    registration['environment'] = 'production'
    monkeypatch.setattr(remote, 'execution_config', lambda _: registration)
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: pytest.fail('production service operation before closure'))
    assert remote.require_execution(release_request) is registration
    with pytest.raises(ValueError, match='complete import closure'):
        remote.require_execution(release_request, deploying=True)


@pytest.mark.parametrize('load_state', ['loaded', 'error'])
def test_preview_refuses_an_enabled_or_uninspectable_application_provisioner(registration, release_request, monkeypatch, load_state):
    monkeypatch.setattr(remote, 'execution_config', lambda _: registration)
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: load_state)
    with pytest.raises(ValueError, match='absent or masked'):
        remote.require_execution(release_request, deploying=True)


def test_preview_refuses_a_masked_but_still_running_provisioner(registration, release_request, monkeypatch):
    monkeypatch.setattr(remote, 'execution_config', lambda _: registration)
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: 'masked' if '--property=LoadState' in a else 'active')
    with pytest.raises(ValueError, match='must not be running'):
        remote.require_execution(release_request, deploying=True)


@pytest.mark.parametrize('load_state', ['masked', 'not-found'])
def test_preview_accepts_only_an_inactive_absent_or_masked_provisioner(registration, release_request, monkeypatch, load_state):
    monkeypatch.setattr(remote, 'execution_config', lambda _: registration)
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: load_state if '--property=LoadState' in a else 'inactive')
    assert remote.require_execution(release_request, deploying=True) is registration


def test_preview_resume_never_restarts_the_root_provisioner(registration, release_request, monkeypatch):
    monkeypatch.setattr(remote, 'paths', lambda _: (Path('/app'), Path('/hosting'), Path('/releases')))
    monkeypatch.setattr(remote, 'profiles', lambda _: [('profile', Path('/data'))])
    calls = []
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: calls.append(a))
    remote.resume(release_request, config=registration)
    assert any(release_request['service'] + '-gateway.service' in command for command in calls)
    assert not any('provisioner' in argument for command in calls for argument in command)


def test_gateway_socket_is_explicitly_stopped_until_final_resume(registration, release_request, monkeypatch):
    monkeypatch.setattr(remote, 'paths', lambda _: (Path('/app'), Path('/hosting'), Path('/releases')))
    monkeypatch.setattr(remote, 'profiles', lambda _: [('profile', Path('/data'))])
    calls = []
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: calls.append(a))
    remote.stop(release_request, config=registration)
    stopped = calls[-1]
    assert stopped[:2] == ('systemctl', 'stop')
    assert release_request['service'] + '-gateway.socket' in stopped
    calls.clear()
    remote.resume(release_request, gateway=False, config=registration)
    assert all('gateway' not in argument for command in calls for argument in command)
    remote.resume(release_request, config=registration)
    assert calls[-1] == ('systemctl', 'start', release_request['service'] + '-gateway.socket',
                        release_request['service'] + '-gateway.service')


def test_untrusted_prepared_hardlink_is_rejected_before_privileged_freeze(tmp_path):
    root = tmp_path / 'bundle'
    root.mkdir()
    original = root / 'original'
    original.write_text('untrusted output')
    os.link(original, root / 'alias')
    with pytest.raises(ValueError, match='links or special files'):
        remote.bundle_tree(root)


def test_preparation_only_mutates_a_private_clone_before_immutable_promotion(tmp_path, registration, release_request, monkeypatch):
    app = tmp_path / 'app'
    current = app / 'backend/.venv/oaw-runtime-bundle'
    current.mkdir(parents=True)
    (current / 'library.py').write_text('installed')
    release = tmp_path / 'release'
    (release / 'stage').mkdir(parents=True)
    monkeypatch.setattr(remote, 'paths', lambda _: (app, tmp_path / 'hosting', tmp_path / 'releases'))
    monkeypatch.setattr(remote, 'release_dir', lambda _: release)
    monkeypatch.setattr(remote, 'trusted_metadata', lambda *a, **kw: None)
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=501, pw_gid=501)))
    monkeypatch.setattr(os, 'chown', lambda *a, **kw: None, raising=False)
    def prepare(*args, writable, readonly, **kwargs):
        assert readonly == ((release / 'stage', '/workspace/source'), (readonly[1][0], '/workspace/previous'))
        assert (writable / 'bundle/library.py').read_text() == 'installed'
        (writable / 'bundle/library.py').write_text('prepared')
        assert (current / 'library.py').read_text() == 'installed'
    monkeypatch.setattr(remote, 'run_sandbox', prepare)
    remote.prepare_bundle(release_request, registration)
    assert (current / 'library.py').read_text() == 'installed'
    assert (release / 'prepared-runtime-bundle/library.py').read_text() == 'prepared'
    assert not list(release.glob('prepare-*'))


def test_verification_gets_only_minimal_snapshot_and_removes_it_on_failure(tmp_path, registration, release_request, monkeypatch):
    hosting = tmp_path / 'hosting'
    (hosting / 'control').mkdir(parents=True)
    release = tmp_path / 'release'
    release.mkdir()
    profile = {'profile_id': '00000000-0000-4000-8000-000000000001',
               'user_id': '00000000-0000-4000-8000-000000000002', 'mode': 'builder', 'secret': 'ab' * 32}
    row = {**profile, 'upstream': 'unix:///run/oaw-preview/' + profile['profile_id'] + '.sock',
           'unrelated_private_value': 'must stay hidden'}
    remote.write(hosting / 'control/hosting.json', {'profiles': [row]})
    monkeypatch.setattr(remote, 'paths', lambda _: (tmp_path / 'app', hosting, tmp_path / 'releases'))
    monkeypatch.setattr(remote, 'release_dir', lambda _: release)
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_gid=502)))
    monkeypatch.setattr(os, 'chown', lambda *a, **kw: None, raising=False)
    def verify(*args, readonly, **kwargs):
        assert remote.read(readonly[0][0]) == {'profiles': [profile]}
        assert readonly[1][0] == Path('/run/oaw-preview')
        assert all(hosting not in path.parents and path != hosting for path, _ in readonly)
        raise ValueError('worker refused bootstrap')
    monkeypatch.setattr(remote, 'run_sandbox', verify)
    with pytest.raises(ValueError, match='refused bootstrap'):
        remote.verify_release(release_request, registration)
    assert not (release / 'verification-input.json').exists()


def test_runtime_bundle_promotion_and_rollback_retain_previous_bytes(tmp_path, release_request, monkeypatch):
    app = tmp_path / 'app'
    bundle = app / 'backend/.venv/oaw-runtime-bundle'
    bundle.mkdir(parents=True)
    (bundle / 'library.py').write_text('previous')
    release = tmp_path / 'release'
    prepared = release / 'prepared-runtime-bundle'
    prepared.mkdir(parents=True)
    (prepared / 'library.py').write_text('prepared')
    monkeypatch.setattr(remote, 'paths', lambda _: (app, tmp_path / 'hosting', tmp_path / 'releases'))
    # Windows cannot represent Linux UID/mode ownership; retain real tree/link checks.
    monkeypatch.setattr(remote, 'trusted_metadata', lambda *a, **kw: None)
    remote.write(release / 'backup.json', {})
    remote.install_bundle(release_request, release)
    assert (bundle / 'library.py').read_text() == 'prepared'
    remote.restore_bundle(release_request, release)
    assert (bundle / 'library.py').read_text() == 'previous'
    assert next(release.glob('replaced-runtime-bundle-*/library.py')).read_text() == 'prepared'


def test_runtime_bundle_rollback_rejects_corruption_before_replacing_installed_bytes(tmp_path, release_request, monkeypatch):
    app = tmp_path / 'app'
    bundle = app / 'backend/.venv/oaw-runtime-bundle'
    bundle.mkdir(parents=True)
    (bundle / 'library.py').write_text('previous')
    release = tmp_path / 'release'
    prepared = release / 'prepared-runtime-bundle'
    prepared.mkdir(parents=True)
    (prepared / 'library.py').write_text('installed')
    monkeypatch.setattr(remote, 'paths', lambda _: (app, tmp_path / 'hosting', tmp_path / 'releases'))
    monkeypatch.setattr(remote, 'trusted_metadata', lambda *a, **kw: None)
    remote.write(release / 'backup.json', {})
    remote.install_bundle(release_request, release)
    (release / 'previous-runtime-bundle/library.py').write_text('corrupted backup')
    with pytest.raises(ValueError, match='Runtime bundle backup integrity'):
        remote.restore_bundle(release_request, release)
    assert (bundle / 'library.py').read_text() == 'installed'
    assert not list(release.glob('replaced-runtime-bundle-*'))


def test_verifier_worker_assertion_matches_existing_authentication_protocol():
    from backend.hosting.worker import WorkerIdentityProvider
    profile = {'profile_id': '00000000-0000-4000-8000-000000000001',
               'user_id': '00000000-0000-4000-8000-000000000002', 'secret': 'ab' * 32}
    provider = WorkerIdentityProvider(profile['secret'], profile['profile_id'], profile['user_id'])
    scope = {'headers': [(b'authorization', ('Bearer ' + verifier.worker_token(profile)).encode())]}
    identity = asyncio.run(provider.authenticate(scope))
    assert identity is not None and identity.subject == profile['user_id']


def test_verifier_rejects_worker_that_admits_anonymous_requests(tmp_path, monkeypatch):
    snapshot = tmp_path / 'verification.json'
    snapshot.write_text(json.dumps({'profiles': [{'profile_id': 'profile'}]}))
    monkeypatch.setattr(verifier, 'request_worker', lambda *args: (200, b'{}'))
    with pytest.raises(ValueError, match='without gateway identity'):
        verifier.verify(snapshot)


def test_migration_rejects_duplicate_or_commented_nonroot_identity():
    original = '[Service]\nUser=root\n# User=oaw-pretend\nProtectSystem=strict\nBindReadOnlyPaths=/profile\nExecStart=/old\n'
    with pytest.raises(ValueError, match='hardened'):
        remote.worker_unit(original, '/app', '/helper.py', 'oaw-preview')
    with pytest.raises(ValueError, match='hardened'):
        remote.worker_unit(original.replace('# User=oaw-pretend', 'User=oaw-pretend'), '/app', '/helper.py', 'oaw-preview')


def test_migration_rejects_a_nonroot_name_that_resolves_to_root(monkeypatch):
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=0, pw_gid=501)))
    with pytest.raises(ValueError, match='nonroot UID'):
        remote.checked_worker_identity('[Service]\nUser=oaw-root-alias\n')


def test_migration_maps_trusted_helper_into_existing_rootdirectory_and_rejects_hooks():
    original = ('[Service]\nUser=oaw-profile\nRootDirectory=/opt/independent-runtime\nProtectSystem=strict\n'
                'BindReadOnlyPaths=/srv/preview/profile:/var/lib/oaw-preview-profile\nExecStart=/old\n')
    result = remote.worker_unit(original, '/opt/preview', '/opt/operator/release_worker.py', 'oaw-preview', preview=True)
    assert 'BindReadOnlyPaths=/opt/operator/release_worker.py:/workspace/release_worker.py\n' in result
    assert ' -I /workspace/release_worker.py migrate ' in result
    assert '--environment preview' in result
    with pytest.raises(ValueError, match='hardened'):
        remote.worker_unit(original + 'ExecStartPre=+/opt/preview/artifact.py\n', '/opt/preview', '/helper', 'oaw-preview')


def test_preview_preparation_loads_only_core_registry(tmp_path, monkeypatch):
    from backend.plugins import loader
    calls = []
    def registry(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(load_failures=[])
    monkeypatch.setattr(loader, 'load_plugin_registry', registry)
    result = worker.prepare(tmp_path / 'source', tmp_path / 'previous', tmp_path / 'scratch', tmp_path / 'bundle', preview=True)
    assert result == {'prepared_packs': [], 'composition': 'preview-core'}
    assert calls == [{'data_root': tmp_path / 'scratch/probe', 'core_only': True,
                      'load_installed': False, 'load_entry_points': False}]


def test_preview_migration_uses_preview_factory_even_with_an_environment_override(tmp_path, monkeypatch):
    from backend.hosting import preview, upgrades
    config = tmp_path / 'worker.json'
    config.write_text(json.dumps({'profile_id': '00000000-0000-4000-8000-000000000001',
        'user_id': '00000000-0000-4000-8000-000000000002', 'application_id': 'preview', 'mode': 'builder',
        'public_origin': 'https://preview.example', 'secret': 'ab' * 32, 'data_root': 'data'}))
    monkeypatch.setenv('OAW_HOSTED_SERVICES_FACTORY', 'unexpected_module:must_not_import')
    async def migrate(config, *, services_factory):
        assert services_factory is preview.create_preview_services
        return {'preview': True}
    monkeypatch.setattr(upgrades, 'migrate_profile', migrate)
    assert asyncio.run(worker.migrate(config, preview=True)) == {'preview': True}


@pytest.fixture
def seed_installation(tmp_path, registration, release_request, monkeypatch):
    app, hosting, releases = (tmp_path / name for name in ('app', 'hosting', 'releases'))
    runtime = app / 'backend/.venv'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin/python').write_text('independently prepared runtime')
    (runtime / 'bin/python').chmod(0o755)
    if os.name == 'nt':
        # Windows has no POSIX executable bit for a file named "python".
        original_stat = Path.stat
        def linux_python_mode(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == runtime / 'bin/python':
                fields = list(metadata)
                fields[0] |= 0o111
                return os.stat_result(fields)
            return metadata
        monkeypatch.setattr(Path, 'stat', linux_python_mode)
    (runtime / 'oaw-runtime-bundle').mkdir()
    release = releases / release_request['release']
    release.mkdir(parents=True)
    files = {'main.py': b'approved source', 'backend/pyproject.toml': b'[project]\ndependencies=[]\n'}
    manifest = {'version': 2, 'kind': 'oaw-build', 'source_commit': 'b' * 40,
                'comparison_commit': 'a' * 40, 'routine': False,
                'files': {name: remote.hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    archive = release / 'bundle.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, data in {**files, 'release-manifest.json': json.dumps(manifest).encode()}.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o644
            tar.addfile(member, io.BytesIO(data))
    approval = {'archive_sha256': remote.digest(archive), 'source_commit': manifest['source_commit']}
    remote.write(release / 'request.json', {**release_request, **approval})
    remote.save(release, 'uploading')
    monkeypatch.setattr(remote, 'paths', lambda _: (app, hosting, releases))
    monkeypatch.setattr(remote, 'release_dir', lambda _: release)
    monkeypatch.setattr(remote, 'require_execution', lambda _, **kwargs: registration)
    monkeypatch.setattr(remote, 'authorize', lambda *a, **kw: approval)
    monkeypatch.setattr(remote, 'trusted_operator', lambda: Path(remote.__file__))
    monkeypatch.setattr(remote, 'trusted_metadata', lambda *a, **kw: None)
    monkeypatch.setattr(remote, 'run', lambda *a, **kw: pytest.fail('initial seeding cannot execute a process'))
    return SimpleNamespace(app=app, hosting=hosting, runtime=runtime, release=release,
                           archive=archive, approval=approval, manifest=manifest)


def test_initial_seed_installs_approved_source_and_receipt_without_runtime_or_hosting_changes(seed_installation, release_request):
    fixture = seed_installation
    assert remote.seed(release_request)['phase'] == 'seeded'
    assert (fixture.app / 'main.py').read_text() == 'approved source'
    receipt = remote.read(fixture.app / '.oaw-deployment.json')
    assert receipt['source_commit'] == fixture.approval['source_commit']
    assert receipt['archive_sha256'] == fixture.approval['archive_sha256']
    assert receipt['operator_protocol'] == remote.PROTOCOL_VERSION
    assert (fixture.runtime / 'bin/python').read_text() == 'independently prepared runtime'
    assert not fixture.hosting.exists()


def test_initial_seed_rejects_a_tampered_approved_archive_before_source_changes(seed_installation, release_request):
    fixture = seed_installation
    fixture.archive.write_bytes(b'replaced archive')
    with pytest.raises(ValueError, match='checksum differs'):
        remote.seed(release_request)
    assert not (fixture.app / 'main.py').exists()
    assert not (fixture.release / 'stage').exists()


@pytest.mark.parametrize('occupied', ['main.py', '.oaw-deployment.json'])
def test_initial_seed_refuses_existing_application_or_receipt(seed_installation, release_request, occupied):
    fixture = seed_installation
    target = fixture.app / occupied
    target.write_text('existing installation')
    with pytest.raises(ValueError, match='contain only'):
        remote.seed(release_request)
    assert target.read_text() == 'existing installation'
    assert not (fixture.release / 'stage').exists()


def test_initial_seed_is_unavailable_for_production(seed_installation, registration, release_request):
    registration['environment'] = 'production'
    with pytest.raises(ValueError, match='only for Preview'):
        remote.seed(release_request)
    assert not (seed_installation.app / 'main.py').exists()


def test_initial_seed_checks_provisioner_boundary_before_publishing_source(seed_installation, release_request, monkeypatch):
    def boundary(request, *, deploying):
        assert deploying is True
        raise ValueError('Preview application provisioner must not be running')
    monkeypatch.setattr(remote, 'require_execution', boundary)
    with pytest.raises(ValueError, match='must not be running'):
        remote.seed(release_request)
    assert not (seed_installation.app / 'main.py').exists()
    assert remote.read(seed_installation.release / 'state.json')['phase'] == 'uploading'
