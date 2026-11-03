"""Independently installed deployment admission controller; never run from a checkout.

The timer polls GitHub's native PR checks. Only the installed release operator
executes deployments; this process holds its host lease until rollback finishes.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

ROOT = Path('/opt/oaw-preview-controller')
STATE = Path('/var/lib/oaw-preview-controller')
REPOSITORY = 'theAfish/open-agent-world'
REPOSITORY_ID = 1351393176
BASE = 'feat/aliyun-agentcore-poc'
WORKFLOWS = {
    367413344: ('.github/workflows/ci.yml', 'CI required'),
    363172125: ('.github/workflows/docs.yml', 'Docs required'),
    368742567: ('.github/workflows/desktop-check.yml', 'Native required'),
}
CI = 367413344
PINNED_FILES = [value[0] for value in WORKFLOWS.values()] + [
    '.github/run-backend-tests.py', 'scripts/aliyun_release.py', 'deploy/aliyun/release_remote.py',
]
SHA = re.compile(r'[a-f0-9]{40}')
DIGEST = re.compile(r'[a-f0-9]{64}')
MAX_ARCHIVE = 512 * 1024 * 1024
INTEGRATION = 'aliyun-integration'


def integration(cfg):
    return cfg.get('environment') == INTEGRATION


def select_environment(environment):
    global ROOT, STATE
    if environment == INTEGRATION:
        ROOT = Path('/opt/oaw-integration-controller')
        STATE = Path('/var/lib/oaw-integration-controller')


class Rejected(ValueError):
    """A terminal admission rejection, without deployment side effects."""


class PreviewExcluded(Rejected):
    """Protected pipeline changes require independent review before Preview."""


class Pending(ValueError):
    """GitHub checks have not yet reached an admissible terminal state."""


def require(condition, message):
    if not condition:
        raise Rejected(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    if os.name == 'posix':
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def trusted(path, *, private=False, directory=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'Controller paths cannot traverse links')
    for item in (path, *path.parents):
        metadata = item.lstat()
        is_directory = item != path or directory
        require(metadata.st_uid == 0 and not metadata.st_mode & 0o022
                and (stat.S_ISDIR(metadata.st_mode) if is_directory else stat.S_ISREG(metadata.st_mode)),
                'Controller paths must be root-owned and not writable by other users')
        if not is_directory:
            require(metadata.st_nlink == 1,
                    'Controller secrets must be private regular files')
        if item == path and private:
            require(not metadata.st_mode & 0o077, 'Controller state and secrets must be private to root')
    return path


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value),
            'Invalid GitHub UTC timestamp')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def configuration(path):
    require(Path(path) == ROOT / 'config.json', 'Use the independently installed controller configuration')
    cfg = read(trusted(path, private=True))
    expected = {'version', 'repository', 'repository_id', 'base_branch', 'environment',
                'trusted_revision', 'enabled_after', 'token_file', 'controller_files', 'access_instructions'}
    if ROOT == Path('/opt/oaw-integration-controller'):
        expected.add('activation')
    require(set(cfg) == expected,
            'Invalid controller configuration fields')
    require(cfg['version'] == 1 and cfg['repository'] == REPOSITORY and cfg['repository_id'] == REPOSITORY_ID
            and cfg['base_branch'] == BASE and cfg['environment'] ==
            (INTEGRATION if 'activation' in expected else 'preview'), 'Unapproved controller repository or target')
    if integration(cfg):
        activation = cfg['activation']
        require(isinstance(activation, dict) and set(activation) == {'mode', 'approved_run', 'accepted_revision'}
                and activation['mode'] in {'disabled', 'controlled', 'routine'}, 'Invalid integration activation policy')
        approved = activation['approved_run']
        require(approved is None or isinstance(approved, dict) and set(approved) ==
                {'run_id', 'run_attempt', 'baseline_commit', 'baseline_receipt'}
                and all(type(approved[key]) is int and approved[key] > 0 for key in ('run_id', 'run_attempt'))
                and isinstance(approved['baseline_commit'], str) and SHA.fullmatch(approved['baseline_commit'])
                and isinstance(approved['baseline_receipt'], str) and DIGEST.fullmatch(approved['baseline_receipt']),
                'Controlled deployment requires one exact run and installed baseline')
        accepted = activation['accepted_revision']
        require(accepted is None or isinstance(accepted, str) and SHA.fullmatch(accepted), 'Invalid accepted integration revision')
        require(activation['mode'] != 'controlled' or approved is not None, 'Controlled deployment approval is missing')
        require(activation['mode'] != 'routine' or accepted is not None and approved is None,
                'Routine deployment requires completed acceptance and no pending controlled approval')
    require(isinstance(cfg['trusted_revision'], str) and SHA.fullmatch(cfg['trusted_revision']),
            'Controller must pin an independently reviewed revision')
    timestamp(cfg['enabled_after'])
    require(cfg['token_file'] == str(ROOT / 'github-token'), 'Use the controller-private token file')
    require(isinstance(cfg['access_instructions'], str) and 1 <= len(cfg['access_instructions']) <= 4000,
            'Trusted Preview access instructions are required')
    files = cfg['controller_files']
    require(isinstance(files, dict) and set(files) == {'preview_controller.py', 'preview_deploy.py'},
            'Controller installation must pin both trusted files')
    for name, digest in files.items():
        require(isinstance(digest, str) and DIGEST.fullmatch(digest)
                and sha(trusted(ROOT / name)) == digest, 'Controller installation inventory differs')
    trusted(STATE, private=True, directory=True)
    return cfg


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class GitHub:
    def __init__(self, token):
        require(isinstance(token, str) and token and not re.search(r'\s', token), 'Invalid controller token')
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, *, method='GET', body=None):
        require(path.startswith('/') and not path.startswith('//'), 'Invalid GitHub API path')
        request = Request('https://api.github.com' + path, method=method,
                          data=None if body is None else json.dumps(body).encode(), headers={
                              'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                              'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json',
                              'User-Agent': 'OAW-Preview-controller'})
        try:
            with self.opener.open(request, timeout=45) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
                require(len(payload) <= 16 * 1024 * 1024, 'GitHub response exceeds controller limit')
                return json.loads(payload) if payload else None
        except HTTPError as error:
            # Never echo HTTP response bodies or signed artifact URLs.
            raise Pending('GitHub API request failed with HTTP ' + str(error.code)) from None
        except (URLError, TimeoutError, ConnectionError):
            raise Pending('GitHub API connection failed; candidate will be revalidated') from None

    def get(self, suffix):
        return self.request('/repos/' + REPOSITORY + ('/' + suffix if suffix else ''))

    def post(self, suffix, body):
        return self.request('/repos/' + REPOSITORY + '/' + suffix, method='POST', body=body)

    def patch(self, suffix, body):
        return self.request('/repos/' + REPOSITORY + '/' + suffix, method='PATCH', body=body)

    def pages(self, suffix, key):
        result = []
        for page in range(1, 11):
            data = self.get(suffix + ('&' if '?' in suffix else '?') + urlencode({'per_page': 100, 'page': page}))
            rows = data[key] if key else data
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise Pending('GitHub pagination limit reached; admission requires a narrower reviewed window')

    def download(self, artifact, destination):
        require(type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= MAX_ARCHIVE,
                'Artifact exceeds the approved archive limit')
        request = Request('https://api.github.com/repos/' + REPOSITORY + '/actions/artifacts/'
                          + str(artifact['id']) + '/zip', headers={
                              'Authorization': 'Bearer ' + self.token, 'User-Agent': 'OAW-Preview-controller'})
        try:
            self.opener.open(request, timeout=45).close()
            raise Rejected('Artifact API did not provide a signed download URL')
        except HTTPError as error:
            if error.code != 302:
                raise Pending('Artifact API download failed with HTTP ' + str(error.code)) from None
            location = error.headers.get('Location', '')
        except (URLError, TimeoutError, ConnectionError):
            raise Pending('Artifact API connection failed; candidate will be revalidated') from None
        parsed = urlsplit(location)
        require(parsed.scheme == 'https' and parsed.hostname is not None and not parsed.username and not parsed.password
                and parsed.port in {None, 443}
                and parsed.hostname.endswith(('.blob.core.windows.net', '.githubusercontent.com')),
                'Artifact download URL is outside approved GitHub storage')
        # This request carries no GitHub token, and redirects remain disabled.
        try:
            with self.opener.open(Request(location), timeout=90) as response, Path(destination).open('wb') as output:
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    require(count <= artifact['size_in_bytes'], 'Artifact download exceeds its advertised size')
                    output.write(chunk)
        except HTTPError as error:
            raise Pending('GitHub storage download failed with HTTP ' + str(error.code)) from None
        except (URLError, TimeoutError, ConnectionError):
            raise Pending('GitHub storage connection failed; candidate will be revalidated') from None
        require(count == artifact['size_in_bytes'] and 'sha256:' + sha(destination) == artifact['digest'],
                'GitHub artifact size or digest differs')


def permission(api, actor):
    require(isinstance(actor, dict) and isinstance(actor.get('login'), str)
            and re.fullmatch(r'[A-Za-z0-9-]+', actor['login']), 'Invalid contributor identity')
    value = api.get('collaborators/' + quote(actor['login'], safe='') + '/permission')
    require(value.get('permission') in {'write', 'maintain', 'admin'}, 'Contributor is not authorized for automatic Preview')


def same_repository(value):
    return isinstance(value, dict) and value.get('id') == REPOSITORY_ID and value.get('full_name') == REPOSITORY


def successful_run(api, run, workflow_id, pr, *, event='pull_request'):
    require(run.get('workflow_id') == workflow_id and run.get('path') == WORKFLOWS[workflow_id][0]
            and run.get('event') == event and same_repository(run.get('repository'))
            and same_repository(run.get('head_repository')) and run.get('head_sha') == pr['head']['sha'],
            'Workflow identity or immutable run head differs')
    if event == 'pull_request':
        association = run.get('pull_requests', [])
        require(len(association) == 1 and association[0].get('number') == pr['number']
                and association[0].get('base', {}).get('ref') == BASE
                and association[0].get('base', {}).get('sha') == pr['base']['sha'],
                'Workflow run does not belong to the current PR base')
    else:
        require(run.get('head_branch') == BASE, 'Push run targets another branch')
    if run.get('status') != 'completed':
        raise Pending('Required workflow is still running')
    if run.get('conclusion') != 'success' and workflow_id != CI:
        # A native/docs-only rerun can make the same immutable CI artifact
        # admissible later; do not permanently consume that candidate.
        raise Pending('Required workflow has not succeeded')
    require(run.get('conclusion') == 'success', 'Required workflow did not succeed')
    for actor in ('actor', 'triggering_actor'):
        permission(api, run.get(actor))
    attempt = run.get('run_attempt')
    require(type(attempt) is int and attempt > 0, 'Invalid workflow attempt')
    actual = api.get(f'actions/runs/{run["id"]}/attempts/{attempt}')
    require(all(actual.get(key) == run.get(key) for key in ('id', 'workflow_id', 'head_sha', 'run_attempt', 'event', 'status', 'conclusion')),
            'Workflow attempt metadata differs')
    jobs = api.pages(f'actions/runs/{run["id"]}/attempts/{attempt}/jobs', 'jobs')
    require(jobs and all(job.get('run_attempt') == attempt and job.get('head_sha') == pr['head']['sha']
                         and job.get('status') == 'completed'
                         and (job.get('conclusion') == 'success' or workflow_id == 363172125
                              and job.get('name') == 'deploy' and job.get('conclusion') == 'skipped') for job in jobs)
            and sum(job.get('name') == WORKFLOWS[workflow_id][1] for job in jobs) == 1,
            'Required gate or workflow jobs did not all succeed in this attempt')
    return actual


def workflow_runs(api, workflow_id, head_sha, *, event='pull_request'):
    return api.pages(f'actions/workflows/{workflow_id}/runs?' + urlencode({'event': event, 'head_sha': head_sha}),
                     'workflow_runs')


def admit(api, cfg, run_id, attempt):
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
            'Deployment requests require positive run and attempt IDs')
    if integration(cfg):
        return admit_push(api, cfg, run_id, attempt)
    repo = api.get('')
    require(same_repository(repo), 'GitHub repository identity differs')
    environment = api.get('environments/preview')
    require(environment.get('name') == 'preview' and environment.get('protection_rules') == []
            and 'deployment_branch_policy' in environment and environment['deployment_branch_policy'] is None,
            'Preview environment has protections this controller cannot enforce')
    run = api.get(f'actions/runs/{run_id}')
    require(run.get('run_attempt') == attempt, 'A newer workflow attempt supersedes this request')
    require(timestamp(run['created_at']) >= timestamp(cfg['enabled_after']), 'Workflow predates automatic Preview authorization')
    association = run.get('pull_requests', [])
    require(len(association) == 1 and type(association[0].get('number')) is int, 'Run must identify exactly one PR')
    pr = api.get('pulls/' + str(association[0]['number']))
    require(pr.get('state') == 'open' and pr.get('draft') is False
            and same_repository(pr.get('head', {}).get('repo')) and same_repository(pr.get('base', {}).get('repo'))
            and pr['base'].get('ref') == BASE, 'PR is closed, draft, external, or targets another branch')
    permission(api, pr.get('user'))
    actual = successful_run(api, run, CI, pr)
    merge = api.get(f'git/ref/pull/{pr["number"]}/merge')['object']['sha']
    require(isinstance(merge, str) and SHA.fullmatch(merge) and pr.get('merge_commit_sha') == merge,
            'Current PR merge is not ready')
    commit = api.get('git/commits/' + merge)
    require([parent['sha'] for parent in commit.get('parents', [])] == [pr['base']['sha'], pr['head']['sha']],
            'Current merge parents differ from the PR head/base')
    for path in PINNED_FILES:
        pinned = api.get('contents/' + path + '?' + urlencode({'ref': cfg['trusted_revision']}))
        proposed = api.get('contents/' + path + '?' + urlencode({'ref': merge}))
        if not (pinned.get('type') == proposed.get('type') == 'file' and pinned.get('sha') == proposed.get('sha')):
            raise PreviewExcluded('PR changes a trusted validation entrypoint: ' + path)
    for workflow_id in WORKFLOWS:
        runs = [item for item in workflow_runs(api, workflow_id, pr['head']['sha'])
                if any(link.get('number') == pr['number'] for link in item.get('pull_requests', []))]
        require(runs, 'Required workflow is missing')
        latest = max(runs, key=lambda item: (timestamp(item['run_started_at']), item['id'], item['run_attempt']))
        if workflow_id == CI:
            require(latest['id'] == run_id and latest['run_attempt'] == attempt,
                    'A newer CI run or attempt supersedes this request')
        else:
            # Fetch fresh metadata; list responses can race a rerun.
            successful_run(api, api.get(f'actions/runs/{latest["id"]}'), workflow_id, pr)
    artifacts = api.pages(f'actions/runs/{run_id}/artifacts', 'artifacts')
    selected = [item for item in artifacts if item.get('name') == f'oaw-preview-{run_id}-{attempt}']
    require(len(selected) == 1, 'Expected exactly one attempt-bound Preview artifact')
    artifact = selected[0]
    provenance = artifact.get('workflow_run', {})
    require(artifact.get('expired') is False and type(artifact.get('id')) is int
            and isinstance(artifact.get('digest'), str) and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['digest'])
            and all(provenance.get(key) == value for key, value in {
                'id': run_id, 'repository_id': REPOSITORY_ID, 'head_repository_id': REPOSITORY_ID,
                'head_sha': pr['head']['sha']}.items()), 'Artifact provenance is invalid')
    require(timestamp(actual['run_started_at']) <= timestamp(artifact['created_at'])
            <= timestamp(artifact['updated_at']) <= timestamp(actual['updated_at']), 'Artifact belongs to another run attempt')
    candidate = {'version': 1, 'repository': REPOSITORY, 'repository_id': REPOSITORY_ID,
                 'pull_request': pr['number'], 'head_sha': pr['head']['sha'], 'base_sha': pr['base']['sha'],
                 'base_ref': BASE, 'tested_commit': merge, 'run_id': run_id, 'run_attempt': attempt}
    # Admission includes several API reads. Check freshness again at its end so
    # a push during workflow/artifact verification cannot reuse the early snapshot.
    fresh_run = api.get(f'actions/runs/{run_id}')
    require(fresh_run.get('run_attempt') == attempt and fresh_run.get('status') == 'completed'
            and fresh_run.get('conclusion') == 'success', 'Workflow attempt changed during admission')
    fresh_pr = api.get('pulls/' + str(pr['number']))
    require(fresh_pr.get('state') == 'open' and fresh_pr.get('draft') is False
            and fresh_pr.get('head', {}).get('sha') == candidate['head_sha']
            and fresh_pr.get('base', {}).get('sha') == candidate['base_sha']
            and fresh_pr.get('base', {}).get('ref') == BASE and fresh_pr.get('merge_commit_sha') == merge,
            'PR became stale during admission')
    return {'candidate': candidate, 'artifact': artifact, 'created_at': run['created_at']}


def admit_push(api, cfg, run_id, attempt):
    """Admit a fresh push build, independently of any earlier PR merge ref."""
    require(same_repository(api.get('')), 'GitHub repository identity differs')
    environment = api.get('environments/' + INTEGRATION)
    require(environment.get('name') == INTEGRATION and environment.get('protection_rules') == []
            and 'deployment_branch_policy' in environment and environment['deployment_branch_policy'] is None,
            'Integration environment has protections this controller cannot enforce; use the root activation policy')
    run = api.get(f'actions/runs/{run_id}')
    require(run.get('run_attempt') == attempt, 'A newer workflow attempt supersedes this request')
    require(timestamp(run['created_at']) >= timestamp(cfg['enabled_after']), 'Workflow predates integration authorization')
    revision = api.get('git/ref/heads/' + BASE)['object']['sha']
    require(isinstance(revision, str) and SHA.fullmatch(revision) and run.get('head_sha') == revision,
            'Push candidate is obsolete; current integration branch differs')
    subject = {'head': {'sha': revision}}
    actual = successful_run(api, run, CI, subject, event='push')
    for path in PINNED_FILES:
        pinned = api.get('contents/' + path + '?' + urlencode({'ref': cfg['trusted_revision']}))
        proposed = api.get('contents/' + path + '?' + urlencode({'ref': revision}))
        require(pinned.get('type') == proposed.get('type') == 'file' and pinned.get('sha') == proposed.get('sha'),
                'Push changes a trusted validation entrypoint: ' + path)
    for workflow_id in WORKFLOWS:
        runs = workflow_runs(api, workflow_id, revision, event='push')
        require(runs, 'Required push workflow is missing')
        latest = max(runs, key=lambda item: (timestamp(item['run_started_at']), item['id'], item['run_attempt']))
        if workflow_id == CI:
            require(latest['id'] == run_id and latest['run_attempt'] == attempt,
                    'A newer CI run or attempt supersedes this request')
        else:
            successful_run(api, api.get(f'actions/runs/{latest["id"]}'), workflow_id, subject, event='push')
    selected = [item for item in api.pages(f'actions/runs/{run_id}/artifacts', 'artifacts')
                if item.get('name') == f'oaw-integration-{run_id}-{attempt}']
    require(len(selected) == 1, 'Expected exactly one attempt-bound integration artifact')
    artifact = selected[0]
    provenance = artifact.get('workflow_run', {})
    require(artifact.get('expired') is False and type(artifact.get('id')) is int
            and isinstance(artifact.get('digest'), str) and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['digest'])
            and all(provenance.get(key) == value for key, value in {
                'id': run_id, 'repository_id': REPOSITORY_ID, 'head_repository_id': REPOSITORY_ID,
                'head_sha': revision}.items()), 'Artifact provenance is invalid')
    require(timestamp(actual['run_started_at']) <= timestamp(artifact['created_at'])
            <= timestamp(artifact['updated_at']) <= timestamp(actual['updated_at']), 'Artifact belongs to another run attempt')
    candidate = {'version': 1, 'repository': REPOSITORY, 'repository_id': REPOSITORY_ID,
                 'event': 'push', 'head_sha': revision, 'base_ref': BASE, 'tested_commit': revision,
                 'run_id': run_id, 'run_attempt': attempt}
    fresh = api.get(f'actions/runs/{run_id}')
    require(fresh.get('run_attempt') == attempt and fresh.get('status') == 'completed'
            and fresh.get('conclusion') == 'success', 'Workflow attempt changed during admission')
    require(api.get('git/ref/heads/' + BASE)['object']['sha'] == revision,
            'Integration branch advanced during admission')
    return {'candidate': candidate, 'artifact': artifact, 'created_at': run['created_at']}


def routine_path(name):
    """Only UI/assets, docs and tests are unattended; backend/runtime changes need review."""
    if not isinstance(name, str) or not name or '\\' in name or any(part in {'', '.', '..'} for part in name.split('/')):
        return False
    return (name.startswith(('frontend/src/', 'frontend/public/', 'docs/', 'backend/tests/'))
            or bool(re.fullmatch(r'(?:plugins|packs)/[^/]+/(?:frontend/src|frontend/public|tests)/.+', name)))


def production_approval(api, cfg, state, candidate, baseline):
    """Root-installed activation policy and authoritative diff, never artifact claims."""
    activation = cfg['activation']
    mode = activation['mode']
    require(mode != 'disabled', 'Integration deployment is disabled pending maintainer approval')
    require(isinstance(baseline.get('commit'), str) and SHA.fullmatch(baseline['commit'])
            and isinstance(baseline.get('receipt'), str) and DIGEST.fullmatch(baseline['receipt']),
            'Installed integration baseline has no verifiable receipt')
    binding = {'run_id': candidate['run_id'], 'run_attempt': candidate['run_attempt'],
               'baseline_commit': baseline['commit'], 'baseline_receipt': baseline['receipt']}
    if mode == 'controlled':
        require(activation['approved_run'] == binding, 'Controlled approval differs from this run or installed baseline')
        return binding
    require(mode == 'routine' and activation['accepted_revision'] == state.get('controlled_revision'),
            'Complete controlled deployment acceptance before enabling routine updates')
    previous = state.get('slot')
    require(previous and previous.get('installed_commit') == baseline['commit']
            and previous.get('result', {}).get('receipt', {}).get('receipt') == baseline['receipt'],
            'Installed baseline differs from the accepted controller history; controlled acceptance is required')
    comparison = api.get('compare/' + baseline['commit'] + '...' + candidate['tested_commit'])
    require(comparison.get('status') == 'ahead'
            and comparison.get('merge_base_commit', {}).get('sha') == baseline['commit'],
            'Routine deployment must advance the exact installed baseline without conflicting history')
    files = comparison.get('files')
    require(isinstance(files, list) and 0 < len(files) < 300,
            'Routine diff is empty or exceeds the complete GitHub comparison limit; explicit review required')
    for item in files:
        require(item.get('status') in {'added', 'modified', 'removed', 'renamed'}
                and routine_path(item.get('filename'))
                and (item.get('status') != 'renamed' or routine_path(item.get('previous_filename'))),
                'Change requires explicit maintainer approval: ' + str(item.get('filename')))
    return binding


def unpack(archive, destination, admission):
    expected = admission['candidate']
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        require(len(entries) == 3 and {item.filename for item in entries} == {'bundle.tar.gz', 'release.json', 'candidate.json'},
                'Artifact must contain only the exact three candidate files')
        for item in entries:
            mode = item.external_attr >> 16
            require(not item.flag_bits & 1 and (not stat.S_IFMT(mode) or stat.S_ISREG(mode))
                    and 0 < item.file_size <= (MAX_ARCHIVE if item.filename == 'bundle.tar.gz' else 65536),
                    'Artifact contains an invalid or oversized member')
            with bundle.open(item) as source, (destination / item.filename).open('wb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            (destination / item.filename).chmod(0o600)
    candidate = read(destination / 'candidate.json')
    require(set(candidate) == set(expected) | {'archive_sha256'}
            and all(candidate.get(key) == value for key, value in expected.items())
            and type(candidate.get('version')) is int
            and all(type(candidate.get(key)) is int for key in
                    ('repository_id', 'run_id', 'run_attempt') + (('pull_request',) if 'pull_request' in expected else ())),
            'Artifact candidate claims differ from authoritative GitHub provenance')
    digest = sha(destination / 'bundle.tar.gz')
    record = read(destination / 'release.json')
    require(candidate.get('archive_sha256') == digest and record.get('archive_sha256') == digest
            and record.get('version') == 2 and record.get('kind') == 'oaw-build'
            and record.get('commit') == expected['tested_commit'] and record.get('routine') is False,
            'Fixed build archive identity differs')
    return {**candidate, 'artifact_id': admission['artifact']['id'],
            **({'pr_number': candidate['pull_request']} if 'pull_request' in candidate else {}),
            'artifact_digest': admission['artifact']['digest']}


def log_url(candidate):
    return f'https://github.com/{REPOSITORY}/actions/runs/{candidate["run_id"]}/attempts/{candidate["run_attempt"]}'


def deployment_status(api, deployment_id, state, candidate, description):
    body = {
        'state': state, 'environment': candidate.get('environment', 'preview'),
        'log_url': log_url(candidate), 'description': description[:140], 'auto_inactive': False}
    if candidate.get('event') != 'push':
        body['environment_url'] = 'http://127.0.0.1:18081'
    api.post(f'deployments/{deployment_id}/statuses', body)


def comment(api, cfg, candidate, state, actual_commit=None):
    if candidate.get('event') == 'push':
        return
    marker = '<!-- oaw-private-preview -->'
    body = (marker + '\nPrivate shared Preview: **' + state + '**.\n\n'
            f'PR #{candidate["pull_request"]}; head `{candidate["head_sha"]}`; tested merge `{candidate["tested_commit"]}`.\n\n'
            f'[CI run {candidate["run_id"]}, attempt {candidate["run_attempt"]}]({log_url(candidate)}).\n\n'
            + (f'Operator receipt revision: `{actual_commit}`.\n\n' if actual_commit else '')
            + cfg['access_instructions'])
    upsert_comment(api, candidate['pull_request'], marker, body)


def upsert_comment(api, pull_request, marker, body):
    comments = api.pages(f'issues/{pull_request}/comments', None)
    # Only update this token's own prior comment, never an impersonated marker.
    identity = api.request('/user')['id']
    existing = [item for item in comments if item.get('user', {}).get('id') == identity
                and item.get('body', '').startswith(marker)]
    if existing:
        api.patch(f'issues/comments/{existing[-1]["id"]}', {'body': body})
    else:
        api.post(f'issues/{pull_request}/comments', {'body': body})


def exclusion_comment(api, candidate, reason):
    marker = '<!-- oaw-preview-eligibility -->'
    body = (marker + '\nAutomatic Private Preview: **not eligible for this revision**.\n\n'
            f'Head `{candidate["head_sha"]}`; '
            f'[CI run {candidate["run_id"]}, attempt {candidate["run_attempt"]}]({log_url(candidate)}).\n\n'
            + reason + '.\n\n'
            '**No Preview deployment was attempted.** Protected validation/deployment files must match '
            'the independently reviewed controller policy. That restriction remains enforced.\n\n'
            'This reporter uses GitHub commit statuses, which have no neutral/skipped state; '
            'the non-success status records an authorization exclusion, not a failed deployment. '
            'It does not claim a successful Preview.\n\n'
            'An intentional Preview policy exclusion is not a blocker for merging a properly reviewed '
            'infrastructure PR when all required checks and other merge requirements are satisfied. '
            'Production installation and activation remain separately gated.')
    upsert_comment(api, candidate['pull_request'], marker, body)


def commit_status(api, candidate, state, description):
    api.post('statuses/' + candidate['head_sha'], {
        'state': state, 'context': 'Ali integration deployment' if candidate.get('event') == 'push' else 'Private Preview',
        'description': description[:140], 'target_url': log_url(candidate)})


@contextlib.contextmanager
def host_lease():
    import fcntl
    with (STATE / '.host.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def load_state():
    path = STATE / 'state.json'
    return read(path) if path.exists() else {'version': 1, 'generation': 0, 'seen': {}, 'slot': None, 'inflight': None}


def save_state(state):
    write(STATE / 'state.json', state)


def reconcile(api, cfg, state, adapter, operator):
    """A killed transaction is never replayed. Resume reporting only if receipt proves completion."""
    inflight = state.get('inflight')
    if not inflight:
        return
    candidate = inflight['candidate']
    if inflight.get('phase') == 'reporting-result':
        current = operator['dispatch']({**adapter['TARGET'], 'action': 'status'})
        recorded = inflight['result']['receipt']
        keys = ('commit', 'archive_sha256', 'release', 'receipt')
        if not all(key in current and key in recorded and current[key] == recorded[key] for key in keys):
            deployment_status(api, inflight['deployment_id'], 'error', candidate,
                              'Stored result no longer matches the current Preview receipt; operator inspection required')
            raise Pending('Current Preview receipt differs from the recorded result; automatic deployments are frozen')
        finish(api, cfg, state, inflight, inflight['result'])
        return
    if inflight.get('phase') == 'reporting-start':
        deployment_status(api, inflight['deployment_id'], 'error', candidate,
                          'Controller interrupted before deployment; candidate will be revalidated')
        state['inflight'] = None
        save_state(state)
        return
    installed = operator['dispatch']({**adapter['TARGET'], 'action': 'status'})
    if (installed['commit'] == candidate['tested_commit']
            and installed.get('archive_sha256') == candidate['archive_sha256'] and installed.get('release')):
        release = operator['release_dir']({**adapter['TARGET'], 'release': installed['release']})
        terminal = operator['read'](release / 'state.json')
        pinned = operator['read'](release / 'request.json')
        if (terminal.get('phase') == 'complete' and terminal.get('commit') == candidate['tested_commit']
                and pinned.get('source_commit') == candidate['tested_commit']
                and pinned.get('archive_sha256') == candidate['archive_sha256']):
            finish(api, cfg, state, inflight, {'state': terminal, 'receipt': installed})
            return
    raise Pending('Interrupted Preview transaction requires operator inspection; automatic deployments are frozen')


def report_rejection(api, cfg, state, run, reason, *, installed_commit=None, excluded=False):
    """Report only against a current same-repository head; never overwrite a newer slot result."""
    if integration(cfg):
        if (run.get('event') != 'push' or run.get('workflow_id') != CI or run.get('head_branch') != BASE
                or not same_repository(run.get('head_repository'))
                or api.get('git/ref/heads/' + BASE)['object']['sha'] != run.get('head_sha')):
            return
        slot = state.get('slot')
        identity = (run['id'], run['run_attempt'])
        if (slot and slot['candidate'].get('head_sha') == run.get('head_sha')
                and (slot['candidate']['run_id'], slot['candidate']['run_attempt']) >= identity):
            return
        fresh = api.get(f'actions/runs/{run["id"]}')
        if fresh.get('run_attempt') != run['run_attempt']:
            return
        runs = workflow_runs(api, CI, run['head_sha'], event='push')
        latest = max(runs, key=lambda item: (timestamp(item['run_started_at']), item['id'], item['run_attempt'])) if runs else None
        if latest is None or (latest['id'], latest['run_attempt']) != identity:
            return
        candidate = {'event': 'push', 'head_sha': run['head_sha'], 'run_id': run['id'], 'run_attempt': run['run_attempt']}
        installed = installed_commit or (state.get('slot') or {}).get('installed_commit', 'unknown')
        commit_status(api, candidate, 'failure', 'Installed ' + installed + '; deployment gated: ' + reason)
        return
    if run.get('event') != 'pull_request' or run.get('workflow_id') != CI or not same_repository(run.get('head_repository')):
        return
    links = run.get('pull_requests', [])
    if len(links) != 1:
        return
    pr = api.get('pulls/' + str(links[0]['number']))
    slot = state['slot']
    if (pr.get('state') != 'open' or not same_repository(pr.get('head', {}).get('repo'))
            or pr['head'].get('sha') != run.get('head_sha')
            or slot and slot['candidate']['head_sha'] == run.get('head_sha')
            and slot['candidate']['run_id'] >= run['id']):
        return
    candidate = {'head_sha': run['head_sha'], 'run_id': run['id'], 'run_attempt': run['run_attempt'],
                 'pull_request': pr['number']}
    if excluded:
        exclusion_comment(api, candidate, reason)
    commit_status(api, candidate, 'failure',
                  ('Preview not eligible; no deployment attempted: ' if excluded else 'Preview rejected: ') + reason)


def finish(api, cfg, state, inflight, result):
    candidate, deployment_id = inflight['candidate'], inflight['deployment_id']
    phase = result['state']['phase']
    actual = result['receipt']['commit']
    success = phase in {'complete', 'already-current'} and actual == candidate['tested_commit']
    if success:
        previous = state['slot']
        deployment_status(api, deployment_id, 'success', candidate, 'Installed validated revision ' + actual)
        commit_status(api, candidate, 'success', 'Installed validated revision ' + actual)
        comment(api, cfg, candidate, 'deployed', actual)
        if previous and previous['deployment_id'] != deployment_id:
            deployment_status(api, previous['deployment_id'], 'inactive', previous['candidate'],
                              'Replaced by installed revision ' + actual)
            if candidate.get('event') != 'push' and previous['candidate']['pull_request'] != candidate['pull_request']:
                comment(api, cfg, previous['candidate'], f'superseded by PR #{candidate["pull_request"]}', actual)
        state['generation'] += 1
        state['slot'] = {**inflight, 'result': result, 'installed_commit': actual, 'generation': state['generation']}
        if integration(cfg) and cfg['activation']['mode'] == 'controlled':
            state['controlled_revision'] = actual
    else:
        deployment_status(api, deployment_id, 'failure' if phase != 'recovery-required' else 'error', candidate,
                          'Deployment ' + phase + '; installed revision ' + actual)
        commit_status(api, candidate, 'failure', 'Deployment ' + phase + '; installed ' + actual)
        comment(api, cfg, candidate, phase, actual)
        if phase == 'recovery-required':
            raise Pending('Preview recovery is required; automatic deployments are frozen')
    state['seen'][f'{candidate["run_id"]}:{candidate["run_attempt"]}'] = phase
    state['inflight'] = None
    save_state(state)


def process(api, cfg, state, adapter, operator, run_id, attempt, directory):
    if integration(cfg):
        require(cfg['activation']['mode'] != 'disabled', 'Integration deployment is disabled pending maintainer approval')
    admission = admit(api, cfg, run_id, attempt)
    slot = state['slot']
    order = (admission['created_at'], run_id)
    require(not slot or order >= (slot['created_at'], slot['candidate']['run_id']),
            'An older PR update cannot replace a newer shared-slot deployment')
    zip_path = Path(directory) / 'candidate.zip'
    api.download(admission['artifact'], zip_path)
    candidate = unpack(zip_path, Path(directory) / 'candidate', admission)
    if integration(cfg):
        baseline = operator['dispatch']({**adapter['TARGET'], 'action': 'status'})
        binding = production_approval(api, cfg, state, candidate, baseline)
        candidate.update(environment=INTEGRATION, baseline_commit=binding['baseline_commit'],
                         baseline_receipt=binding['baseline_receipt'])
    environment = INTEGRATION if integration(cfg) else 'preview'
    deployment = api.post('deployments', {
        'ref': candidate['tested_commit'], 'environment': environment, 'auto_merge': False, 'required_contexts': [],
        'transient_environment': not integration(cfg), 'production_environment': integration(cfg),
        'description': 'Ali integration release' if integration(cfg) else f'Private shared Preview for PR #{candidate["pull_request"]}',
        'payload': {key: candidate[key] for key in ('pull_request', 'head_sha', 'base_sha', 'tested_commit',
                                                   'run_id', 'run_attempt', 'artifact_id', 'artifact_digest') if key in candidate},
    })
    require(type(deployment.get('id')) is int and deployment.get('sha') == candidate['tested_commit'],
            'GitHub deployment identity differs')
    inflight = {'candidate': candidate, 'deployment_id': deployment['id'], 'created_at': admission['created_at'],
                'generation': state['generation'], 'phase': 'reporting-start'}
    state['inflight'] = inflight
    save_state(state)
    destination = 'Ali integration service' if integration(cfg) else 'private shared Preview'
    deployment_status(api, deployment['id'], 'queued', candidate, 'Verified artifact waiting for the ' + destination)
    commit_status(api, candidate, 'pending', 'Verified candidate preparing the ' + destination)
    comment(api, cfg, candidate, 'deploying')
    generation = state['generation']

    def before_switch():
        require(load_state()['generation'] == generation, 'Shared-slot generation changed before deployment')
        fresh = admit(api, cfg, run_id, attempt)
        require(fresh == admission, 'Candidate became stale during deployment preparation')
        if integration(cfg):
            require(configuration(ROOT / 'config.json') == cfg,
                    'Integration activation policy changed before switching')
            current = operator['dispatch']({**adapter['TARGET'], 'action': 'status'})
            require(production_approval(api, cfg, state, candidate, current) == binding,
                    'Integration activation approval or installed baseline changed before switching')
        deployment_status(api, deployment['id'], 'in_progress', candidate, 'Verified current candidate; switching the registered service')

    inflight['phase'] = 'transaction'
    save_state(state)
    result = adapter['deploy'](operator, candidate, Path(directory) / 'candidate/bundle.tar.gz', before_switch)
    inflight.update(phase='reporting-result', result=result)
    save_state(state)
    finish(api, cfg, state, inflight, result)


def poll(api, cfg, adapter):
    if integration(cfg) and cfg['activation']['mode'] == 'disabled':
        print(json.dumps({'environment': INTEGRATION, 'deployment': 'disabled'}), flush=True)
        return
    with host_lease():
        operator = adapter['load_operator']()
        state = load_state()
        inflight = state.get('inflight')
        if (inflight and inflight.get('phase') == 'reporting-result'
                and inflight['result']['state']['phase'] == 'recovery-required'):
            # Reporting an already recorded recovery error must not depend on
            # admitting another transaction or revoking its remaining approval.
            reconcile(api, cfg, state, adapter, operator)
        adapter['recover'](operator)
        reconcile(api, cfg, state, adapter, operator)
        suffix = f'actions/workflows/{CI}/runs?' + urlencode({'event': 'push' if integration(cfg) else 'pull_request',
                                                           'created': '>=' + cfg['enabled_after']})
        runs = sorted(api.pages(suffix, 'workflow_runs'), key=lambda item: (item['created_at'], item['id']), reverse=True)
        for run in runs:
            if integration(cfg) and cfg['activation']['mode'] == 'controlled':
                approved = cfg['activation']['approved_run']
                if (run['id'], run['run_attempt']) != (approved['run_id'], approved['run_attempt']):
                    continue
            key = f'{run["id"]}:{run["run_attempt"]}'
            approved_rejection = (integration(cfg) and cfg['activation']['mode'] == 'controlled'
                                  and isinstance(state['seen'].get(key), dict)
                                  and set(state['seen'][key]) == {'rejected'})
            if (key in state['seen'] and not approved_rejection
                    or run.get('status') != 'completed' or run.get('conclusion') != 'success'):
                continue
            try:
                with tempfile.TemporaryDirectory(prefix='candidate-', dir=STATE) as directory:
                    process(api, cfg, state, adapter, operator, run['id'], run['run_attempt'], directory)
            except Rejected as error:
                if state['inflight']:
                    raise Pending('Deployment started; inspect the trusted controller state before retrying') from error
                if integration(cfg):
                    current = operator['dispatch']({**adapter['TARGET'], 'action': 'status'})
                    report_rejection(api, cfg, state, run, str(error), installed_commit=current['commit'])
                else:
                    report_rejection(api, cfg, state, run, str(error), excluded=isinstance(error, PreviewExcluded))
                state['seen'][key] = {'rejected': str(error)} if integration(cfg) else str(error)
                save_state(state)
                print(json.dumps({'run_id': run['id'], 'attempt': run['run_attempt'], 'rejected': str(error)}), flush=True)
            except Pending:
                if state['inflight']:
                    raise
                continue


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=['preview', INTEGRATION], default='preview')
    parser.add_argument('--config', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('poll')
    verify = commands.add_parser('verify-run', help='Read-only admission/download verification; never authorize or deploy')
    verify.add_argument('run_id', type=int)
    verify.add_argument('attempt', type=int)
    args = parser.parse_args(argv)
    select_environment(args.environment)
    require(os.name == 'posix' and os.geteuid() == 0 and sys.flags.isolated and sys.flags.no_site,
            'Run the installed controller as root using independent Python -I -S')
    require(Path(__file__).absolute() == ROOT / 'preview_controller.py', 'Use the independently installed controller')
    cfg = configuration(args.config or ROOT / 'config.json')
    api = GitHub(trusted(cfg['token_file'], private=True).read_text(encoding='utf-8').strip())
    if args.command == 'verify-run':
        with tempfile.TemporaryDirectory(prefix='verify-', dir=STATE) as directory:
            admission = admit(api, cfg, args.run_id, args.attempt)
            archive = Path(directory) / 'candidate.zip'
            api.download(admission['artifact'], archive)
            candidate = unpack(archive, Path(directory) / 'candidate', admission)
            print(json.dumps({'verified': candidate}), flush=True)
        return
    adapter = runpy.run_path(str(trusted(ROOT / 'preview_deploy.py')))
    if integration(cfg):
        adapter['integration_target']()
        adapter = adapter['deploy'].__globals__
    poll(api, cfg, adapter)


if __name__ == '__main__':
    try:
        main()
    except (Rejected, Pending) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
