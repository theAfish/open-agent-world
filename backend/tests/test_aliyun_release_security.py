"""Deployment approval boundary; no SSH, systemd or cloud operations are executed."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_security_remote', ROOT / 'deploy/aliyun/release_remote.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)

COMMIT = 'b' * 40
BASE = 'a' * 40
RELEASE = '20261101T010000Z-' + COMMIT[:12] + '-12345678'


def write_archive(path, *, version=2, source=COMMIT, extra=None, manifest_extra=None, corrupt=False):
    files = {'main.py': b'raise AssertionError("artifact code must not run in these tests")\n'}
    files.update(extra or {})
    manifest = {'version': version, 'source_commit': source, 'routine': False,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    if version == 2:
        manifest.update(kind='oaw-build', comparison_commit=BASE,
                        checks={'backend': [], 'frontend': [], 'full_ci': False})
    else:
        operators = {f'operator/{name}': b'raise AssertionError("legacy operator must never execute")\n'
                     for name in ('release_remote.py', 'release_worker.py')}
        manifest.update(baseline_receipt='c' * 64,
                        operator_files={name: hashlib.sha256(data).hexdigest() for name, data in operators.items()})
        files.update(operators)
    manifest.update(manifest_extra or {})
    if corrupt:
        files['main.py'] += b'# differs from manifest\n'
    files['release-manifest.json'] = json.dumps(manifest).encode()
    with tarfile.open(path, 'w:gz') as archive:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    return manifest


@pytest.fixture
def authority(tmp_path, monkeypatch):
    operator = tmp_path / 'operator'
    operator.mkdir()
    monkeypatch.setattr(remote, 'OPERATOR', operator)
    # This fixture models an already installed operator. Metadata checks have
    # separate negative tests below and are never disabled by production code.
    monkeypatch.setattr(remote, 'trusted_file', lambda path, **kwargs: Path(path))
    monkeypatch.setattr(remote, 'trusted_operator', lambda: operator / 'release_remote.py')
    monkeypatch.setattr(remote, 'trusted_worker', lambda: operator / 'release_worker.py')
    app, hosting, releases = (tmp_path / name for name in ('app', 'hosting', 'releases'))
    monkeypatch.setattr(remote, 'paths', lambda request: (app, hosting, releases))
    monkeypatch.setattr(remote, 'run', lambda *args, **kwargs: pytest.fail('Unexpected command execution'))
    request = {'app': str(app), 'hosting': str(hosting), 'releases': str(releases), 'service': 'oaw-hosted',
               'purpose': 'trusted_manual', 'release': RELEASE, 'source_commit': COMMIT,
               'baseline_commit': BASE, 'baseline_receipt': 'c' * 64}
    archive = tmp_path / 'bundle.tar.gz'
    manifest = write_archive(archive)
    request['archive_sha256'] = remote.digest(archive)
    policy = {'version': 1, 'purpose': 'trusted_manual',
              'target': {key: request[key] for key in remote.TARGET_KEYS},
              'approved_artifacts': [{'archive_sha256': request['archive_sha256'], 'source_commit': COMMIT}]}
    remote.write(operator / 'trusted-artifacts.json', policy)
    return SimpleNamespace(operator=operator, request=request, policy=policy, archive=archive,
                           manifest=manifest, releases=releases, b=releases / RELEASE)


def initialize(f):
    assert remote.dispatch({**f.request, 'action': 'init'})['phase'] == 'uploading'
    shutil.copyfile(f.archive, f.b / 'bundle.tar.gz')


@pytest.mark.parametrize('key', ['app', 'hosting', 'releases', 'service'])
def test_policy_rejects_each_wrong_target_before_creating_release(authority, key):
    f = authority
    with pytest.raises(ValueError, match='target is not approved'):
        remote.dispatch({**f.request, key: f.request[key] + '-other', 'action': 'init'})
    assert not f.releases.exists()


@pytest.mark.parametrize('mutation,match', [
    ({'archive_sha256': 'd' * 64}, 'not been approved'),
    ({'source_commit': 'd' * 40}, 'commit differs'),
    ({'purpose': 'preview'}, 'Only trusted_manual'),
])
def test_unapproved_identity_is_rejected_before_upload(authority, mutation, match):
    f = authority
    with pytest.raises(ValueError, match=match):
        remote.dispatch({**f.request, **mutation, 'action': 'init'})
    assert not f.releases.exists()


def test_missing_policy_fails_closed(authority):
    f = authority
    (f.operator / 'trusted-artifacts.json').unlink()
    with pytest.raises(FileNotFoundError):
        remote.dispatch({**f.request, 'action': 'init'})
    assert not f.releases.exists()


@pytest.mark.parametrize('key,value', [('baseline_commit', 'd' * 40), ('baseline_receipt', 'd' * 64)])
def test_upload_resume_cannot_replace_pinned_baseline(authority, key, value):
    f = authority
    initialize(f)
    with pytest.raises(ValueError, match='staged approval or baseline'):
        remote.dispatch({**f.request, key: value, 'action': 'init'})
    assert remote.read(f.b / 'request.json')[key] == f.request[key]


def test_revoked_digest_is_rejected_before_extraction(authority, monkeypatch):
    f = authority
    initialize(f)
    f.policy['approved_artifacts'] = []
    remote.write(f.operator / 'trusted-artifacts.json', f.policy)
    monkeypatch.setattr(remote, 'extract_bundle', lambda *args, **kwargs: pytest.fail('Unapproved extraction'))
    with pytest.raises(ValueError, match='not been approved'):
        remote.dispatch({**f.request, 'action': 'stage'})
    assert not (f.b / 'stage').exists()


def test_approved_archive_cannot_claim_another_commit(authority):
    f = authority
    f.policy['approved_artifacts'][0]['source_commit'] = 'd' * 40
    remote.write(f.operator / 'trusted-artifacts.json', f.policy)
    f.request['source_commit'] = 'd' * 40
    initialize(f)
    with pytest.raises(ValueError, match='Archive source differs'):
        remote.dispatch({**f.request, 'action': 'stage'})
    assert not (f.b / 'stage').exists()
    assert not list(f.b.glob('unpack-*'))


def test_portable_deployment_requires_baseline_before_extraction(authority):
    f = authority
    f.request.pop('baseline_receipt')
    initialize(f)
    with pytest.raises(ValueError, match='pinned installation baseline'):
        remote.dispatch({**f.request, 'action': 'stage'})
    assert not list(f.b.glob('unpack-*'))


@pytest.mark.parametrize('version', [1, 2])
def test_approved_payload_formats_stage_without_executing_operator(authority, monkeypatch, version):
    f = authority
    write_archive(f.archive, version=version)
    f.request['archive_sha256'] = remote.digest(f.archive)
    f.policy['approved_artifacts'][0]['archive_sha256'] = f.request['archive_sha256']
    remote.write(f.operator / 'trusted-artifacts.json', f.policy)
    initialize(f)
    monkeypatch.setattr(remote, 'plan', lambda request: {'to_commit': COMMIT})
    assert remote.dispatch({**f.request, 'action': 'stage'})['phase'] == 'staged'
    pinned = remote.read(f.b / 'request.json')
    assert pinned['manifest_sha256'] == remote.digest(f.b / 'stage/release-manifest.json')


@pytest.mark.parametrize('extra,manifest_extra,match', [
    ({'operator/json.py': b'raise AssertionError("import hijack")'}, None, 'cannot contain operator'),
    (None, {'target': {'app': '/opt/app'}}, 'Invalid portable build manifest'),
])
def test_portable_payload_rejects_privileged_fields_before_extraction(tmp_path, extra, manifest_extra, match):
    archive = tmp_path / 'bundle.tar.gz'
    write_archive(archive, extra=extra, manifest_extra=manifest_extra)
    output = tmp_path / 'stage'
    with pytest.raises(ValueError, match=match):
        remote.extract_bundle(archive, output)
    assert not output.exists()


def test_payload_hashes_are_checked_before_extraction(tmp_path):
    archive = tmp_path / 'bundle.tar.gz'
    write_archive(archive, corrupt=True)
    output = tmp_path / 'stage'
    with pytest.raises(ValueError, match='integrity'):
        remote.extract_bundle(archive, output)
    assert not output.exists()


def test_prepare_uses_installed_helper_in_isolated_python(authority, monkeypatch):
    f = authority
    initialize(f)
    remote.save(f.b, 'staged')
    monkeypatch.setattr(remote, 'plan', lambda request: {})
    from contextlib import nullcontext
    monkeypatch.setattr(remote, 'lock', lambda *args, **kwargs: nullcontext())
    calls = []
    monkeypatch.setattr(remote, 'run', lambda *args, **kwargs: calls.append(args))
    assert remote.dispatch({**f.request, 'action': 'check'})['phase'] == 'ready'
    assert calls[0][1:4] == ('-I', str(f.operator / 'release_worker.py'), 'prepare')


def test_background_job_uses_installed_controller_not_legacy_payload(authority, monkeypatch):
    f = authority
    initialize(f)
    stage = f.b / 'stage'
    stage.mkdir()
    remote.write(stage / 'release-manifest.json', f.manifest)
    remote.save(f.b, 'ready')
    monkeypatch.setattr(remote, 'unit_state', lambda unit: 'inactive')
    monkeypatch.setattr(remote, 'plan', lambda request: {'already_current': False})
    calls = []
    monkeypatch.setattr(remote, 'run', lambda *args, **kwargs: calls.append(args))
    assert remote.start_job({**f.request, 'action': 'start'})['phase'] == 'queued'
    command = calls[0]
    assert command[0] == 'systemd-run' and '--no-block' in command
    assert command[-3:] == ('-I', str(f.operator / 'release_remote.py'), str(f.b / 'transaction.json'))
    assert not any('stage/operator' in argument for argument in command)


def test_revoked_approval_blocks_transaction_before_stopping_services(authority):
    f = authority
    initialize(f)
    stage = f.b / 'stage'
    stage.mkdir()
    remote.write(stage / 'release-manifest.json', f.manifest)
    f.policy['approved_artifacts'] = []
    remote.write(f.operator / 'trusted-artifacts.json', f.policy)
    with pytest.raises(ValueError, match='not been approved'):
        remote.transaction({**f.request, 'action': 'transaction'})


def test_staged_manifest_is_bound_to_the_approved_archive(authority, monkeypatch):
    f = authority
    initialize(f)
    original_plan = remote.plan
    monkeypatch.setattr(remote, 'plan', lambda request: {})
    remote.dispatch({**f.request, 'action': 'stage'})
    manifest = deepcopy(f.manifest)
    manifest['checks']['backend'] = ['different-test.py']
    remote.write(f.b / 'stage/release-manifest.json', manifest)
    with pytest.raises(ValueError, match='Staged release manifest changed'):
        original_plan(f.request)


@pytest.mark.parametrize('metadata,private,match', [
    (SimpleNamespace(st_uid=1000, st_mode=stat.S_IFREG | 0o600, st_nlink=1), False, 'root-owned'),
    (SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o620, st_nlink=1), False, 'root-owned'),
    (SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o600, st_nlink=1), False, 'regular files'),
    (SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600, st_nlink=2), False, 'hard links'),
    (SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644, st_nlink=1), True, 'private to root'),
])
def test_operator_and_policy_metadata_cannot_be_weakened(metadata, private, match):
    with pytest.raises(ValueError, match=match):
        remote.trusted_metadata(metadata, private=private)


def test_controller_rejects_running_from_an_artifact_directory(monkeypatch):
    monkeypatch.setattr(remote, '__file__', '/srv/releases/untrusted/operator/release_remote.py')
    with pytest.raises(ValueError, match='independently installed'):
        remote.trusted_operator()


def test_application_root_cannot_include_the_trusted_operator(monkeypatch):
    class LinuxPath(PurePosixPath):
        def resolve(self):
            return self

    # Exercise Linux path policy on Windows without touching any host paths.
    monkeypatch.setattr(remote, 'Path', LinuxPath)
    monkeypatch.setattr(remote, 'OPERATOR', LinuxPath('/opt/app/operator'))
    request = {'app': '/opt/app', 'hosting': '/srv/hosting', 'releases': '/var/releases', 'service': 'oaw-hosted'}
    with pytest.raises(ValueError, match='operator must be outside'):
        remote.paths(request)
    assert remote.paths({**request, 'app': '/opt/other-app'})[0] == LinuxPath('/opt/other-app')
