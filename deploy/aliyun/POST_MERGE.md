# Ali integration delivery

Implementation does not authorize installation or a production restart. The
example configuration is **disabled**. First approve installation and one
controlled acceptance deployment; approve unattended updates separately afterward.

## Developer workflow

1. Create a feature branch from `feat/aliyun-agentcore-poc`.
2. Develop, test the affected behavior, and push the branch.
3. Open a PR against `feat/aliyun-agentcore-poc`.
4. Review its automatic [private Preview](PR_PREVIEW.md) and required checks.
5. Merge after review and checks pass, using the existing branch protections.

The team can develop and try a published user view entirely locally using the
[POC recipe](../../examples/poc/README.md). Local changes do not require a cloud
deployment. The GitHub approval mode below is the preferred team delivery path;
the older controlled/routine policies remain available for existing installations.

The merge triggers fresh CI, Native, and Docs checks on the resulting branch
commit. CI packages the frontend and Pack outputs already used by its acceptance
checks into immutable `oaw-integration-<run_id>-<run_attempt>`. An earlier PR
Preview artifact is never the final release. Developers do not supply SHAs,
archives, SSH credentials, or deployment commands.

After controlled acceptance and separate activation, compatible UI/assets,
documentation, and test updates deploy automatically. Backend, runtime,
dependency, migration, Pack contract, workflow, and infrastructure changes stop
for maintainer approval. A gated merge remains installed at the previous revision;
GitHub's `Ali integration deployment` status reports the actual installed SHA.
The path classifier is deliberately conservative; review remains necessary for
behavioral risks inside an allowed UI change.

Infrastructure PRs that change the trusted validation pipeline can be excluded
from automatic Preview even when all required checks pass. The commit-status
reporter cannot represent neutral/skipped; an explanatory PR comment records
**no deployment attempted**. This intentional exclusion is not a merge blocker
for a properly reviewed infrastructure PR when required checks and other merge
requirements are satisfied. Keep the admission restriction intact; see the
[eligibility and policy-update procedure](PR_PREVIEW.md#github-configuration).

## Privileged execution boundary

The former root service used the application venv and imported
`backend.hosting.provisioner`. Its imports include configuration, deployment
storage, worker/shared services, template validation/publication, and their
transitive backend and `open_agent_world` modules. The root resource hook imports
`backend.cloud.aliyun.provisioning`, `scripts.aliyun_workspaces`, Alibaba SDKs,
and Sandbox support. Pinning only the entry script does not protect this closure.

The reviewed `provisioner_guard.py` instead runs with system Python `-I -S`.
It verifies separate, complete source and dependency inventories beneath
`/opt/oaw-provisioner`, including ownership, permissions, file types and hashes;
missing/extra files, links, `.pth` and editable hooks fail closed. It clears the
environment, adds only these sealed import roots to the trusted standard library,
and calls the existing reconciler. The sole resource handler is the existing
Alibaba hook. The source revision and every dependency are independently reviewed
and cannot be upgraded by an application artifact.

This retains the existing queue, account checks, resource reconciliation,
Sandbox/Volume assignments and profile registration. Application initialization
and template updates still execute through each existing nonroot worker unit.
Pack preparation uses the registered nonroot build identity in a separate
runtime; migrations retain each profile's UID, environment and data mounts.
HTTP verification uses an independent nonroot verifier and reviewed stdlib code.
The production gateway owns its worker sockets; the dedicated verifier receives
only socket group access. No tenant gains access to another tenant's data.

The root release operator still performs the existing locked backup, install,
migration, verification and automatic rollback transaction. Native production
workers keep their existing isolation; they do not acquire Preview's runtime
configuration. Neither Preview services nor its controller installation changes.

## Admission and activation

The same controller/adapter source used by Preview is installed separately under
`/opt/oaw-integration-controller`, with state under
`/var/lib/oaw-integration-controller`. It polls GitHub on the existing ECS; no
deployment credential enters an Actions job and no new server or inbound endpoint
is needed. Application artifacts cannot replace controller/operator code.

Admission checks repository name/numeric ID, exact branch head and push event,
workflow IDs/paths, authorized actors, latest runs and attempts, all required
jobs, reviewed producer files, artifact identity/timestamps/digest, and the
candidate/contained archive. It rejects expired, ambiguous, obsolete or
conflicting requests. A host lease spans preparation, switching, rollback and
reporting; the existing release lock and installed receipt bind the transaction.
Freshness, root activation policy and baseline are rechecked immediately before
stopping services. Reporting uses GitHub deployments and commit statuses; failures
include the actual restored revision. Interrupted results are reconciled from the
operator receipt, never blindly replayed.

Modes in [integration-controller.example.json](integration-controller.example.json):

| Mode | Authority |
| --- | --- |
| `disabled` | No admission or deployment. Initial installation state. |
| `controlled` | Root approves one exact run/attempt and current installed commit/receipt. Required for first acceptance and later higher-risk changes. |
| `routine` | Root records an accepted controlled revision already present in controller history. Only complete, forward diffs from its current installed receipt and the conservative path allowlist qualify. |
| `github` | After the same controlled acceptance, require the pinned CI run's successful native environment approval job and an actual maintainer review. Backend changes may qualify; trusted pipeline and host compatibility guards still apply. |

All CI archives retain `routine:false`: even unattended updates use the full
existing per-profile migration and verification transaction. An artifact cannot
request a reduced verification path. Dependency/service generator compatibility
guards continue to reject updates needing a separately planned host upgrade.

## GitHub approval mode

The CI workflow contains two optional jobs, enabled only for **pushes to the Ali
branch** with repository variable `OAW_ALIYUN_GITHUB_DEPLOYMENT=enabled`:

1. `Ali required gates` waits for CI required, Native and Docs on the merged SHA.
2. `Ali deployment approval` targets environment `aliyun-integration-approval`.
   A maintainer clicks **Review deployments → Approve and deploy**. This job has
   no checkout, SSH credentials, candidate execution, or deployment permissions.

The ECS controller still admits the latest push run/attempt and immutable
artifact independently. In `github` mode it additionally checks that the approval
environment has required reviewers and disallows administrator bypass, the exact
attempt's approval job succeeded, and GitHub records an approval for that
environment from a repository maintainer/admin. It rechecks approval and the
installed receipt immediately before switching. GitHub's approval-job success is
only permission; **Ali integration deployment** reports actual installation or
rollback. Never use the approval environment as the controller's output environment:
`aliyun-integration` remains the status destination, without job protections.

PRs, `dev`, `main`, and an unset repository variable skip both jobs. This does not
weaken any required CI job. Changed trusted workflow/build entrypoints still need
an independently reviewed controller pin update. A dependency/systemd/provisioner
upgrade remains an explicit host operation; approval cannot bypass those guards.
Expired artifacts require a fresh CI attempt and approval, not a manual SHA.

### Reviewed activation plan for the existing ECS

Read-only inspection on 2026-11-09 found production at
`59255fc14fab13bc13c3846423b71df7e080addd`, with the independent production operator,
build/verify execution runtime and sealed provisioner already installed. Private
Preview is active. The integration controller is absent; the root filesystem has
about 7.6 GiB free. The older installation inventory below describes 2026-11-03,
not the current host. **No installation or restart was performed by this change.**

After this PR passes required CI and its exact activation is approved:

1. Merge the reviewed PR, wait for the new Ali **push** CI, Native and Docs gates,
   and select the run/attempt automatically from that resulting branch head. Keep
   the GitHub opt-in variable unset during initial controlled acceptance.
2. Create GitHub environment `aliyun-integration-approval`, restrict it to the Ali
   branch, set `theAfish` as required reviewer and `can_admins_bypass=false`.
   Self-review may remain allowed for the initial single-maintainer team. Require
   a normal environment approval even for administrators. Do not change branch
   protections, repository secrets, the `preview` environment or its credentials.
   Also create the status-only `aliyun-integration` environment with no protection
   rules or branch policy (it is absent in the inspected repository). The two
   environments deliberately separate permission from the deployment result.
3. Install only the reviewed `preview_controller.py` and `preview_deploy.py` under
   `/opt/oaw-integration-controller`; code is root-owned `0644`, directory `0700`.
   Install the exact file hashes and reviewed producer revision in root-private
   `config.json`. Provision a separate root-private GitHub token with the existing
   controller permissions (Actions/Contents/Environments read; Deployments and
   Commit statuses write; collaborator permission visibility). Never place it in
   Actions or an application-readable path. Reuse authorized credentials only
   if their scope and lifetime are suitable; otherwise request a token from the
   repository administrator through the existing private configuration directory.
4. Create `/var/lib/oaw-integration-controller` as root `0700`. Install the existing
   controller service/timer, adding `ReadOnlyPaths` for every unrelated current
   `/opt` entry. Leave the timer disabled. Do not replace the trusted publisher,
   execution runtime or sealed provisioner, and retain the shared host lease.
   Under that same lease and with no in-flight Preview transaction, update the
   existing Private Preview controller's two reviewed files, file inventories and
   trusted producer revision to this merge. Its admission must understand the two
   optional skipped CI jobs. Preserve its environment, state/history, token and
   Core/mock isolation. Restore its existing timer afterward; do not replay PRs
   previously excluded by the older trust pin.
5. Bind `controlled` mode to the selected successful run/attempt and current
   installed commit/receipt, then run one controller transaction. This reuses
   nonroot preparation/migration/verification and the existing restore path.
   Verify the resulting receipt, login and the admin's **Git: 实验工作台** entry.
   Do not assign/reset user workspaces, create Sandbox VMs or call a paid model.
6. Only after successful controlled acceptance, retain its accepted revision and
   set mode `github`, then set the GitHub opt-in variable and enable the timer.
   Future eligible merges await a maintainer's native GitHub approval. Report
   completion of the first real approval/installation separately from local tests.
7. If initial acceptance fails, keep the timer and opt-in variable disabled and
   report the publisher's actual restored revision. Disable new admissions with
   `systemctl disable --now oaw-integration-controller.timer`; never interrupt or
   replay an in-flight transaction. Its existing reconciliation handles recovery.

This plan creates two GitHub environments, sets one opt-in variable, installs the
integration controller and updates the existing Preview controller on the ECS.
It does not change Nginx, DNS/TLS, firewall, cloud resources, user
accounts, model/environment settings, user data or Sandbox/Volume bindings. The
first controlled application deployment briefly restarts the existing services.

Git scenes arrive inside the validated application bundle and are exposed by the
nonroot builder using existing publication/queue APIs. Choosing target accounts
is still an admin action. Legacy online-authored workspaces have different source
lineage: importing a Git scene must not silently adopt, reset or replace them.

## Installation plan: approval required

Perform these steps only after approval of their exact pins, identities, unit
diffs and maintenance window. Save the prior operator, configuration and unit
files; do not clear existing release backups or controller history.

Read-only inspection on 2026-11-03 found no production operator, sealed
provisioner, production execution runtime or integration controller installed.
The production gateway was active, and its existing provisioner unit still used
root. The initial operator step is therefore a **fresh installation**, subject
to rechecking those facts at approval time. Host system Python was 3.14.4; build
the sealed provisioner's native dependencies for that interpreter, separately
from the explicitly registered Python 3.12 release-build runtime. Never substitute
the running application's venv into the privileged import path.

1. Read the live receipt, execution registration, unit definitions and existing
   accounts/profile/resource inventories. Record private persistence evidence and
   free-space checks. Exclude active user work and release/provisioning jobs.
   Prepare the existing release transaction's required immutable runtime bundle;
   retain all existing Python/scientific dependencies and per-profile environments.
2. On a nonroot Linux staging account, materialize `backend`, `scripts` and
   `open_agent_world` from the independently reviewed commit. Export dependencies:

   ```sh
   uv export --project backend --locked --no-dev --no-emit-project \
     --extra hosting --extra aliyun --extra aliyun-operator \
     --output-file /path/to/staging/requirements.txt
   uv pip install --python /usr/bin/python3 --target /path/to/staging/dependencies --link-mode copy \
     --require-hashes -r /path/to/staging/requirements.txt
   ```

   Use the ECS system Python version/ABI and architecture. Inspect any required
   wheel builds as nonroot. Stage only reviewed source/dependencies, without
   venvs, editable installs, bytecode caches or site startup hooks. Copy them as
   real files to `/opt/oaw-provisioner/source` and `dependencies`, root-owned and
   nonwritable by other users. Do not copy production secrets or tenant data.
3. Prepare `/opt/oaw-production-execution`, the independent self-contained release
   runtime, with its registered Python 3.12 interpreter/shared libraries, locked production
   Python dependencies, `uv`, and required offline Pack wheels/runtime tools.
   Reuse the existing bootstrap runtime contract: root-owned real files, no links,
   empty `/workspace/{source,previous,scratch,sockets}` directories and empty
   `release_worker.py`, `release_verify.py`, `verification.json` placeholders.
   Test preparation under the actual nonroot namespace before a service switch;
   it has no Internet or production credentials. Do not bind the production venv
   into this build runtime. Existing worker venvs and scientific environments stay
   in place; changed host dependencies require a separate upgrade plan.
4. Use [bootstrap.production.example.json](bootstrap.production.example.json),
   preserving an existing reviewed production configuration if present. Register
   target `/opt/open-agent-world`, `/srv/oaw-hosting`, `/opt/oaw-releases`, service
   `oaw-hosted`, sandbox `/opt/oaw-production-execution`, and distinct
   `oaw-production-build`/`oaw-production-verify` identities. Review its plan.
   Install the five reviewed operator files (`bootstrap.py`, `release_remote.py`,
   `release_worker.py`, `release_verify.py`, `provisioner_guard.py`) through
   `bootstrap.py --config <private-config> [--upgrade-from <current-installation-digest>] --apply`,
   invoked by `/usr/bin/python3 -I -S` from an independent root-owned review
   directory. Preserve the exact target and existing manual approvals. This step
   neither starts services nor upgrades application code.

   Exact commands from the independently reviewed, root-owned staging directory:

   ```sh
   /usr/bin/python3 -I -S /opt/oaw-operator-review-phase3b/bootstrap.py \
     --config /root/oaw-production-phase3b-bootstrap.json
   # Only after the installation plan is approved; fresh operator installation:
   /usr/bin/python3 -I -S /opt/oaw-operator-review-phase3b/bootstrap.py \
     --config /root/oaw-production-phase3b-bootstrap.json --apply
   ```

   For an existing operator, add `--upgrade-from <CURRENT_INSTALLATION_SHA256>`
   to both commands, using the current `installation.json` digest obtained
   read-only. Preserve its execution registration and manual artifact approvals
   in the reviewed configuration. Retain the old installation; inspect/archive
   any `.previous` directory before an upgrade. Placeholder hashes in the example
   deliberately prevent installation until the reviewed files are pinned.
5. Fill [provisioner.example.json](provisioner.example.json), including the reviewed
   source revision, target and verifier identity. Generate complete inventories
   only after independently inspecting both installed trees:

   ```python
   # Run with trusted system Python -I -S; do not import staged application code.
   import hashlib, json
   from pathlib import Path
   path = Path('/root/oaw-provisioner-reviewed.json')
   value = json.loads(path.read_text())
   value['files'] = {
       key: {p.relative_to(Path(value[key])).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(Path(value[key]).rglob('*')) if p.is_file()}
       for key in ('source', 'dependencies')}
   path.write_text(json.dumps(value, indent=2) + '\n')
   path.chmod(0o600)
   ```

   Install the reviewed result as `/opt/oaw-release-operator/provisioner.json`
   (`root:root`, `0600`). Run `provisioner_guard.py check` using system Python
   `-I -S`; this validates files without application imports. Review `units`
   output and the `socket-units` JSON output, which renders exact worker socket
   files through the sealed source without changing the host. During the approved window,
   stop the old provisioner timer/service, install the exact reviewed units and
   daemon-reload. Install the reviewed `socket-units` files, retaining gateway ownership
   while granting the dedicated verifier group `0660`; restart affected sockets
   with their workers stopped, then restore them. Do not change profile UIDs,
   secrets, mounts or upstream paths. Test the real sealed import closure and an
   idle tick under the fixed unit before restarting the trusted timer.

   **Supersedes older ECS instructions:** never use the application venv as root
   for `provisioner enable`, `engineering`, or `install-systemd` after installing
   this boundary. Use the installed guard for privileged reconciliation and
   explicitly reviewed operator tooling for infrastructure changes.
6. Install the same two reviewed controller source files under
   `/opt/oaw-integration-controller` (directory `0700`, code `0644`), its private
   config/token (`0600`) and state directory (`0700`). Set reviewed code hashes,
   `trusted_revision`, and `enabled_after` (UTC); keep activation **disabled**.
   Keep the token only on ECS, scoped to this repository: Metadata, Actions,
   Contents and collaborator/environment reads; Deployments and Commit statuses
   read/write. No Contents/Actions/Administration write or cloud credentials.
   Confirm endpoint permissions before activation.
7. Separately approve a dedicated GitHub environment `aliyun-integration` for
   tracking, with no protection rules or branch policy. This polling controller
   cannot enforce Actions environment approval rules and refuses them; do not
   remove existing protections. Root-controlled activation supplies the explicit
   gate. Existing PR review/branch protections remain unchanged.
8. Inspect [service](oaw-integration-controller.service) and
   [timer](oaw-integration-controller.timer), adding read-only bindings for
   `/opt/oaw-provisioner` and every unrelated `/opt` entry. Controller code/token,
   operator code/registrations, and execution runtime remain read-only. Keep app
   and release directories in one `/opt` mount for atomic promotion/rollback.
   Verify syntax and actual namespace rename, policy replacement, code-write
   denial and Preview-access denial probes. Install units and daemon-reload;
   **leave the timer stopped and disabled**.

Verify a current successful push build without deploying:

```sh
/usr/bin/python3 -I -S /opt/oaw-integration-controller/preview_controller.py \
  --environment aliyun-integration verify-run <RUN_ID> <ATTEMPT>
```

This command downloads/validates provenance only. Pipeline changes in this PR
also need an independent pin update before the existing Preview controller will
admit it; a PR artifact cannot update that policy itself.

## First controlled acceptance: separate approval

Operator checklist (live ECS required; no step is performed by this PR):

- [ ] Approve the reviewed operator/provisioner inventories, exact unit diffs,
  execution runtime and maintenance window; verify backups and idle user work.
- [ ] Keep integration automation disabled and its timer stopped. Install only
  the reviewed boundary and verifier socket permissions; confirm an idle sealed
  provisioner tick and nonroot filesystem/network denials.
- [ ] Verify the actual post-merge push artifact, required workflows and exact
  installed baseline. Approve one run/attempt and start one service invocation.
- [ ] Check every worker's cold startup, existing-account login, profile and
  conversation/file persistence, tenant isolation and existing runtime/Sandbox
  assignments. Confirm GitHub and the installed receipt report the same SHA.
- [ ] With explicit data-restore approval and no intervening tenant writes,
  exercise current-release rollback; confirm original code, runtime, data and
  login. Revalidate and approve any reinstall against the restored baseline.
- [ ] Retain private evidence. Keep unattended mode disabled pending a separate
  acceptance decision; no automatic enabling is part of installation or rollback.

Bind `activation.approved_run` to `{run_id, run_attempt, baseline_commit,
baseline_receipt}` read from the current verified run and operator status, set
mode `controlled`, and start **one** `oaw-integration-controller.service`
invocation. Do not start its timer. The exact same approval cannot replay a
completed transaction. Recheck branch/run freshness if a newer merge arrives.

Acceptance must establish:

| Boundary | Required evidence |
| --- | --- |
| Installed revision | Push SHA, artifact, operator receipt, GitHub deployment and status agree. |
| Startup and authentication | Gateway and every worker cold-start; anonymous/runtime boundaries remain enforced; real existing accounts can log in. |
| Persistence and isolation | Existing account IDs, profile/card/conversation identities, settings and files survive; user environments and Sandbox/Volume assignments are unchanged and remain separate. |
| Runtime compatibility | Existing approved model/Sandbox/scientific tools remain available under their original identities; paid operations require their own explicit acceptance authorization. |
| Rollback | Demonstrate the approved snapshot restore procedure in the controlled window, verify restored code/runtime/data and login, then revalidate the candidate before reinstalling it. No fault injection into tenant data. |
| Recovery/reporting | Installed/restored SHA is reported accurately; stale/competing requests cannot switch; uncertain recovery freezes admission. |

Restoring a data snapshot can discard writes after its timestamp. Obtain explicit
data-restore approval and keep the service quiescent during this acceptance.
Routine mode requires another approval after all evidence passes: set
`accepted_revision` to the successful controlled revision in state, clear
`approved_run`, set mode `routine`, then start/enable the timer. Never manufacture
controller history to satisfy this gate. A run already consumed by the controller
cannot be replayed after manual rollback. A later reviewed integration push/run
must complete a fresh controlled deployment before routine mode can be enabled
against that installed baseline. Higher-risk changes keep using one
baseline-bound controlled approval; runtime/service changes require their own
upgrade plan first.

## Stop and rollback

`systemctl disable --now oaw-integration-controller.timer` stops new admission.
Let an active service finish its transaction/rollback; stopping the timer does
not cancel it. Set activation to `disabled` to revoke future switches. Preserve
state, in-flight markers, authorization journals and release logs.

The release directory records the release ID, original baseline, backups and
actual transaction state. Ordinary failure automatically restores code, runtime
bundle and data. For an explicit current-release rollback, after data-restore
approval first re-authorize that exact current release, then use the existing
operator/client procedure. The controller deliberately removes its temporary
archive authorization when a deployment finishes; a later manual rollback must
not inherit it. With timers stopped and no active transaction, read the installed
receipt and retained release `request.json`; verify the release ID, source commit
and archive digest agree, and verify the retained archive bytes. Independently
approve only that `{archive_sha256, source_commit}` entry in the root-owned
`trusted-artifacts.json`, preserving existing target/approvals and private file
permissions. Save the exact previous policy for restoration. Do not approve
values copied from an unverified artifact or weaken `approved_release` checks.

```sh
python scripts/aliyun_release.py --config <private-release-config> \
  rollback --release <CURRENT_RELEASE_ID> --restore-data
```

Use independently reviewed local tooling; it calls the installed trusted
operator. An older release cannot overwrite a newer installed receipt. After an
interruption inspect private state/receipt before recovery; never delete state
to retry. Once the rollback task is terminal, restore the saved authorization
policy; do not revoke authority from a still-running transaction. After a manual
rollback use controlled approval to establish the next
controller baseline. Restore a prior operator/configuration only with both
timers stopped and no active transaction. Do not restore the unsafe application
root provisioner as a shortcut.

## Changes needing maintainer approval

- Independently reviewed provisioner source/dependencies and their complete
  inventories; production operator installation/upgrade and private registrations.
- The separate release runtime and nonroot build/verifier system identities.
- Replacing the root provisioner unit and existing worker socket group/mode;
  restarts in an approved maintenance window, without changing tenant identities.
- Host controller/config/token/state, exact systemd namespace bindings, new GitHub
  tracking environment and pipeline pins. No merge-rule or Actions-secret change.
- One exact real deployment and rollback/data restore acceptance, including any
  explicitly selected paid runtime checks.
- A later, separate decision to enable unattended compatible updates; every
  higher-risk release or independent controller/provisioner/runtime upgrade.

No installation, production deployment, data restore, credential provisioning or
unattended activation is authorized by merging the implementation PR alone.

## Local release-readiness checks

Controller/operator/provenance/archive/rollback tests need no ECS. POSIX
ownership, symlinks and `flock` need Linux, but still run locally. The checklist
above needs the actual ECS identities, systemd namespaces, sockets, installed
runtime bundles and existing private accounts/data; local tests cannot establish
those acceptance boundaries.

The hosted upgrade suite exercises real installed Pack declarations. A Core-only
environment lacks the optional ADK/LiteLLM providers and the declared Knowledge
Base/Library converters, causing migration to correctly reject startup. Prepare
only those declarations in a disposable Linux environment before rerunning:

```sh
UV_PROJECT_ENVIRONMENT=/tmp/oaw-phase3b-tests uv sync --project backend --locked \
  --extra hosting --extra aliyun --extra aliyun-operator --extra adk --extra litellm --group dev
uv pip install --python /tmp/oaw-phase3b-tests/bin/python \
  --editable ./plugins/knowledge_base --editable ./plugins/library
/tmp/oaw-phase3b-tests/bin/python .github/run-backend-tests.py \
  backend/tests/test_hosted_upgrades.py -p no:cacheprovider
```

These are local test dependencies already declared by the affected Packs.
Production dependencies and migration failure handling remain unchanged. Do not
skip Pack failures or classify missing dependency wheels as a hosting-policy
exclusion to make a migration pass.
