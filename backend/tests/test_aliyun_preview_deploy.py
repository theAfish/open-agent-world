"""Trusted controller adapter regressions; no live SSH/systemd operations occur."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from backend.tests.test_aliyun_release import installation, remote
from backend.tests.test_aliyun_release_security import write_archive


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('preview_deploy', ROOT / 'deploy/aliyun/preview_deploy.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
original_authorize = remote.authorize


@pytest.fixture
def deployment(installation, tmp_path, monkeypatch):
    existing = installation
    shutil.rmtree(existing.b)
    operator = tmp_path / 'operator'
    operator.mkdir()
    shutil.copyfile(ROOT / 'deploy/aliyun/release_remote.py', operator / 'release_remote.py')
    controller = tmp_path / 'controller'
    controller.mkdir()
    target = {key: existing.request[key] for key in remote.TARGET_KEYS}
    monkeypatch.setattr(adapter, 'TARGET', target)
    monkeypatch.setattr(adapter, 'OPERATOR', operator)
    monkeypatch.setattr(adapter, 'STATE', controller)
    # Filesystem ownership is checked separately. This fixture reuses the real
    # transaction's temporary files, inventory, backups, receipts and recovery.
    monkeypatch.setattr(adapter, 'trusted', lambda path, **kwargs: Path(path))
    monkeypatch.setattr(remote, 'OPERATOR', operator)
    monkeypatch.setattr(remote, 'trusted_operator', lambda: operator / 'release_remote.py')
    monkeypatch.setattr(remote, 'trusted_file', lambda path, **kwargs: Path(path))
    monkeypatch.setattr(remote, 'authorize', original_authorize)
    monkeypatch.setattr(remote, 'prepare_bundle', lambda request, config: None)
    policy = operator / 'trusted-artifacts.json'
    remote.write(policy, {'version': 1, 'purpose': 'trusted_manual', 'target': target,
                          'approved_artifacts': []})
    original_policy = policy.read_bytes()
    archive = tmp_path / 'bundle.tar.gz'
    write_archive(archive, extra={'backend/pyproject.toml': b'[project]\ndependencies=[]\n'})
    candidate = {'tested_commit': 'b' * 40,
                 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    lock_depth = []

    @contextmanager
    def release_lock(root, **kwargs):
        assert not lock_depth, 'Nested operator locks would deadlock on Linux'
        lock_depth.append(root)
        try:
            yield
        finally:
            lock_depth.pop()

    monkeypatch.setattr(remote, 'lock', release_lock)
    return SimpleNamespace(existing=existing, operator=vars(remote), archive=archive,
                           candidate=candidate, policy=policy, original_policy=original_policy,
                           lock_depth=lock_depth, journal=controller / 'operator-policy-journal.json')


def test_adapter_uses_fixed_bytes_and_fresh_admission_under_transaction_lock(deployment):
    f = deployment
    admissions = []

    def admit():
        assert len(f.lock_depth) == 1
        admissions.append(True)

    result = adapter.deploy(f.operator, f.candidate, f.archive, admit)
    assert result['state']['phase'] == 'complete'
    assert result['receipt']['commit'] == f.candidate['tested_commit']
    assert result['receipt']['archive_sha256'] == f.candidate['archive_sha256']
    assert len(admissions) == 2
    assert f.policy.read_bytes() == f.original_policy and not f.journal.exists()


def test_update_during_preparation_is_rejected_before_service_stop(deployment):
    f = deployment
    calls = []

    def admit():
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('PR head superseded the validated build')

    result = adapter.deploy(f.operator, f.candidate, f.archive, admit)
    assert result['state']['phase'] == 'failed'
    assert 'superseded' in result['state']['error']
    assert result['receipt']['commit'] == 'a' * 40
    assert not any(command[:2] == ('systemctl', 'stop') for command in f.existing.commands)
    assert f.policy.read_bytes() == f.original_policy


def test_deployment_failure_reports_restored_receipt_and_removes_temporary_approval(deployment, monkeypatch):
    f = deployment

    def fail_verification(*arguments, **kwargs):
        if arguments[0] == 'verify':
            raise RuntimeError('controlled verification failure')
        return f.existing.run(*arguments, **kwargs)

    monkeypatch.setattr(remote, 'run', fail_verification)
    result = adapter.deploy(f.operator, f.candidate, f.archive, lambda: None)
    assert result['state']['phase'] == 'failed-rolled-back'
    assert result['receipt']['commit'] == 'a' * 40
    assert f.policy.read_bytes() == f.original_policy and not f.journal.exists()


def test_preparation_failure_restores_policy_without_starting_transaction(deployment, monkeypatch):
    f = deployment

    def fail(*args):
        raise RuntimeError('dependency preparation failed')

    monkeypatch.setattr(remote, 'prepare_bundle', fail)
    result = adapter.deploy(f.operator, f.candidate, f.archive, lambda: None)
    assert result['state']['phase'] == 'failed'
    assert 'preparation failed' in result['state']['error']
    assert 'dependency preparation failed' in (
        Path(adapter.TARGET['releases']) / result['state']['release'] / 'failure.log').read_text()
    assert result['receipt']['commit'] == 'a' * 40
    assert remote.read(Path(adapter.TARGET['releases']) / result['state']['release'] / 'state.json')['phase'] == 'failed'
    assert f.policy.read_bytes() == f.original_policy and not f.journal.exists()
    assert not any(command[:2] == ('systemctl', 'stop') for command in f.existing.commands)


def test_changed_baseline_during_failed_preparation_stays_uncertain(deployment, monkeypatch):
    f = deployment

    def fail(*args):
        receipt = f.existing.app / '.oaw-deployment.json'
        value = remote.read(receipt)
        remote.write(receipt, {**value, 'source_commit': 'c' * 40})
        raise RuntimeError('external deployment raced preparation')

    monkeypatch.setattr(remote, 'prepare_bundle', fail)
    with pytest.raises(ValueError, match='baseline changed during failed preparation'):
        adapter.deploy(f.operator, f.candidate, f.archive, lambda: None)
    assert f.policy.read_bytes() == f.original_policy
    assert remote.read(next(Path(adapter.TARGET['releases']).glob('*/state.json')))['phase'] == 'staged'


@pytest.mark.parametrize('phase,active', [('installing', ''), ('ready', ''), ('complete', 'activating')])
def test_other_incomplete_or_running_release_blocks_slot(deployment, monkeypatch, phase, active):
    f = deployment
    previous = Path(adapter.TARGET['releases']) / ('20261101T010000Z-' + 'a' * 12 + '-12345678')
    previous.mkdir()
    remote.write(previous / 'state.json', {'phase': phase})
    monkeypatch.setattr(remote, 'unit_state', lambda unit: active)
    with pytest.raises(ValueError, match='recovery|still running'):
        adapter.deploy(f.operator, f.candidate, f.archive, lambda: None)
    assert f.policy.read_bytes() == f.original_policy and not f.journal.exists()


def test_changed_archive_is_rejected_before_operator_authorization(deployment):
    f = deployment
    f.archive.write_bytes(f.archive.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='digest changed'):
        adapter.deploy(f.operator, f.candidate, f.archive, lambda: None)
    assert f.policy.read_bytes() == f.original_policy and not f.journal.exists()


@pytest.mark.parametrize('replacement_written', [False, True])
def test_interrupted_approval_journal_restores_exact_original_policy(deployment, replacement_written):
    f = deployment
    replacement = f.original_policy + b'\n'
    record = {'version': 1, 'original_policy': f.original_policy.decode(),
              'original_sha256': adapter.digest(f.original_policy),
              'replacement_sha256': adapter.digest(replacement)}
    f.journal.write_text(json.dumps(record))
    if replacement_written:
        f.policy.write_bytes(replacement)
    assert adapter.recover_policy(f.operator)
    assert f.policy.read_bytes() == f.original_policy and not f.journal.exists()


def test_journal_recovery_does_not_overwrite_an_independent_policy_change(deployment):
    f = deployment
    with pytest.raises(ValueError, match='changed during'):
        with adapter.approval(f.operator, f.candidate):
            f.policy.write_bytes(b'{"independent":"administrator edit"}\n')
    assert json.loads(f.policy.read_bytes()) == {'independent': 'administrator edit'}
    assert f.journal.exists()


def test_startup_recovery_does_not_revoke_a_running_transaction_approval(deployment, monkeypatch):
    f = deployment
    prior = Path(adapter.TARGET['releases']) / ('20261101T010000Z-' + 'a' * 12 + '-12345678')
    prior.mkdir()
    remote.write(prior / 'state.json', {'phase': 'installing'})
    replacement = f.original_policy + b'\n'
    f.journal.write_text(json.dumps({'version': 1, 'original_policy': f.original_policy.decode(),
                                    'original_sha256': adapter.digest(f.original_policy),
                                    'replacement_sha256': adapter.digest(replacement)}))
    f.policy.write_bytes(replacement)
    monkeypatch.setattr(remote, 'unit_state', lambda unit: 'active')
    with pytest.raises(ValueError, match='needs recovery'):
        adapter.recover(f.operator)
    assert f.policy.read_bytes() == replacement and f.journal.exists()


def test_operator_loader_checks_reviewed_inventory_before_executing_code(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'OPERATOR', tmp_path)
    monkeypatch.setattr(adapter, 'trusted', lambda path, **kwargs: Path(path))
    monkeypatch.setattr(adapter.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(adapter, 'sys_isolated', lambda: True)
    files = {name: '0' * 64 for name in ('bootstrap.py', 'release_remote.py', 'release_worker.py', 'release_verify.py')}
    (tmp_path / 'installation.json').write_text(json.dumps({'operator_files': files}))
    (tmp_path / 'bootstrap.py').write_text('raise AssertionError("unreviewed code executed")')
    monkeypatch.setattr(adapter.runpy, 'run_path', lambda path: pytest.fail('Unreviewed operator was executed'))
    with pytest.raises(ValueError, match='reviewed inventory'):
        adapter.load_operator()
