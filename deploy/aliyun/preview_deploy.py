"""Deployment admission adapter; all switching stays in the installed operator.

The caller owns the durable host-wide controller lease. This module never loads
application code, starts an asynchronous deployment, or accepts target paths from
a GitHub artifact. A journal makes temporary artifact approval recoverable after
controller interruption.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import tempfile
import traceback
from uuid import uuid4


OPERATOR = Path('/opt/oaw-release-operator-preview')
STATE = Path('/var/lib/oaw-preview-controller')
TARGET = {'app': '/opt/oaw-preview-app', 'hosting': '/srv/oaw-preview-hosting',
          'releases': '/opt/oaw-preview-releases', 'service': 'oaw-preview'}
RELEASE_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}-[a-f0-9]{8}')
TERMINAL = {'complete', 'already-current', 'failed', 'failed-rolled-back', 'rolled-back', 'seeded'}


def integration_target():
    """Select fixed installation paths; never accept paths from build metadata."""
    global OPERATOR, STATE, TARGET
    OPERATOR = Path('/opt/oaw-release-operator')
    STATE = Path('/var/lib/oaw-integration-controller')
    TARGET = {'app': '/opt/open-agent-world', 'hosting': '/srv/oaw-hosting',
              'releases': '/opt/oaw-releases', 'service': 'oaw-hosted'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def trusted(path, *, private=False, directory=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path,
            'Controller paths cannot traverse links')
    for current in (path, *path.parents):
        metadata = current.lstat()
        is_directory = current != path or directory
        require(metadata.st_uid == 0 and not metadata.st_mode & 0o022,
                'Controller paths must be root-owned and not writable by others')
        require(stat.S_ISDIR(metadata.st_mode) if is_directory else stat.S_ISREG(metadata.st_mode),
                'Controller paths must be regular files or real directories')
        if not is_directory:
            require(metadata.st_nlink == 1, 'Controller files cannot be hard links')
        if current == path and private:
            require(not metadata.st_mode & 0o077, 'Controller state must be private to root')
    return path


def load_operator():
    """Load only independently installed bytes matching their installation record."""
    require(getattr(os, 'geteuid', lambda: -1)() == 0 and sys_isolated(),
            'Run the controller as root with Python -I -S')
    installation = json.loads(trusted(OPERATOR / 'installation.json').read_text(encoding='utf-8'))
    files = installation.get('operator_files')
    expected = {'bootstrap.py', 'release_remote.py', 'release_worker.py', 'release_verify.py'}
    if OPERATOR == Path('/opt/oaw-release-operator'):
        expected.add('provisioner_guard.py')
    require(isinstance(files, dict) and set(files) == expected,
            'Installed operator inventory is invalid')
    for name, expected in files.items():
        require(isinstance(expected, str) and re.fullmatch('[a-f0-9]{64}', expected)
                and digest(trusted(OPERATOR / name).read_bytes()) == expected,
                'Installed operator differs from its reviewed inventory')
    namespace = runpy.run_path(str(OPERATOR / 'release_remote.py'))
    # runpy's returned mapping can differ from function globals. Use the actual
    # installed module namespace consistently for the adapter and its functions.
    operator = namespace['dispatch'].__globals__
    require(operator['OPERATOR'] == OPERATOR and
            'before_switch' in inspect.signature(operator['transaction']).parameters,
            'Installed operator lacks trusted admission support')
    operator['trusted_operator']()
    return operator


def sys_isolated():
    import sys
    return sys.flags.isolated and sys.flags.no_site


def sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic(path, data):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).chmod(0o600)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def journal_path():
    return trusted(STATE, private=True, directory=True) / 'operator-policy-journal.json'


def recover_policy(operator):
    """Restore an interrupted temporary approval while the caller owns the lease.

    The caller must first exclude running/incomplete release transactions. An
    unexpected concurrent policy edit is retained and requires manual review.
    """
    journal = journal_path()
    if not journal.exists() and not journal.is_symlink():
        return False
    record = json.loads(trusted(journal, private=True).read_text(encoding='utf-8'))
    require(set(record) == {'version', 'original_policy', 'original_sha256', 'replacement_sha256'}
            and record['version'] == 1 and isinstance(record['original_policy'], str),
            'Temporary operator approval journal is invalid')
    original = record['original_policy'].encode('utf-8')
    require(digest(original) == record['original_sha256'], 'Operator policy journal integrity failed')
    policy = trusted(OPERATOR / 'trusted-artifacts.json', private=True)
    require(digest(policy.read_bytes()) in {record['original_sha256'], record['replacement_sha256']},
            'Operator policy changed during temporary authorization; inspect before recovery')
    atomic(policy, original)
    journal.unlink()
    sync_directory(journal.parent)
    return True


@contextmanager
def approval(operator, candidate):
    operator['authorize'](TARGET)
    policy_path = trusted(OPERATOR / 'trusted-artifacts.json', private=True)
    original = policy_path.read_bytes()
    policy = json.loads(original)
    entry = {'archive_sha256': candidate['archive_sha256'], 'source_commit': candidate['tested_commit']}
    if entry not in policy['approved_artifacts']:
        policy['approved_artifacts'].append(entry)
    replacement = (json.dumps(policy, indent=2) + '\n').encode('utf-8')
    journal = journal_path()
    require(not journal.exists() and not journal.is_symlink(), 'Recover the previous operator approval first')
    atomic(journal, json.dumps({'version': 1, 'original_policy': original.decode('utf-8'),
                               'original_sha256': digest(original),
                               'replacement_sha256': digest(replacement)}).encode('utf-8'))
    try:
        atomic(policy_path, replacement)
        yield
    finally:
        recover_policy(operator)


def require_idle(operator, *, current_release=None):
    releases = Path(TARGET['releases'])
    trusted(releases, private=True, directory=True)
    for directory in releases.iterdir():
        if not RELEASE_ID.fullmatch(directory.name):
            continue
        trusted(directory, private=True, directory=True)
        state_path = directory / 'state.json'
        require(state_path.exists(), 'Existing release has no state; inspect before deployment')
        state = json.loads(trusted(state_path, private=True).read_text(encoding='utf-8'))
        require(directory.name == current_release or state.get('phase') in TERMINAL,
                'Existing release needs recovery before another deployment')
        active = operator['unit_state']('oaw-release-' + directory.name + '.service')
        require(active not in {'active', 'activating', 'deactivating'},
                'A previous deployment transaction is still running')


def recover(operator):
    """Recover a finished controller's approval after excluding active releases."""
    with operator['lock'](Path(TARGET['releases'])):
        require_idle(operator)
        return recover_policy(operator)


def deploy(operator, candidate, archive, before_switch):
    """Deploy admitted bytes synchronously, retaining the existing rollback engine."""
    require(callable(before_switch), 'A trusted freshness callback is required')
    require(isinstance(candidate.get('tested_commit'), str) and
            re.fullmatch('[a-f0-9]{40}', candidate['tested_commit']) and
            isinstance(candidate.get('archive_sha256'), str) and
            re.fullmatch('[a-f0-9]{64}', candidate['archive_sha256']),
            'Invalid admitted commit or archive digest')
    archive = trusted(archive, private=True)
    require(file_digest(archive) == candidate['archive_sha256'], 'Admitted archive digest changed')
    releases = Path(TARGET['releases'])
    with operator['lock'](releases):
        require_idle(operator)
        recover_policy(operator)
        baseline = operator['dispatch']({**TARGET, 'action': 'status'})
        if 'baseline_commit' in candidate:
            require(baseline['commit'] == candidate['baseline_commit']
                    and baseline['receipt'] == candidate['baseline_receipt'],
                    'Installed baseline changed after trusted admission')
        try:
            before_switch()
        except Exception:
            installed = operator['dispatch']({**TARGET, 'action': 'status'})
            require(installed['commit'] == baseline['commit'] and installed['receipt'] == baseline['receipt'],
                    'Installed baseline changed during rejected admission; inspect before recovery')
            return {'state': {'phase': 'failed', 'error': 'Admission rejected; installed service unchanged'},
                    'receipt': installed}
    identifier = (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' +
                  candidate['tested_commit'][:12] + '-' + uuid4().hex[:8])
    request = {**TARGET, 'release': identifier, 'purpose': 'trusted_manual',
               'archive_sha256': candidate['archive_sha256'], 'source_commit': candidate['tested_commit'],
               'baseline_commit': baseline['commit'], 'baseline_receipt': baseline['receipt']}
    with approval(operator, candidate):
        try:
            with operator['lock'](releases):
                state = operator['dispatch']({**request, 'action': 'init'})
                require(state['phase'] == 'uploading', 'New release is not awaiting upload')
                destination = operator['release_dir'](request) / 'bundle.tar.gz'
                with archive.open('rb') as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                destination.chmod(0o600)
                require(operator['digest'](destination) == candidate['archive_sha256'], 'Copied archive digest changed')
                operator['dispatch']({**request, 'action': 'stage'})
            operator['dispatch']({**request, 'action': 'check'})
        except Exception:
            with operator['lock'](releases):
                installed = operator['dispatch']({**TARGET, 'action': 'status'})
                require(installed['commit'] == baseline['commit'] and installed['receipt'] == baseline['receipt'],
                        'Installed baseline changed during failed preparation; inspect before recovery')
                directory = operator['release_dir'](request)
                message = 'Release preparation failed; installed service unchanged; inspect the private release log'
                if directory.exists():
                    with (directory / 'failure.log').open('a', encoding='utf-8') as stream:
                        traceback.print_exc(file=stream)
                    operator['save'](directory, 'failed', error=message)
                return {'state': {'phase': 'failed', 'release': identifier, 'error': message}, 'receipt': installed}

        def admitted():
            require_idle(operator, current_release=identifier)
            before_switch()

        state = operator['transaction'](request, before_switch=admitted)
        installed = operator['dispatch']({**TARGET, 'action': 'status'})
        if state['phase'] == 'complete':
            require(installed['commit'] == candidate['tested_commit'] and installed['release'] == identifier
                    and installed['archive_sha256'] == candidate['archive_sha256'],
                    'Installed receipt differs from the admitted candidate')
        return {'state': state, 'receipt': installed}
