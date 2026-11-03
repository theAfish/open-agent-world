"""Authoritative GitHub admission and shared-slot reporting, without cloud access."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('preview_admission', ROOT / 'deploy/aliyun/preview_controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

HEAD, BASE, MERGE, TRUSTED = (value * 40 for value in 'abcd')
START, BUILT, END = ('2026-11-03T01:00:00Z', '2026-11-03T01:10:00Z', '2026-11-03T01:20:00Z')
REPO = {'id': controller.REPOSITORY_ID, 'full_name': controller.REPOSITORY}
ACTOR = {'login': 'trusted-author', 'id': 12}


class GitHubFixture:
    """Small GitHub API data fixture; every unrecognized request fails the test."""
    pages = controller.GitHub.pages

    def __init__(self):
        self.pr = {'number': 80, 'state': 'open', 'draft': False, 'user': ACTOR,
                   'head': {'sha': HEAD, 'repo': REPO},
                   'base': {'sha': BASE, 'ref': controller.BASE, 'repo': REPO}, 'merge_commit_sha': MERGE}
        self.environment = {'name': 'preview', 'protection_rules': [], 'deployment_branch_policy': None}
        self.permission = 'write'
        self.runs = {}
        self.jobs = {}
        for index, workflow_id in enumerate(controller.WORKFLOWS):
            run_id = 100 + index
            self.runs[run_id] = {
                'id': run_id, 'run_attempt': 1, 'workflow_id': workflow_id,
                'path': controller.WORKFLOWS[workflow_id][0], 'event': 'pull_request',
                'repository': REPO, 'head_repository': REPO, 'head_sha': HEAD,
                'actor': ACTOR, 'triggering_actor': ACTOR,
                'status': 'completed', 'conclusion': 'success',
                'created_at': START, 'run_started_at': START, 'updated_at': END,
                'pull_requests': [{'number': 80, 'base': {'ref': controller.BASE, 'sha': BASE}}],
            }
            self.jobs[run_id] = [{'name': controller.WORKFLOWS[workflow_id][1], 'run_attempt': 1,
                                  'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'}]
        self.artifact = {'id': 500, 'name': 'oaw-preview-100-1', 'expired': False,
                         'digest': 'sha256:' + 'e' * 64, 'created_at': BUILT, 'updated_at': BUILT,
                         'workflow_run': {'id': 100, 'repository_id': controller.REPOSITORY_ID,
                                          'head_repository_id': controller.REPOSITORY_ID, 'head_sha': HEAD}}
        self.pinned_sha = 'f' * 40
        self.proposed_sha = self.pinned_sha
        self.calls = []

    def get(self, suffix):
        self.calls.append(('get', suffix))
        parsed = urlsplit(suffix)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == '':
            return deepcopy(REPO)
        if path == 'environments/preview':
            return deepcopy(self.environment)
        if path == 'pulls/80':
            return deepcopy(self.pr)
        if path == 'collaborators/trusted-author/permission':
            return {'permission': self.permission}
        if path == 'git/ref/pull/80/merge':
            return {'object': {'sha': MERGE}}
        if path == 'git/commits/' + MERGE:
            return {'parents': [{'sha': BASE}, {'sha': HEAD}]}
        if path.startswith('contents/'):
            return {'type': 'file', 'sha': self.pinned_sha if query['ref'] == [TRUSTED] else self.proposed_sha}
        parts = path.split('/')
        if len(parts) >= 3 and parts[:2] == ['actions', 'runs']:
            run_id = int(parts[2])
            if len(parts) == 3 or len(parts) == 5 and parts[3] == 'attempts':
                return deepcopy(self.runs[run_id])
            if parts[-1] == 'jobs':
                return {'jobs': deepcopy(self.jobs[run_id])}
            if parts[-1] == 'artifacts':
                return {'artifacts': [deepcopy(self.artifact)]}
        if len(parts) == 4 and parts[:2] == ['actions', 'workflows']:
            workflow_id = int(parts[2])
            return {'workflow_runs': [deepcopy(run) for run in self.runs.values()
                                      if run['workflow_id'] == workflow_id]}
        raise AssertionError('Unexpected API read: ' + suffix)


@pytest.fixture
def cfg():
    return {'trusted_revision': TRUSTED, 'enabled_after': '2026-11-03T00:00:00Z',
            'access_instructions': 'Open the authorized SSH tunnel, then http://127.0.0.1:18081.'}


def test_current_same_repository_merge_is_admitted_using_head_and_merge_as_distinct_identities(cfg):
    api = GitHubFixture()
    # The Docs Pages deployment is deliberately skipped on all PRs.
    api.jobs[101].append({'name': 'deploy', 'run_attempt': 1, 'head_sha': HEAD,
                         'status': 'completed', 'conclusion': 'skipped'})
    admission = controller.admit(api, cfg, 100, 1)
    assert admission['candidate']['head_sha'] == HEAD
    assert admission['candidate']['tested_commit'] == MERGE
    assert admission['artifact']['id'] == 500


@pytest.mark.parametrize('mutation,expected', [
    ('closed', 'PR is closed'), ('draft', 'PR is closed'), ('fork', 'PR is closed'),
    ('branch', 'PR is closed'), ('unauthorized', 'Contributor is not authorized'),
    ('wrong_repository', 'repository identity differs'), ('root_environment_gate', 'protections'),
    ('environment_branch_policy', 'protections'), ('old_authorization', 'predates'),
    ('stale_head', 'immutable run head differs'), ('attempt', 'newer workflow attempt'),
    ('wrong_workflow', 'Workflow identity'), ('workflow_changed', 'trusted validation entrypoint'),
    ('job_skipped', 'workflow jobs'), ('job_old_attempt', 'workflow jobs'),
    ('missing_gate', 'Required gate'), ('newer_ci', 'newer CI run'),
    ('artifact_other_repo', 'Artifact provenance'), ('artifact_wrong_head', 'Artifact provenance'),
    ('artifact_old_attempt', 'another run attempt'), ('artifact_no_digest', 'Artifact provenance'),
    ('artifact_expired', 'Artifact provenance'), ('wrong_artifact_name', 'exactly one'),
])
def test_admission_rejects_stale_or_untrusted_provenance_without_mutation(cfg, mutation, expected):
    api = GitHubFixture()
    if mutation == 'closed': api.pr['state'] = 'closed'
    elif mutation == 'draft': api.pr['draft'] = True
    elif mutation == 'fork': api.pr['head']['repo'] = {'id': 9, 'full_name': 'external/fork'}
    elif mutation == 'branch': api.pr['base']['ref'] = 'main'
    elif mutation == 'unauthorized': api.permission = 'read'
    elif mutation == 'wrong_repository':
        original = api.get
        api.get = lambda suffix: {'id': 9, 'full_name': controller.REPOSITORY} if suffix == '' else original(suffix)
    elif mutation == 'root_environment_gate': api.environment['protection_rules'] = [{'type': 'required_reviewers'}]
    elif mutation == 'environment_branch_policy': api.environment['deployment_branch_policy'] = {'protected_branches': True}
    elif mutation == 'old_authorization': cfg['enabled_after'] = '2026-11-03T02:00:00Z'
    elif mutation == 'stale_head':
        # Association fields remain current while the immutable historical run head differs.
        api.runs[100]['head_sha'] = '0' * 40
    elif mutation == 'attempt': api.runs[100]['run_attempt'] = 2
    elif mutation == 'wrong_workflow': api.runs[100]['workflow_id'] = 999
    elif mutation == 'workflow_changed': api.proposed_sha = '0' * 40
    elif mutation == 'job_skipped': api.jobs[100][0]['conclusion'] = 'skipped'
    elif mutation == 'job_old_attempt': api.jobs[100][0]['run_attempt'] = 2
    elif mutation == 'missing_gate': api.jobs[100][0]['name'] = 'Other success'
    elif mutation == 'newer_ci': api.runs[103] = {**api.runs[100], 'id': 103}
    elif mutation == 'artifact_other_repo': api.artifact['workflow_run']['repository_id'] = 9
    elif mutation == 'artifact_wrong_head': api.artifact['workflow_run']['head_sha'] = '0' * 40
    elif mutation == 'artifact_old_attempt': api.artifact['created_at'] = '2026-11-02T01:10:00Z'
    elif mutation == 'artifact_no_digest': api.artifact['digest'] = None
    elif mutation == 'artifact_expired': api.artifact['expired'] = True
    elif mutation == 'wrong_artifact_name': api.artifact['name'] = 'oaw-preview-100-2'
    with pytest.raises(controller.Rejected, match=expected):
        controller.admit(api, cfg, 100, 1)
    assert all(method == 'get' for method, _ in api.calls)


def test_pending_required_checks_are_retried_instead_of_authorized(cfg):
    api = GitHubFixture()
    api.runs[101].update(status='in_progress', conclusion=None)
    with pytest.raises(controller.Pending, match='still running'):
        controller.admit(api, cfg, 100, 1)


def test_new_attempt_of_older_required_run_cannot_be_hidden_by_newer_success(cfg):
    api = GitHubFixture()
    api.runs[90] = {**api.runs[101], 'id': 90, 'run_attempt': 2,
                    'run_started_at': '2026-11-03T01:30:00Z', 'conclusion': 'failure'}
    with pytest.raises(controller.Pending, match='has not succeeded'):
        controller.admit(api, cfg, 100, 1)


def test_secondary_check_rerun_can_admit_the_same_ci_artifact_after_success(cfg):
    api = GitHubFixture()
    api.runs[101]['conclusion'] = 'failure'
    with pytest.raises(controller.Pending, match='has not succeeded'):
        controller.admit(api, cfg, 100, 1)
    api.runs[101]['conclusion'] = 'success'
    assert controller.admit(api, cfg, 100, 1)['artifact']['id'] == 500


def test_repository_identity_request_does_not_add_github_404_trailing_slash():
    api = controller.GitHub('synthetic-test-token')
    paths = []
    api.request = lambda path: paths.append(path)
    api.get('')
    api.get('pulls/80')
    assert paths == ['/repos/' + controller.REPOSITORY, '/repos/' + controller.REPOSITORY + '/pulls/80']


def archive_fixture(tmp_path, cfg, *, candidate_change=None, record_change=None, extra=None, symlink=False):
    admission = controller.admit(GitHubFixture(), cfg, 100, 1)
    archive_data = b'opaque fixed build archive; only installed operator parses tar'
    digest = hashlib.sha256(archive_data).hexdigest()
    candidate = {**admission['candidate'], 'archive_sha256': digest, **(candidate_change or {})}
    record = {'version': 2, 'kind': 'oaw-build', 'archive_sha256': digest, 'commit': MERGE,
              'routine': False, **(record_change or {})}
    path = tmp_path / 'artifact.zip'
    with zipfile.ZipFile(path, 'w') as bundle:
        bundle.writestr('bundle.tar.gz', archive_data)
        bundle.writestr('candidate.json', json.dumps(candidate))
        item = zipfile.ZipInfo('release.json')
        if symlink:
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(item, json.dumps(record))
        if extra:
            bundle.writestr(extra, 'unapproved')
    return admission, path


def test_only_fixed_candidate_bytes_with_matching_provenance_are_unpacked(tmp_path, cfg):
    admission, archive = archive_fixture(tmp_path, cfg)
    result = controller.unpack(archive, tmp_path / 'unpacked', admission)
    assert result['artifact_id'] == 500 and result['pr_number'] == 80
    assert sorted(path.name for path in (tmp_path / 'unpacked').iterdir()) == ['bundle.tar.gz', 'candidate.json', 'release.json']


@pytest.mark.parametrize('options,expected', [
    ({'candidate_change': {'tested_commit': HEAD}}, 'candidate claims'),
    ({'candidate_change': {'run_attempt': 2}}, 'candidate claims'),
    ({'candidate_change': {'run_id': True}}, 'candidate claims'),
    ({'candidate_change': {'archive_sha256': '0' * 64}}, 'archive identity'),
    ({'record_change': {'commit': HEAD}}, 'archive identity'),
    ({'record_change': {'routine': True}}, 'archive identity'),
    ({'extra': '../escape.py'}, 'exact three'),
    ({'symlink': True}, 'invalid or oversized'),
])
def test_untrusted_outer_artifact_never_grants_authority(tmp_path, cfg, options, expected):
    admission, archive = archive_fixture(tmp_path, cfg, **options)
    with pytest.raises(controller.Rejected, match=expected):
        controller.unpack(archive, tmp_path / 'unpacked', admission)
    assert not (tmp_path.parent / 'escape.py').exists()


def test_download_verifies_github_archive_digest_and_never_sends_token_to_storage(tmp_path):
    from urllib.error import HTTPError
    from email.message import Message
    content = b'immutable artifact bytes'
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            if len(seen) == 1:
                headers = Message()
                headers['Location'] = 'https://trusted.blob.core.windows.net/artifact?signature=private'
                raise HTTPError(request.full_url, 302, 'redirect', headers, None)
            return io.BytesIO(content)

    api = controller.GitHub('synthetic-test-token')
    api.opener = Opener()
    artifact = {'id': 55, 'size_in_bytes': len(content), 'digest': 'sha256:' + hashlib.sha256(content).hexdigest()}
    api.download(artifact, tmp_path / 'artifact.zip')
    assert seen[0].get_header('Authorization') == 'Bearer synthetic-test-token'
    assert seen[1].get_header('Authorization') is None
    artifact['digest'] = 'sha256:' + '0' * 64
    api.opener = Opener()
    seen.clear()
    with pytest.raises(controller.Rejected, match='size or digest differs'):
        api.download(artifact, tmp_path / 'bad.zip')


@pytest.mark.parametrize('where,code', [('api', 429), ('api', 503), ('storage', 403), ('storage', 503)])
def test_transient_download_failures_leave_candidate_retryable(tmp_path, where, code):
    from email.message import Message
    from urllib.error import HTTPError

    class Opener:
        def open(self, request, timeout):
            if where == 'api' or 'api.github.com' not in request.full_url:
                raise HTTPError(request.full_url, code, 'temporary', Message(), None)
            headers = Message()
            headers['Location'] = 'https://trusted.blob.core.windows.net/artifact?private-signature'
            raise HTTPError(request.full_url, 302, 'redirect', headers, None)

    api = controller.GitHub('synthetic-test-token')
    api.opener = Opener()
    with pytest.raises(controller.Pending, match='HTTP ' + str(code)):
        api.download({'id': 55, 'size_in_bytes': 8, 'digest': 'sha256:' + '0' * 64}, tmp_path / 'artifact.zip')


@pytest.fixture
def reporting(tmp_path, monkeypatch, cfg):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    records = []
    monkeypatch.setattr(controller, 'deployment_status', lambda api, deployment_id, phase, candidate, text:
                        records.append(('deployment', deployment_id, phase)))
    monkeypatch.setattr(controller, 'commit_status', lambda api, candidate, phase, text:
                        records.append(('commit', candidate['head_sha'], phase)))
    monkeypatch.setattr(controller, 'comment', lambda api, cfg, candidate, phase, actual=None:
                        records.append(('comment', candidate['pull_request'], phase, actual)))
    state = controller.load_state()
    candidate = {**controller.admit(GitHubFixture(), cfg, 100, 1)['candidate'], 'archive_sha256': 'e' * 64}
    inflight = {'candidate': candidate, 'deployment_id': 7, 'created_at': START, 'generation': 0}
    state['inflight'] = inflight
    return state, inflight, records


def test_success_reports_actual_revision_and_marks_prior_pr_slot_inactive(reporting, cfg):
    state, inflight, records = reporting
    previous = deepcopy(inflight)
    previous['candidate']['pull_request'] = 79
    previous['deployment_id'] = 6
    state['slot'] = previous
    controller.finish(None, cfg, state, inflight, {'state': {'phase': 'complete'}, 'receipt': {'commit': MERGE}})
    assert ('deployment', 7, 'success') in records and ('deployment', 6, 'inactive') in records
    assert ('comment', 80, 'deployed', MERGE) in records
    assert ('comment', 79, 'superseded by PR #80', MERGE) in records
    assert state['generation'] == 1 and state['slot']['installed_commit'] == MERGE and state['inflight'] is None


def test_failed_deployment_reports_restored_revision_and_preserves_previous_success(reporting, cfg):
    state, inflight, records = reporting
    previous = {'deployment_id': 6, 'installed_commit': BASE}
    state['slot'] = previous
    controller.finish(None, cfg, state, inflight,
                      {'state': {'phase': 'failed-rolled-back'}, 'receipt': {'commit': BASE}})
    assert state['slot'] == previous and state['generation'] == 0 and state['inflight'] is None
    assert ('deployment', 7, 'failure') in records and ('deployment', 6, 'inactive') not in records
    assert ('comment', 80, 'failed-rolled-back', BASE) in records


def test_failed_recovery_keeps_inflight_freeze_and_error_status(reporting, cfg):
    state, inflight, records = reporting
    with pytest.raises(controller.Pending, match='recovery is required'):
        controller.finish(None, cfg, state, inflight,
                          {'state': {'phase': 'recovery-required'}, 'receipt': {'commit': BASE}})
    assert state['inflight'] == inflight and ('deployment', 7, 'error') in records


def test_interrupted_controller_never_replays_unproven_deployment(reporting, cfg):
    state, inflight, records = reporting
    operator = {'dispatch': lambda request: {'commit': BASE}}
    with pytest.raises(controller.Pending, match='requires operator inspection'):
        controller.reconcile(None, cfg, state, {'TARGET': {}}, operator)
    assert state['inflight'] == inflight and records == []


def test_same_commit_with_wrong_archive_cannot_reconcile_success(reporting, cfg):
    state, inflight, records = reporting
    operator = {'dispatch': lambda request: {'commit': MERGE, 'archive_sha256': '0' * 64, 'release': 'old'}}
    with pytest.raises(controller.Pending, match='requires operator inspection'):
        controller.reconcile(None, cfg, state, {'TARGET': {}}, operator)
    assert state['inflight'] == inflight and records == []


def test_durable_terminal_result_resumes_reporting_without_replaying_deployment(reporting, cfg):
    state, inflight, records = reporting
    receipt = {'commit': BASE, 'archive_sha256': '0' * 64, 'release': 'prior', 'receipt': '1' * 64}
    inflight.update(phase='reporting-result', result={'state': {'phase': 'failed-rolled-back'},
                                                   'receipt': receipt})
    controller.reconcile(None, cfg, state, {'TARGET': {}}, {'dispatch': lambda request: receipt})
    assert state['inflight'] is None and ('comment', 80, 'failed-rolled-back', BASE) in records


def test_manual_change_after_recorded_result_cannot_be_reported_as_current_success(reporting, cfg):
    state, inflight, records = reporting
    receipt = {'commit': MERGE, 'archive_sha256': 'e' * 64, 'release': 'candidate', 'receipt': '1' * 64}
    inflight.update(phase='reporting-result', result={'state': {'phase': 'complete'}, 'receipt': receipt})
    with pytest.raises(controller.Pending, match='receipt differs'):
        controller.reconcile(None, cfg, state, {'TARGET': {}},
                             {'dispatch': lambda request: {**receipt, 'release': 'different-manual-release'}})
    assert ('deployment', 7, 'error') in records and ('deployment', 7, 'success') not in records


def test_interrupted_initial_reporting_retries_admission_without_claiming_deployment(reporting, cfg):
    state, inflight, records = reporting
    inflight['phase'] = 'reporting-start'
    controller.reconcile(None, cfg, state, {}, {})
    assert state['inflight'] is None and state['seen'] == {} and ('deployment', 7, 'error') in records


def test_complete_controller_path_downloads_fixed_artifact_and_calls_only_existing_operator_adapter(
        reporting, cfg, tmp_path):
    state, _, records = reporting
    state['inflight'] = None
    admission, archive = archive_fixture(tmp_path, cfg)
    api = GitHubFixture()
    api.download = lambda artifact, destination: Path(destination).write_bytes(archive.read_bytes())
    api.post = lambda path, body: {'id': 7, 'sha': body['ref']}
    calls = []

    def deploy(operator, candidate, fixed_archive, before_switch):
        calls.append((candidate['tested_commit'], candidate['artifact_id'], fixed_archive.read_bytes()))
        before_switch()
        return {'state': {'phase': 'complete'}, 'receipt': {'commit': MERGE}}

    directory = tmp_path / 'download'
    directory.mkdir()
    controller.process(api, cfg, state, {'deploy': deploy}, {}, 100, 1, directory)
    assert calls == [(MERGE, 500, b'opaque fixed build archive; only installed operator parses tar')]
    assert ('deployment', 7, 'in_progress') in records and ('deployment', 7, 'success') in records
    assert state['seen'] == {'100:1': 'complete'} and state['slot']['candidate']['tested_commit'] == MERGE


def test_new_commit_during_preparation_is_rejected_at_the_switch_callback(reporting, cfg, tmp_path):
    state, _, records = reporting
    state['inflight'] = None
    admission, archive = archive_fixture(tmp_path, cfg)
    api = GitHubFixture()
    api.download = lambda artifact, destination: Path(destination).write_bytes(archive.read_bytes())
    api.post = lambda path, body: {'id': 7, 'sha': body['ref']}

    def deploy(operator, candidate, fixed_archive, before_switch):
        api.pr['head']['sha'] = '0' * 40
        with pytest.raises(controller.Rejected, match='immutable run head differs'):
            before_switch()
        return {'state': {'phase': 'failed'}, 'receipt': {'commit': BASE}}

    directory = tmp_path / 'download'
    directory.mkdir()
    controller.process(api, cfg, state, {'deploy': deploy}, {}, 100, 1, directory)
    assert ('deployment', 7, 'failure') in records and state['slot'] is None


def test_competing_older_pr_is_rejected_before_download_or_deployment(reporting, cfg, monkeypatch, tmp_path):
    state, _, records = reporting
    state['inflight'] = None
    state['slot'] = {'created_at': END, 'candidate': {'run_id': 999}}
    api = GitHubFixture()
    api.download = lambda *args: pytest.fail('Older candidate must not be downloaded')
    with pytest.raises(controller.Rejected, match='older PR update'):
        controller.process(api, cfg, state, {}, {}, 100, 1, tmp_path)
    assert records == [] and state['generation'] == 0


@pytest.mark.skipif(os.name != 'posix', reason='Linux controller flock boundary')
def test_shared_slot_host_lease_rejects_another_controller(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    with controller.host_lease():
        with pytest.raises(BlockingIOError):
            with controller.host_lease():
                pytest.fail('Competing controller acquired the lease')
