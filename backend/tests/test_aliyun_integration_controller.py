"""Post-merge provenance, explicit activation and real release adapter boundaries."""
from copy import deepcopy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

import pytest

from backend.tests.test_aliyun_preview_controller import BASE, HEAD, TRUSTED, GitHubFixture, controller

RECEIPT = 'f' * 64


def test_native_approval_workflow_is_opt_in_and_never_executes_candidate_code():
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[2] / '.github/workflows/ci.yml').read_text())
    jobs = workflow['jobs']
    for key in ('aliyun-gates', 'aliyun-approval'):
        assert "github.event_name == 'push'" in jobs[key]['if']
        assert "refs/heads/feat/aliyun-agentcore-poc" in jobs[key]['if']
        assert "vars.OAW_ALIYUN_GITHUB_DEPLOYMENT == 'enabled'" in jobs[key]['if']
        assert all('uses' not in step for step in jobs[key]['steps'])
    assert jobs['aliyun-gates']['needs'] == 'required'
    assert jobs['aliyun-approval']['needs'] == 'aliyun-gates'
    assert jobs['aliyun-approval']['environment'] == 'aliyun-integration-approval'
    assert jobs['aliyun-approval']['permissions'] == {}
    assert jobs['aliyun-approval']['steps'][0]['run'].startswith('echo ')
    assert 'aliyun-approval' not in jobs['required']['needs']


class PushGitHub(GitHubFixture):
    def __init__(self):
        super().__init__()
        self.branch = HEAD
        self.environment['name'] = controller.INTEGRATION
        for run in self.runs.values():
            run.update(event='push', head_branch=controller.BASE, pull_requests=[])
        self.artifact['name'] = 'oaw-integration-100-1'
        self.comparison = {'status': 'ahead', 'merge_base_commit': {'sha': BASE},
                           'files': [{'filename': 'frontend/src/App.tsx', 'status': 'modified'}]}

    def get(self, suffix):
        path = urlsplit(suffix).path
        if path == 'environments/' + controller.INTEGRATION:
            return deepcopy(self.environment)
        if path == 'git/ref/heads/' + controller.BASE:
            return {'object': {'sha': self.branch}}
        if path.startswith('compare/'):
            return deepcopy(self.comparison)
        return super().get(suffix)


@pytest.fixture
def integration_cfg():
    return {'environment': controller.INTEGRATION, 'trusted_revision': TRUSTED,
            'enabled_after': '2026-11-03T00:00:00Z', 'access_instructions': 'Existing service.',
            'activation': {'mode': 'disabled', 'approved_run': None, 'accepted_revision': None}}


def test_push_admission_uses_exact_resulting_commit_and_never_reads_pr_merge_ref(integration_cfg):
    api = PushGitHub()
    admission = controller.admit(api, integration_cfg, 100, 1)
    assert admission['candidate'] == {
        'version': 1, 'repository': controller.REPOSITORY, 'repository_id': controller.REPOSITORY_ID,
        'event': 'push', 'head_sha': HEAD, 'base_ref': controller.BASE, 'tested_commit': HEAD,
        'run_id': 100, 'run_attempt': 1}
    assert all('pulls/' not in suffix and 'pull/' not in suffix for _, suffix in api.calls)


def github_approval_fixture(cfg):
    api = PushGitHub()
    state, candidate, baseline = approval_fixture(cfg, mode='github')
    api.permission = 'maintain'
    api.jobs[100].append({**api.jobs[100][0], 'name': 'Ali deployment approval'})
    environment = {'name': 'aliyun-integration-approval', 'id': 5, 'can_admins_bypass': False,
                   'protection_rules': [{'type': 'required_reviewers', 'reviewers': [{'type': 'User', 'reviewer': {'id': 1}}]}]}
    reviews = [{'state': 'approved', 'user': {'login': 'trusted-author'},
                'environments': [{'id': 5, 'name': 'aliyun-integration-approval'}]}]
    original = api.get

    def get(path):
        if path == 'environments/aliyun-integration-approval':
            return deepcopy(environment)
        if path == 'actions/runs/100/approvals':
            return deepcopy(reviews)
        return original(path)

    api.get = get
    return api, state, candidate, baseline, environment, reviews


def test_native_github_approval_allows_reviewed_backend_change_after_controlled_acceptance(integration_cfg):
    api, state, candidate, baseline, _, _ = github_approval_fixture(integration_cfg)
    api.comparison['files'] = [{'filename': 'backend/poc.py', 'status': 'modified'}]
    binding = controller.production_approval(api, integration_cfg, state, candidate, baseline)
    assert binding == {'run_id': 100, 'run_attempt': 1, 'baseline_commit': BASE, 'baseline_receipt': RECEIPT}
    # Approval is re-read before switching; it cannot survive a revoked gate.
    api.jobs[100][-1]['conclusion'] = 'cancelled'
    with pytest.raises(controller.Rejected, match='successful GitHub approval'):
        controller.production_approval(api, integration_cfg, state, candidate, baseline)


@pytest.mark.parametrize('fault', ['no_reviewers', 'bypass', 'skipped', 'old_attempt', 'wrong_commit',
                                  'wrong_environment', 'rejected', 'not_maintainer', 'no_acceptance', 'wrong_baseline'])
def test_github_deployment_approval_fails_closed(integration_cfg, fault):
    api, state, candidate, baseline, environment, reviews = github_approval_fixture(integration_cfg)
    if fault == 'no_reviewers': environment['protection_rules'] = []
    elif fault == 'bypass': environment['can_admins_bypass'] = True
    elif fault == 'skipped': api.jobs[100][-1]['conclusion'] = 'skipped'
    elif fault == 'old_attempt': api.jobs[100][-1]['run_attempt'] = 2
    elif fault == 'wrong_commit': api.jobs[100][-1]['head_sha'] = BASE
    elif fault == 'wrong_environment': reviews[0]['environments'][0]['id'] = 99
    elif fault == 'rejected': reviews[0]['state'] = 'rejected'
    elif fault == 'not_maintainer': api.permission = 'write'
    elif fault == 'no_acceptance': state['controlled_revision'] = None
    elif fault == 'wrong_baseline': baseline = {**baseline, 'receipt': '0' * 64}
    with pytest.raises(controller.Rejected):
        controller.production_approval(api, integration_cfg, state, candidate, baseline)


def test_disabled_optional_approval_jobs_do_not_break_pr_or_push_ci_admission(integration_cfg):
    api = PushGitHub()
    for name in ('Ali required gates', 'Ali deployment approval'):
        api.jobs[100].append({**api.jobs[100][0], 'name': name, 'conclusion': 'skipped'})
    assert controller.admit(api, integration_cfg, 100, 1)['candidate']['tested_commit'] == HEAD
    api.jobs[100].append({**api.jobs[100][0], 'name': 'Unexpected skipped security check', 'conclusion': 'skipped'})
    with pytest.raises(controller.Rejected, match='Required gate'):
        controller.admit(api, integration_cfg, 100, 1)


@pytest.mark.parametrize('mutation,expected', [
    ('pr_artifact', 'exactly one'), ('pr_run', 'Workflow identity'), ('other_branch', 'another branch'),
    ('stale', 'obsolete'), ('other_repo', 'Workflow identity'), ('old_attempt', 'newer workflow attempt'),
    ('artifact_commit', 'Artifact provenance'), ('artifact_attempt', 'another run attempt'),
    ('pipeline', 'trusted validation entrypoint'), ('new_ci', 'newer CI run'),
    ('native_gate', 'Required gate'), ('environment_gate', 'protections'),
])
def test_push_rejects_untrusted_or_obsolete_provenance(integration_cfg, mutation, expected):
    api = PushGitHub()
    if mutation == 'pr_artifact': api.artifact['name'] = 'oaw-preview-100-1'
    elif mutation == 'pr_run': api.runs[100]['event'] = 'pull_request'
    elif mutation == 'other_branch': api.runs[100]['head_branch'] = 'main'
    elif mutation == 'stale': api.branch = '0' * 40
    elif mutation == 'other_repo': api.runs[100]['head_repository'] = {'id': 9}
    elif mutation == 'old_attempt': api.runs[100]['run_attempt'] = 2
    elif mutation == 'artifact_commit': api.artifact['workflow_run']['head_sha'] = BASE
    elif mutation == 'artifact_attempt': api.artifact['created_at'] = '2026-11-02T00:00:00Z'
    elif mutation == 'pipeline': api.proposed_sha = '0' * 40
    elif mutation == 'new_ci': api.runs[103] = {**api.runs[100], 'id': 103}
    elif mutation == 'native_gate': api.jobs[102][0]['name'] = 'Unrelated success'
    elif mutation == 'environment_gate': api.environment['protection_rules'] = [{'type': 'required_reviewers'}]
    with pytest.raises(controller.Rejected, match=expected):
        controller.admit(api, integration_cfg, 100, 1)


def test_branch_advance_during_push_admission_is_rejected(integration_cfg):
    api = PushGitHub()
    original = api.get
    count = 0

    def advancing(suffix):
        nonlocal count
        if suffix == 'git/ref/heads/' + controller.BASE:
            count += 1
            if count == 2:
                api.branch = BASE
        return original(suffix)

    api.get = advancing
    with pytest.raises(controller.Rejected, match='advanced during admission'):
        controller.admit(api, integration_cfg, 100, 1)


def test_disabled_poll_never_loads_operator_or_contacts_github(integration_cfg):
    controller.poll(None, integration_cfg, {'load_operator': lambda: pytest.fail('Disabled operator load')})


def test_explicit_controlled_approval_can_admit_previously_gated_run_but_never_replay_transaction(
        tmp_path, monkeypatch, integration_cfg):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    monkeypatch.setattr(controller, 'host_lease', nullcontext)
    integration_cfg['activation'].update(mode='controlled', approved_run={
        'run_id': 100, 'run_attempt': 1, 'baseline_commit': BASE, 'baseline_receipt': RECEIPT})
    state = {'version': 1, 'inflight': None, 'slot': None, 'generation': 0,
             'seen': {'100:1': {'rejected': 'Change requires explicit maintainer approval'}}}
    monkeypatch.setattr(controller, 'load_state', lambda: state)
    processed = []

    def process(*args):
        processed.append((args[-3], args[-2]))
        state['seen']['100:1'] = 'complete'

    monkeypatch.setattr(controller, 'process', process)
    adapter = {'load_operator': lambda: {}, 'recover': lambda operator: None}
    controller.poll(PushGitHub(), integration_cfg, adapter)
    controller.poll(PushGitHub(), integration_cfg, adapter)
    assert processed == [(100, 1)]


def approval_fixture(cfg, *, mode='routine'):
    cfg['activation'].update(mode=mode, accepted_revision=BASE)
    baseline = {'commit': BASE, 'receipt': RECEIPT}
    candidate = {'tested_commit': HEAD, 'run_id': 100, 'run_attempt': 1}
    state = {'controlled_revision': BASE, 'slot': {'installed_commit': BASE,
                                                'result': {'receipt': baseline}}}
    return state, candidate, baseline


def test_controlled_approval_binds_exact_run_attempt_commit_and_receipt(integration_cfg):
    state, candidate, baseline = approval_fixture(integration_cfg, mode='controlled')
    binding = {'run_id': 100, 'run_attempt': 1, 'baseline_commit': BASE, 'baseline_receipt': RECEIPT}
    integration_cfg['activation']['approved_run'] = binding
    assert controller.production_approval(None, integration_cfg, state, candidate, baseline) == binding
    for key, changed in [('run_id', 101), ('run_attempt', 2), ('baseline_commit', HEAD), ('baseline_receipt', '0' * 64)]:
        integration_cfg['activation']['approved_run'] = {**binding, key: changed}
        with pytest.raises(controller.Rejected, match='Controlled approval differs'):
            controller.production_approval(None, integration_cfg, state, candidate, baseline)


@pytest.mark.parametrize('filename', [
    'backend/hosting/systemd.py', 'backend/storage/schema.py', 'backend/routers/ordinary.py',
    'backend/uv.lock', 'frontend/package-lock.json', 'frontend/vite.config.ts',
    'plugins/matcreator/src/handler.py', 'packs/codex/pack.json', 'scripts/aliyun_release.py',
    'deploy/aliyun/preview_controller.py', '.github/workflows/ci.yml',
    'frontend/src/../../backend/schema.py',
])
def test_unattended_source_gate_rejects_infrastructure_migrations_dependencies_and_backend(integration_cfg, filename):
    api = PushGitHub()
    state, candidate, baseline = approval_fixture(integration_cfg)
    candidate['routine'] = True  # An artifact claim cannot grant authorization.
    api.comparison['files'][0]['filename'] = filename
    with pytest.raises(controller.Rejected, match='explicit maintainer approval'):
        controller.production_approval(api, integration_cfg, state, candidate, baseline)


def test_compatible_ui_diff_is_admitted_from_installed_baseline_after_controlled_acceptance(integration_cfg):
    state, candidate, baseline = approval_fixture(integration_cfg)
    binding = controller.production_approval(PushGitHub(), integration_cfg, state, candidate, baseline)
    assert binding['baseline_commit'] == BASE and binding['baseline_receipt'] == RECEIPT


@pytest.mark.parametrize('mutation,expected', [
    ('no_acceptance', 'Complete controlled'), ('manual_receipt', 'accepted controller history'),
    ('diverged', 'conflicting history'), ('backward', 'conflicting history'),
    ('truncated_diff', 'comparison limit'), ('rename', 'explicit maintainer approval'),
])
def test_routine_deployment_rejects_unaccepted_conflicting_or_incomplete_baselines(integration_cfg, mutation, expected):
    api = PushGitHub()
    state, candidate, baseline = approval_fixture(integration_cfg)
    if mutation == 'no_acceptance': state.pop('controlled_revision')
    elif mutation == 'manual_receipt': baseline = {**baseline, 'receipt': '0' * 64}
    elif mutation == 'diverged': api.comparison['merge_base_commit']['sha'] = HEAD
    elif mutation == 'backward': api.comparison['status'] = 'behind'
    elif mutation == 'truncated_diff': api.comparison['files'] *= 300
    elif mutation == 'rename': api.comparison['files'][0].update(status='renamed', previous_filename='backend/schema.py')
    with pytest.raises(controller.Rejected, match=expected):
        controller.production_approval(api, integration_cfg, state, candidate, baseline)


def push_artifact(tmp_path, integration_cfg):
    admission = controller.admit(PushGitHub(), integration_cfg, 100, 1)
    contents = b'exact merged revision build'
    digest = hashlib.sha256(contents).hexdigest()
    archive = tmp_path / 'push.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('bundle.tar.gz', contents)
        bundle.writestr('candidate.json', json.dumps({**admission['candidate'], 'archive_sha256': digest}))
        bundle.writestr('release.json', json.dumps({'version': 2, 'kind': 'oaw-build', 'commit': HEAD,
                                                  'archive_sha256': digest, 'routine': False}))
    return admission, archive


def test_exact_push_artifact_unpacks_without_pr_fields(tmp_path, integration_cfg):
    admission, archive = push_artifact(tmp_path, integration_cfg)
    candidate = controller.unpack(archive, tmp_path / 'unpacked', admission)
    assert candidate['tested_commit'] == HEAD and 'pull_request' not in candidate and 'pr_number' not in candidate


def test_push_failure_reports_full_installed_rollback_revision(tmp_path, monkeypatch, integration_cfg):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    api = PushGitHub()
    records = []
    api.post = lambda suffix, body: records.append((suffix, body))
    candidate = {**controller.admit(api, integration_cfg, 100, 1)['candidate'], 'environment': controller.INTEGRATION}
    inflight = {'candidate': candidate, 'deployment_id': 7}
    state = {'inflight': inflight, 'slot': None, 'generation': 0, 'seen': {}}
    controller.finish(api, integration_cfg, state, inflight,
                      {'state': {'phase': 'failed-rolled-back'}, 'receipt': {'commit': BASE}})
    assert all(BASE in body['description'] for _, body in records)
    assert all('issues' not in suffix for suffix, _ in records)
    assert records[1][1]['context'] == 'Ali integration deployment'
    assert state['slot'] is None and state['inflight'] is None


@pytest.mark.parametrize('supersession', ['installed_run', 'installed_attempt', 'fresh_attempt', 'latest_run'])
def test_older_push_rejection_cannot_overwrite_a_newer_same_commit_result(integration_cfg, supersession):
    api = PushGitHub()
    rejected = deepcopy(api.runs[100])
    state = {'slot': None}
    posts = []
    api.post = lambda suffix, body: posts.append((suffix, body))
    if supersession in {'installed_run', 'installed_attempt'}:
        state['slot'] = {'candidate': {'head_sha': HEAD,
                                     'run_id': 103 if supersession == 'installed_run' else 100,
                                     'run_attempt': 1 if supersession == 'installed_run' else 2},
                         'installed_commit': HEAD}
    elif supersession == 'fresh_attempt':
        api.runs[100]['run_attempt'] = 2
    else:
        api.runs[103] = {**api.runs[100], 'id': 103}
    controller.report_rejection(api, integration_cfg, state, rejected, 'A newer CI run supersedes this request',
                                installed_commit=HEAD)
    assert posts == []


def test_current_push_rejection_reports_actual_installed_revision(integration_cfg):
    api = PushGitHub()
    posts = []
    api.post = lambda suffix, body: posts.append((suffix, body))
    controller.report_rejection(api, integration_cfg, {'slot': None}, api.runs[100],
                                'Change requires explicit maintainer approval', installed_commit=BASE)
    assert len(posts) == 1 and posts[0][0] == 'statuses/' + HEAD
    assert posts[0][1]['state'] == 'failure' and BASE in posts[0][1]['description']


def test_recovered_success_retains_receipt_for_the_next_routine_update(tmp_path, monkeypatch, integration_cfg):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    api = PushGitHub()
    api.post = lambda suffix, body: None
    candidate = {**controller.admit(api, integration_cfg, 100, 1)['candidate'],
                 'environment': controller.INTEGRATION, 'archive_sha256': 'e' * 64}
    inflight = {'candidate': candidate, 'deployment_id': 7, 'phase': 'transaction'}
    state = {'inflight': inflight, 'slot': None, 'generation': 0, 'seen': {}, 'controlled_revision': BASE}
    installed = {'commit': HEAD, 'receipt': RECEIPT, 'archive_sha256': 'e' * 64, 'release': 'finished-release'}
    terminal = {'phase': 'complete', 'commit': HEAD}
    operator = {'dispatch': lambda request: installed, 'release_dir': lambda request: tmp_path / request['release'],
                'read': lambda path: terminal if path.name == 'state.json' else
                {'source_commit': HEAD, 'archive_sha256': 'e' * 64}}
    controller.reconcile(api, integration_cfg, state, {'TARGET': {}}, operator)
    assert state['slot']['result'] == {'state': terminal, 'receipt': installed}
    assert state['inflight'] is None
    integration_cfg['activation'].update(mode='routine', accepted_revision=BASE)
    api.comparison['merge_base_commit']['sha'] = HEAD
    binding = controller.production_approval(api, integration_cfg, state,
                                              {'tested_commit': 'c' * 40, 'run_id': 103, 'run_attempt': 1}, installed)
    assert binding['baseline_commit'] == HEAD and binding['baseline_receipt'] == RECEIPT


def test_controlled_push_path_uses_existing_adapter_and_does_not_enable_unattended_updates(
        tmp_path, monkeypatch, integration_cfg):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    monkeypatch.setattr(controller, 'configuration', lambda path: deepcopy(integration_cfg))
    integration_cfg['activation'].update(mode='controlled', approved_run={
        'run_id': 100, 'run_attempt': 1, 'baseline_commit': BASE, 'baseline_receipt': RECEIPT})
    _, archive = push_artifact(tmp_path, integration_cfg)
    api = PushGitHub()
    api.download = lambda artifact, path: Path(path).write_bytes(archive.read_bytes())
    reports, transactions = [], []

    def post(suffix, body):
        reports.append((suffix, body))
        return {'id': 7, 'sha': body.get('ref')}

    api.post = post
    baseline = {'commit': BASE, 'receipt': RECEIPT}

    def deploy(operator, candidate, fixed_archive, before_switch):
        transactions.append((candidate, fixed_archive.read_bytes()))
        before_switch()
        return {'state': {'phase': 'complete'}, 'receipt': {'commit': HEAD, 'receipt': 'e' * 64}}

    state = controller.load_state()
    destination = tmp_path / 'download'
    destination.mkdir()
    controller.process(api, integration_cfg, state, {'TARGET': {}, 'deploy': deploy},
                       {'dispatch': lambda request: baseline}, 100, 1, destination)
    assert transactions[0][0]['baseline_receipt'] == RECEIPT
    assert transactions[0][1] == b'exact merged revision build'
    assert reports[0][1]['environment'] == controller.INTEGRATION
    assert reports[0][1]['production_environment'] is True
    assert reports[0][1]['transient_environment'] is False
    assert state['slot']['installed_commit'] == state['controlled_revision'] == HEAD
    assert integration_cfg['activation']['mode'] == 'controlled'
    assert integration_cfg['activation']['accepted_revision'] is None
    assert all('issues' not in suffix for suffix, _ in reports)


def test_root_policy_revocation_during_preparation_prevents_switch(tmp_path, monkeypatch, integration_cfg):
    monkeypatch.setattr(controller, 'STATE', tmp_path)
    integration_cfg['activation'].update(mode='controlled', approved_run={
        'run_id': 100, 'run_attempt': 1, 'baseline_commit': BASE, 'baseline_receipt': RECEIPT})
    revoked = deepcopy(integration_cfg)
    revoked['activation']['mode'] = 'disabled'
    monkeypatch.setattr(controller, 'configuration', lambda path: revoked)
    _, archive = push_artifact(tmp_path, integration_cfg)
    api = PushGitHub()
    api.download = lambda artifact, path: Path(path).write_bytes(archive.read_bytes())
    api.post = lambda suffix, body: {'id': 7, 'sha': body.get('ref')}

    def deploy(operator, candidate, fixed_archive, before_switch):
        with pytest.raises(controller.Rejected, match='activation policy changed'):
            before_switch()
        return {'state': {'phase': 'failed'}, 'receipt': {'commit': BASE}}

    directory = tmp_path / 'download'
    directory.mkdir()
    state = controller.load_state()
    controller.process(api, integration_cfg, state, {'TARGET': {}, 'deploy': deploy},
                       {'dispatch': lambda request: {'commit': BASE, 'receipt': RECEIPT}}, 100, 1, directory)
    assert state['slot'] is None and 'controlled_revision' not in state


@pytest.mark.parametrize('activation', [
    {'mode': 'routine', 'approved_run': None, 'accepted_revision': None},
    {'mode': 'controlled', 'approved_run': None, 'accepted_revision': None},
    {'mode': 'automatic', 'approved_run': None, 'accepted_revision': HEAD},
    {'mode': 'controlled', 'approved_run': {'run_id': 100, 'run_attempt': True,
                                         'baseline_commit': BASE, 'baseline_receipt': RECEIPT}, 'accepted_revision': None},
])
def test_installed_configuration_rejects_incomplete_activation_policies(monkeypatch, activation):
    root = Path('/opt/oaw-integration-controller')
    cfg = {'version': 1, 'repository': controller.REPOSITORY, 'repository_id': controller.REPOSITORY_ID,
           'base_branch': controller.BASE, 'environment': controller.INTEGRATION, 'trusted_revision': TRUSTED,
           'enabled_after': '2026-11-03T00:00:00Z', 'token_file': str(root / 'github-token'),
           'controller_files': {name: 'e' * 64 for name in ('preview_controller.py', 'preview_deploy.py')},
           'access_instructions': 'Existing service.', 'activation': activation}
    monkeypatch.setattr(controller, 'ROOT', root)
    monkeypatch.setattr(controller, 'read', lambda path: cfg)
    monkeypatch.setattr(controller, 'trusted', lambda path, **kwargs: path)
    monkeypatch.setattr(controller, 'sha', lambda path: 'e' * 64)
    with pytest.raises(controller.Rejected):
        controller.configuration(root / 'config.json')
