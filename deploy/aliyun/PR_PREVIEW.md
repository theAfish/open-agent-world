# Automated private PR Preview

Phase 3A deploys successful same-repository PR builds to the existing private
Preview. Installation and activation require explicit maintainer approval.
The implementation PR alone does not install credentials or change ECS.

## Flow and trust boundary

1. GitHub's existing `pull_request` checks build and test the synthetic PR merge
   commit. After its frontend acceptance checks, CI packages those exact host and
   Pack outputs through `scripts/aliyun_release.py ci-package`. It uploads
   `oaw-preview-<run_id>-<run_attempt>` with `bundle.tar.gz`, `release.json`, and
   `candidate.json`. These jobs have read-only repository permissions and no
   deployment credentials. Forks do not produce Preview candidates.
2. An independently reviewed ECS controller polls successful Actions runs every
   minute. It runs outside the application and release directories, with its own
   root-private GitHub credential. No inbound webhook, SSH deployment key in
   GitHub, self-hosted build runner, or public Preview ingress is needed.
3. The controller checks repository numeric identity, contributor permissions,
   current open PR and head/base, the current synthetic merge and its parents,
   exact workflow ID/run/attempt, successful required workflows and gate jobs,
   pinned workflow/producer files, artifact identity, timestamps, size and GitHub
   archive digest. Producer metadata is a claim checked against GitHub.
   `workflow_run.head_sha` denotes the PR head; `tested_commit` denotes the
   synthetic merge. They are deliberately recorded separately.
4. Under a durable host lease, the controller temporarily approves only the
   verified archive/commit for the existing installed operator. It uses that
   operator's preparation, backup, migration, verification and rollback. A
   trusted in-process callback rechecks freshness under the operator's actual
   transaction lock immediately before stopping Preview. Requests and artifacts
   cannot supply this callback. The temporary approval is journaled and restored.
5. GitHub deployment records, commit statuses and a sticky PR comment identify
   the tested/deployed revision and private access instructions. A successful
   replacement makes the previous deployment inactive. A failed deployment
   reports the actual installed/restored revision separately.

The controller is intentionally a host polling process rather than an Actions
`workflow_run` job: GitHub runners never receive an ECS deployment credential.
GitHub remains the source of PR events, checks, artifacts and deployment records.

## GitHub configuration

Use the existing repository `theAfish/open-agent-world` (ID `1351393176`) and
integration branch `feat/aliyun-agentcore-poc`. The registered workflows are:

| Workflow | ID | Gate |
| --- | --- | --- |
| `.github/workflows/ci.yml` | 367413344 | CI required |
| `.github/workflows/docs.yml` | 363172125 | Docs required |
| `.github/workflows/desktop-check.yml` | 368742567 | Native required |

Create a dedicated GitHub deployment environment named `preview` only after
approval. This automatic private slot uses the environment for native deployment
tracking. It must have no required reviewers, wait timer, custom protection rules
or branch policy: the controller rejects those configurations. REST deployment
creation does not establish that Actions environment protection gates passed.
If native environment approvals become a requirement, stop activation and use an
approved Actions environment job design; do not remove an existing protection
rule to make this controller run. Existing review and branch protection rules
remain authoritative for merging and are not edited by this workflow.

Provision a repository-scoped fine-grained credential with Metadata read,
Actions read, Contents read, Pull requests read, Deployments read/write,
Commit statuses read/write and Issues read/write. It must be able to read the
collaborator permission and environment endpoints; confirm those endpoints with
the chosen token before activation. Give it no Contents write, Actions write,
Administration write, organization, cloud or production credentials. Keep it
only at `/opt/oaw-preview-controller/github-token`, root:root mode `0600`.
Environment creation is a separate maintainer action, not a token permission.
Do not put the credential in Actions secrets, a build artifact or this repository.

Automatic admission initially requires repository write, maintain or admin
permission for the PR author, original run actor and rerun actor. Fork PRs,
draft/closed PRs, changed pipeline files, old heads/bases/merge commits, old run
attempts, expired/ambiguous artifacts and failed checks are rejected. Pipeline
updates require independent review and a controller policy update to the exact
new trusted revision.

Protected pipeline changes are **not eligible for automatic Preview**. This is
a rejection before artifact download or deployment creation, not a failed
deployment. The existing commit-status API supports only error, failure, pending
and success ([GitHub API](https://docs.github.com/en/rest/commits/statuses)); it
cannot represent neutral/skipped. Keep a non-success authorization status and
an explanatory PR comment stating that no deployment was attempted. Never mark
an excluded revision successfully deployed. The reviewed controller update adds
this explanation automatically without changing admission policy; until that
code is independently installed, a maintainer can post the explanation directly.

An intentional Preview policy exclusion is not a blocker for merging a properly
reviewed infrastructure PR, provided required checks and all other merge
requirements are satisfied. Do not relax branch rules or trust pins to turn an
ineligible Preview green. Later Preview execution requires its own independent
pipeline review and the approved policy-update procedure below.

For a reviewed validation-pipeline update, stop the timer and let the active
transaction finish before changing `trusted_revision`. Verify the current PR/run
again under the new policy. If that attempt was previously recorded in `seen`
only because its validation entrypoint changed, remove only that rejection to
allow fresh admission. Preserve completed results, slot generation and in-flight
state; never clear an unrelated rejection or replay an uncertain transaction.

The service-generator compatibility guard permits verified copies that differ
only by CRLF/LF line endings, as can occur between an initial Windows build and
Linux CI. Raw payload/source inventory hashes remain mandatory. Any other
generator byte change, including removal, still requires an explicit service
upgrade. Python dependency/runtime compatibility checks remain unchanged.

## Exact installation change plan: approval required

Before approval, fill the pins below from the reviewed implementation commit and
read the current ECS installation receipt. Review the resulting bootstrap plan
and the two unit files. Never copy a privileged executable out of a PR artifact.

| Surface | Approved intended change |
| --- | --- |
| GitHub | Add only the dedicated `preview` environment described above. No repository secret, merge rule, review rule or production release trigger changes. |
| Independent operator | Upgrade `/opt/oaw-release-operator-preview` using the existing bootstrap and exact current `installation.json` digest. Only `release_remote.py` changes behavior: an optional trusted freshness callback before switch. The four-file inventory and existing execution/target registration remain pinned. |
| Controller | Install reviewed `preview_controller.py` and `preview_deploy.py` under `/opt/oaw-preview-controller`, root:root `0700` directory, `0644` code; install `config.json` and `github-token` as `0600`. |
| Persistent controller state | Create `/var/lib/oaw-preview-controller`, root:root `0700`, for the host lease, candidate state, current slot and interrupted-authorization journal. |
| systemd | Install only `oaw-preview-controller.service` and `.timer`; daemon-reload; start one inspected service invocation; enable the timer only after acceptance. |
| Preview | Reuse `/opt/oaw-preview-app`, `/srv/oaw-preview-hosting`, `/opt/oaw-preview-releases`, `/opt/oaw-preview-execution`, existing users/units and private Unix ingress. The existing transaction may stop/restart Preview and snapshot/restore its disposable data. |
| Production | No application, provisioning broker, runtime, unit, configuration or data changes. No production restart, deployment or automation. |
| Ingress/cloud | No DNS, TLS, nginx, firewall/security-group, SSH-daemon, new server or cloud-platform changes. Existing SSH tunnel remains the access method. |

1. Pause manual Preview releases while installing; check no transaction is
   active. Record read-only production service identity, configuration/receipt
   fingerprints and existing account/workspace counts using the established
   Phase 2 acceptance procedure. Keep these private; do not publish tenant data.
2. Materialize the reviewed source in a new root-owned review directory outside
   the application. Copy the existing private bootstrap configuration to a new
   Phase 3A configuration. Update only `source_directory` and the four reviewed
   `operator_files` hashes; preserve the exact execution registration, target,
   private loopback settings and existing manual approvals. Pin the live prior
   installation digest with `--upgrade-from`. If an older `.previous` operator
   directory exists, inspect/archive it explicitly rather than replacing it.
3. Run the existing bootstrap plan, inspect it, and apply only the operator step:

   ```sh
   /usr/bin/python3 -I -S /opt/oaw-operator-review-phase3a/bootstrap.py --config /root/oaw-preview-phase3a-bootstrap.json --upgrade-from <CURRENT_INSTALLATION_SHA256>
   /usr/bin/python3 -I -S /opt/oaw-operator-review-phase3a/bootstrap.py --config /root/oaw-preview-phase3a-bootstrap.json --upgrade-from <CURRENT_INSTALLATION_SHA256> --apply
   ```

4. Install the two controller files, credential and
   [configuration](preview-controller.example.json). Replace every placeholder:
   pin `trusted_revision` to the reviewed immutable implementation commit and
   `controller_files` to the independently reviewed code hashes. Set
   `enabled_after` to the approved activation time in UTC. This prevents replay
   of historical successful PR runs. Keep access instructions free of secrets.
5. Install the reviewed [service](oaw-preview-controller.service) and
   [timer](oaw-preview-controller.timer). Verify their syntax with
   `systemd-analyze verify`. The service runs the trusted controller with
   `/usr/bin/python3 -I -S`; never run it from an application checkout. Its write
   paths cover controller/Preview state and the systemd paths needed by the
   existing transaction. App and release directories share one writable `/opt`
   mount so runtime bundle promotion and rollback can use atomic renames.
   Separate writable binds for those directories cause `EXDEV` even when the
   underlying host filesystem is the same. Controller/token, execution runtime
   and independently installed operator code/registration remain read-only;
   the operator directory stays writable for atomic policy-file replacement.
   Inventory `/opt` and add every unrelated existing entry to a root-owned
   `oaw-preview-controller.service.d/10-opt-readonly.conf` drop-in. Keep that
   host inventory private. Review this list again before adding other software
   under `/opt`. Production paths remain inaccessible. The trusted controller
   can create entries in the shared `/opt` parent; application code still runs
   only under the separately reviewed nonroot namespaces.
   Before activation, prove an empty-directory app-to-release rename and return,
   synthetic policy replacement, denied controller/code/token write-open probes,
   and denied production read-open probes under the exact unit properties.
6. Check the exact disposable candidate before enabling deployment:

   ```sh
   /usr/bin/python3 -I -S /opt/oaw-preview-controller/preview_controller.py --config /opt/oaw-preview-controller/config.json verify-run <RUN_ID> <ATTEMPT>
   ```

   This verifies/downloads provenance without writing GitHub statuses or
   deploying. Confirm the output's PR, head/base, tested merge and artifact digest.
7. After the approved first candidate is ready, run
   `systemctl start oaw-preview-controller.service`. Inspect private journal,
   trusted installed receipt and GitHub deployment records. Start the timer
   without enabling it at boot (`systemctl start oaw-preview-controller.timer`)
   for disposable PR acceptance below. Stop the timer if acceptance fails;
   enable it at boot only after acceptance (`systemctl enable oaw-preview-controller.timer`).

## Disposable PR acceptance

Run this only after the exact live plan and credential provisioning are approved.
Use a same-repository disposable branch targeting the integration branch; do not
merge its fault injection. Keep the implementation PR separate.

| Scenario | Required evidence |
| --- | --- |
| Normal update | All three native required workflows pass; candidate artifact binds the actual tested merge; controller automatically updates Preview; receipt, deployment SHA and PR comment agree. |
| Superseded update | Push A then B to one PR. A cannot switch after B changes the PR identity; only the current validated merge is admitted. |
| Invalid provenance | Read-only `verify-run` rejects a previous attempt/run, wrong repository/PR and stale merge. Focused tests additionally exercise altered artifact bytes/digest and mismatched producer metadata without authorizing a live malicious artifact. |
| Competing PRs | Update two disposable PRs concurrently; the host lease and transaction lock serialize the slot. The final receipt matches the active GitHub deployment and its PR comment; the previous occupant is marked inactive/superseded. |
| Controlled failure | Independently inspect a one-line application migration fault that still passes candidate CI, as in Phase 2. Publish it only on a disposable PR. The existing operator must return `failed-rolled-back`; old code, runtime and synthetic data survive; GitHub reports failure and the restored revision. |
| Production preservation | Compare the established production service identity, fingerprints and account/workspace data evidence before/after. No production service/data action is permitted. |
| Private browser | Use the existing foreground SSH tunnel; log in with the disposable account, confirm persistent content and authenticated WebSockets after deployment and rollback. Closing SSH removes local access. |

The shared slot is temporary and can be replaced by a newer eligible PR update.
GitHub reports its occupant independently of application HTML. The loopback
`environment_url` works only through an authorized developer's tunnel; it is
not public access. No account password, private SSH key, cookie or GitHub token
belongs in a PR comment or deployment description.

## Stop and recovery

Disable the timer with `systemctl disable --now oaw-preview-controller.timer` to
stop admitting new candidates. Let an active controller finish: it holds the
host lease through the existing transaction and any automatic rollback. Stopping
the timer does not terminate an in-flight service.

After interruption, do not delete controller state or retry a switch blindly.
The persisted in-flight marker must be reconciled with the operator's private
state and installed receipt; uncertain results stop new admission. Temporary
artifact approvals are restored from the private journal only after excluding
an active transaction. Unexpected policy edits require manual review. Follow
the existing [Preview recovery procedure](PREVIEW.md), including explicit data
restore authorization when needed. A newer successful slot cannot be rolled
back through an older release request.

Keep operator and controller upgrades independent of application deployments.
Retain the reviewed prior operator/configuration for recovery. Revoking the
controller's GitHub token and disabling its timer stops automation without
changing the installed Preview or production. No merge-triggered production
release is enabled by this phase.
