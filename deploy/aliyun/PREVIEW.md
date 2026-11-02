# Shared Preview on the existing ECS

Phase 2 provides one disposable Preview slot and a separately installed trusted
operator. It does not enable automatic GitHub Actions deployment or production
deployment. Server installation, service changes, DNS, TLS and nginx changes
require the operator's explicit approval of the concrete plan.

The first iteration serves the real hosted login, canvas, Core cards, persisted
workspaces, mock Conversation turns and authenticated event WebSockets. Paid
models, model discovery/configuration, cloud Sandbox operations, external Packs
and production cloud credentials are disabled. `core.mock` produces deterministic
responses; this is not acceptance of a model provider or an Alibaba Sandbox.

## Address and browser boundary

The existing production origin uses an IP address and its certificate covers that
IP. A Preview hostname and matching TLS certificate must be approved and verified
before publishing Preview. `https://preview.example.invalid` is a placeholder,
not a provisioned address. The final exact HTTPS origin is pinned in the trusted
configuration after the read-only nginx/TLS audit.

Live installation remains blocked until the approved hostname is supplied. There
is no temporary Preview address to publish or substitute in the meantime.

Use a distinct hostname. A path on the production origin is the same browser
origin. Another port on the same IP/hostname still receives host cookies, even
though it is a different origin. Neither is an acceptable Preview login boundary.
Keep authentication cookies host-only, omit `Domain`, and verify that production
cookies are not transmitted to Preview in a real browser. A Preview hostname
under a shared parent also requires checking production cookies for an existing
parent-domain scope.

The existing gateway enforces its exact configured origin, authenticates its own
accounts, selects the account's server-owned worker, strips browser cookies and
authorization before forwarding, and issues a short-lived assertion for that
exact profile. Production and Preview never share account/session databases,
worker secrets, OIDC configuration or browser storage origins.

## Application and service composition

`backend.hosting.preview` reuses the account store, gateway, worker identity,
profile binding, database, card lifecycle, Conversation delivery and event stream.
The Preview service factory uses Core cards only, `core.mock`, no Sandbox backend,
no marketplace, and no inherited runtime environment. `google.adk` is explicitly
unavailable, including when a request selects it on a card. The Preview HTTP
policy rejects paid model configuration/discovery and Sandbox mutations. Existing
hosted policy continues to reject host paths and host administration.

The application factory is a product policy, not a security boundary for PR code.
Reviewed operating-system restrictions must apply before importing an artifact:

| Boundary | Required Preview setting |
| --- | --- |
| Code and dependencies | Separate immutable release/application directories; separate prepared venv; no production venv or writable dependency cache |
| Accounts and files | Separate hosting root, accounts database, profile databases, uploads, assets, logs and backups; synthetic fixtures only |
| Settings and keys | Independent profile binding and random worker secrets; encrypted settings and their keys remain profile-owned and are never copied from production; no model/cloud credentials in this iteration |
| Service identity | Independent gateway/worker Unix users, socket names and systemd units; no sudo, Docker socket or production service-control permission |
| Filesystem | `RootDirectory` points to the separately prepared Preview runtime; only the canonical Preview app and prescribed per-service data/socket views are bound in; production application, hosting, release and operator roots are absent |
| Worker network | `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`, `IPAddressDeny=any`; only its inherited private Unix socket |
| Gateway network | `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`, `IPAddressDeny=any`; socket-activated Unix ingress from nginx and Unix sockets to Preview workers |
| Capacity | Gateway `MemoryMax=128M`; each of two workers `MemoryMax=384M`; each service `TasksMax=128`; no production limit changes |

Each service uses `MountAPIVFS=yes`, `PrivateIPC=yes`, `ProtectProc=invisible`
and `ProcSubset=pid`. The gateway binds only the read-only app, its own read-only
`/run/oaw-preview` socket directory and its writable control view. A worker binds
only the read-only app/own instance view and its writable data. No host `/run`,
host `/proc`, production socket or system bus is mounted into the runtime.

The gateway ingress is `/run/oaw-preview/gateway.sock`, owned by its Preview
service identity with nginx's `www-data` group and mode `0660`. nginx proxies
HTTP and upgraded WebSockets through that Unix socket; no Preview TCP port is
published. The gateway and workers cannot create TCP connections, including to
production loopback listeners. Verify these denials under the actual units.
Production still needs its own authentication and private filesystem permissions.
This configuration shares the host kernel and
does not promise containment against malicious kernel exploits, resource
exhaustion outside the configured limits, or unknown hostile PR code. Only
reviewed, explicitly allowed same-repository contributors/branches may enter this
slot. Fork and otherwise untrusted PRs remain in secret-free CI; dynamic execution
of hostile code requires a separately isolated VM/Sandbox boundary.

The existing root provisioner imports `backend.hosting.provisioner` from the
application directory. Its `prepare_resources` reads the root-held
`resource-provisioner.json`, then calls `importlib.import_module` for the configured
handler; the current configuration example selects
`backend.cloud.aliyun.provisioning:prepare_profile`. That import can execute the
application's transitive imports as root before a profile's unprivileged
initialization begins. A handler-name allowlist or a digest for the entry script
does not close that import chain. Preview installs no provisioner service/timer;
the new operator refuses production deployment until a separate reviewed
provisioning broker closes that boundary. Existing production is not modified by
this work.

No production Volume, Sandbox
assignment, settings file, encrypted credential, cloud key, instance role access,
SSH key, GitHub write token or production data snapshot belongs in Preview.
Worker units do not load `worker-common.env` or `worker.env`.

## Reviewed bootstrap and nonroot initialization

Start from [bootstrap.example.json](bootstrap.example.json), replace every
placeholder, and inspect the bootstrap plan before approving any server change.
Install `bootstrap.py` and the trusted operator from an independently reviewed
revision outside all application/release directories. The privileged process
must use its own pinned Python and standard-library code. Root must not execute
or import the artifact's application, Pack, helper, recipe or venv. A later app
update cannot update the trusted operator. Unit content and the complete installed
inventory must be pinned and inspected independently of claims in the artifact.

The independently prepared execution root must contain a complete reviewed
Python runtime/dependencies and `/usr/bin/env`, using root-owned real files and
directories without symlinks, hardlinks, special entries or group/world writes.
Its configured Python (for example `/usr/bin/python3.12`) must be executable.
Create these empty bind placeholders inside that root, using the sample paths:

```text
/workspace/source/                  /workspace/previous/
/workspace/scratch/                 /workspace/sockets/
/workspace/release_worker.py        /workspace/release_verify.py
/workspace/verification.json
/opt/oaw-preview-app/               /srv/oaw-preview-hosting/
/srv/oaw-preview-hosting.passwords.json
/var/lib/oaw-preview-control/       /var/lib/oaw-preview-profile/
/run/oaw-preview/
```

The three `/workspace` helper/JSON entries and password placeholder are empty
regular files; the remaining entries are empty directories. The app's separate
`backend/.venv` and `backend/.venv/oaw-runtime-bundle` are also independently
prepared, root-owned and reviewed. The venv must contain the backend base and
hosting dependencies plus its compatible Linux interpreter/libraries. Preparing
the app and runtime also requires directories readable/traversable by the
Preview service UIDs (normally `0755`), readable dependency files (`0644`) and
executable interpreters/libraries where required (`0755`). Verify those permissions
from the actual restricted service identity before starting the gateway.
Preparing these offline bundles is an explicit prerequisite; bootstrap does not install
packages, fetch dependencies, construct a chroot, or run root `pip`/application
setup. Do not bind production's runtime or venv into Preview.

Initialization is a one-time, nonroot action in a new empty Preview hosting root:

```sh
python -m backend.hosting.preview initialize \
  --directory /srv/oaw-preview-hosting \
  --origin https://preview.example.invalid \
  --service oaw-preview \
  --password-file /srv/oaw-preview-hosting.passwords.json
```

Use the reviewed bootstrap's restricted execution mechanism, not a bare root
shell invocation. The password file is a separate owner-only regular file
containing exactly `alice` and `bob`, with distinct operator-generated passwords
of at least 12 characters. Keep it outside the empty hosting directory; it must
not enter an artifact or a log. Replace the placeholder origin before use.

Each account gets its own randomly identified builder profile, random worker
secret and independent data directory. Each fixture contains a text note, a mock
agent and a connected Conversation. `fixture-receipt.json` records only fixture
IDs and disabled capabilities; it is application output, not trusted release
provenance. Initialization refuses to overwrite existing accounts or workspaces.
Export needed Preview results before separately approved fixture replacement.

Generate the service bundle as an unprivileged user using the same reviewed
version. Pin every actual production root rather than copying these examples:

```sh
python -m backend.hosting.preview units \
  --directory /srv/oaw-preview-hosting --application-directory /opt/oaw-preview-app \
  --service oaw-preview --verify-user oaw-preview-verify \
  --execution-root /opt/oaw-preview-execution \
  --production-root /opt/open-agent-world \
  --production-root /srv/oaw-hosting \
  --production-root /opt/oaw-releases \
  --production-root /opt/oaw-release-operator
```

Generation writes a new `systemd/oaw-preview` bundle and prints file digests. It
does not install or start services. The separately pinned bootstrap validates
the full bundle and installs only administrator-reviewed bytes. Do not pass this
Preview bundle to the generic `install-systemd` command: the generic installer
regenerates the normal hosted policy and imports application hosting code as root.

## First manual deployment procedure

These commands describe the separately approved deployment; none are executed by
the local tests. Freeze a clean reviewed source commit first. Use a distinct
local Preview configuration based on
[release.preview.example.json](release.preview.example.json); never reuse the
production configuration. Replace the hostname, SSH identity/target and local
paths, while preserving the independently approved Preview server paths.

1. Build the exact reviewed commit on the nonprivileged build machine. This
   command does not connect to ECS:

   ```powershell
   python scripts/aliyun_release.py build --source 'D:/path/to/clean-reviewed-checkout' --output 'D:/path/to/preview-artifacts' --python 'D:/path/to/build-python.exe'
   ```

   Pin the resulting exact artifact directory, `release.json` archive digest and
   source commit. Do not select a later `latest.json` implicitly. Independently
   review those values before putting the digest/commit pair in the bootstrap
   configuration's `approved_artifacts`.

2. Prepare the separate execution root and initial app venv/runtime bundle using
   the offline prerequisites above. At this point the app directory contains only
   its reviewed `backend/.venv`; application source is installed by the seed
   action. Stage the four reviewed operator files in
   `/opt/oaw-operator-review-v1`, owned by root and outside every application,
   hosting, release and runtime root. Store the completed bootstrap configuration
   at `/root/oaw-preview-bootstrap.json` with mode `0600`.

3. On ECS, generate and review the bootstrap plan with the independently reviewed
   standard-library entry point. Run the second command only after approval of
   that plan:

   ```sh
   /usr/bin/python3 -I -S /opt/oaw-operator-review-v1/bootstrap.py --config /root/oaw-preview-bootstrap.json > /root/oaw-preview-bootstrap.plan.json
   /usr/bin/python3 -I -S /opt/oaw-operator-review-v1/bootstrap.py --config /root/oaw-preview-bootstrap.json --apply
   ```

   This installs the independent Preview operator and its registered nonroot
   identities. It does not initialize accounts, start services or configure
   routing. An upgrade requires the exact prior `installation.json` digest;
   application deployment cannot silently upgrade the operator.

4. From the build machine, seed the approved fixed portable artifact into the
   initial Preview app and write its baseline receipt:

   ```powershell
   python scripts/aliyun_release.py --config 'D:/path/to/preview-release.local.json' seed --artifact 'D:/path/to/preview-artifacts/EXACT_RELEASE_ID'
   ```

   The Preview-only seed refuses existing source/receipts and unapproved
   archives/commits. Root validates/copies source files and writes the receipt
   without importing the app or executing its venv. It does not change hosting
   data or services. A second seed is not an update mechanism.

   If seeding is interrupted, `seed-installing` and a partial app can remain.
   Inspect the exact release state, app inventory and any receipt first. Never
   retry over existing source, add a force/overwrite flag, or synthesize a receipt
   for a partial tree. Recovery requires separate approval to stop any affected
   Preview services, preserve the failed disposable Preview app/state for
   inspection, recreate only the exact registered Preview app path with its
   independently prepared venv/runtime bundle, and seed the reapproved fixed
   artifact into that empty source tree. Recheck target paths before this reset;
   production paths, production receipts and hosting data are outside its scope.

5. Create the empty hosting root owned by the registered setup identity and
   stage its separate password file, using the approved exact sample paths:

   ```sh
   install -d -o oaw-preview-setup -g oaw-preview-setup -m 0700 /srv/oaw-preview-hosting
   install -o oaw-preview-setup -g oaw-preview-setup -m 0600 /root/approved-preview-passwords.json /srv/oaw-preview-hosting.passwords.json
   ```

   Review `preview_preparation.initialize` and
   `preview_preparation.generate_units` in the root-private plan. Execute those
   exact argument arrays in order using the trusted standard-library Python:

   ```sh
   /usr/bin/python3 -I -S -c 'import json,subprocess; p=json.load(open("/root/oaw-preview-bootstrap.plan.json")); subprocess.run(p["preview_preparation"]["initialize"],check=True); subprocess.run(p["preview_preparation"]["generate_units"],check=True)'
   ```

   Each array invokes `systemd-run` with the setup UID, `RootDirectory`, private
   network, read-only app/password binds and a writable Preview hosting bind.
   Python imports of fixture/application code occur only after those restrictions
   apply. The plan is root-owned reviewed input; never execute an array from an
   artifact or an unprivileged generated plan.

6. Inspect all eight generated files (gateway socket/service, both worker
   socket/services, target and `sysusers.conf`). Stage root-owned regular copies
   outside the app/hosting/releases in `/opt/oaw-preview-unit-review-v1`; reject
   links and unexpected entries. Set `preview.unit_source_directory` to that
   path and `preview.unit_files` to the exact reviewed filename/digest map.
   Regenerate and review the unit installation plan before approval:

   ```sh
   /usr/bin/python3 -I -S /opt/oaw-operator-review-v1/bootstrap.py --config /root/oaw-preview-bootstrap.json --step preview-units
   /usr/bin/python3 -I -S /opt/oaw-operator-review-v1/bootstrap.py --config /root/oaw-preview-bootstrap.json --step preview-units --apply
   ```

   The installer compares the complete independent unit policy/inventory,
   registers the distinct service identities, assigns only Preview ownership and
   installs units. It keeps them stopped/disabled and does not change nginx.
   Review the host-wide nonoverlap schedule before starting any Preview service.

7. After the OS isolation probes pass and startup is approved, explicitly start
   only the Preview target:

   ```sh
   systemctl start oaw-preview.target
   ```

   Seeding the initial receipt and running `deploy` again for the same SHA can
   return `already-current`; it is not a first-start action. Fixture initialization
   creates an empty `control/deployments.sqlite3` before any gateway starts, so
   the normal release idle check can run against this fresh installation.
   Prepare the trusted public status projection and the
   nginx template below with the final distinct hostname/TLS certificate. Keep
   the status directory root-owned (`0755`) and its public JSON root-owned
   (`0644`), outside the artifact. Review the exact nginx diff, run `nginx -t`,
   and reload nginx only after approval. No DNS, certificate or nginx change is
   automated here. Complete the browser/HTTPS acceptance checklist before
   marking the slot accepted.

8. For later approved switches, build a new fixed artifact, independently add
   its exact digest/commit to the trusted approval policy, and use the normal
   baseline-bound Preview path:

   ```powershell
   python scripts/aliyun_release.py --config 'D:/path/to/preview-release.local.json' deploy --artifact 'D:/path/to/preview-artifacts/EXACT_RELEASE_ID' --check
   python scripts/aliyun_release.py --config 'D:/path/to/preview-release.local.json' deploy --artifact 'D:/path/to/preview-artifacts/EXACT_RELEASE_ID'
   ```

   `--check` still prepares dependencies and can run nonroot artifact code; it is
   a server mutation requiring approval. Recheck the pinned baseline and update
   the separately reviewed trusted status/header projection after the switch.

## Trusted version identity and switching

The installed receipt currently identifies the source commit and release; it
does not establish GitHub PR/head/base/merge/run provenance. `/api/application`
and application HTML are artifact-controlled and cannot establish trusted release
identity.

[nginx.preview.conf.example](nginx.preview.conf.example) and
[preview-status.example.json](preview-status.example.json) provide a minimal
manually reviewed status route and immutable response headers. They are templates,
not installed routing. Before live acceptance, install a root-owned public status
projection outside the app, replace every placeholder using the independently
approved artifact and installed receipt, and review the matching nginx headers.
Keep GitHub identity fields null in this phase. The application version is
independently reviewed release metadata, not proven by the application response.
Do not expose the full receipt or source inventory. Rerender/review the status
projection and headers after every switch; stale identity blocks acceptance.

The browser-visible identity and immutable response header must agree with that
receipt after cold start. Replacing the shared slot invalidates the previous
occupant's acceptance. This phase accepts only manually approved, pinned trusted
artifacts; it does not implement GitHub head/base/merge/run provenance validation
or make an artifact's self-reported PR identity authoritative.

The current operator uses a per-environment release-directory lock. The legacy
production workflow can still run concurrently with Preview; there is no shared
host deployment lock or controller lease yet. Manual scheduling must exclude
overlapping production/Preview changes, and automated or concurrent deployment
remains blocked until host-wide coordination is implemented and tested. Switching,
backup, interrupted-transaction recovery and rollback use the trusted operator's
pinned protocol. An unknown transaction or stale occupant cannot be cleared by
blindly retrying. Failed recovery keeps the affected environment stopped. Never
copy Preview data back into production or restore an older transaction over a
newer receipt.

## Acceptance and evidence limits

The local focused tests are `backend/tests/test_hosted_preview.py`. They exercise
real ASGI HTTP login and gateway-to-worker routing, real database persistence,
worker TestClient WebSocket authentication/events, and a complete persisted mock
Conversation turn. The in-process dispatch transport selects real ASGI apps; it
does not fabricate their responses. Negative checks cover foreign session
rejection, cross-profile reads, host storage administration, paid-provider
selection, model discovery/configuration, Sandbox actions and root CLI refusal.
The unit renderer is checked without starting systemd.

These tests do not validate Linux mounts/cgroups/seccomp, nginx, TLS, browser
cookie behavior, real Unix-socket gateway WebSockets, Alibaba resources, external
models, ECS load, or production recovery. A mocked system command is not live
infrastructure evidence.

After explicit approval, record acceptance separately for each required boundary:

- Confirm the reviewed operator/interpreter and all imports are outside app and
  artifact directories; exercise rejected paths, altered units and protocol
  mismatch before allowing a switch.
- In the actual service sandbox, prove reads/writes of synthetic production
  canaries and production control sockets fail. Prove gateway and worker outbound TCP,
  metadata and external network attempts fail at the OS level. Verify the host
  enforces the actual socket/address-family/network restrictions.
- Confirm only Preview services, accounts, mounts and directories changed; no
  production account, data, settings, environment, Sandbox or Volume was reused.
- On the final distinct HTTPS hostname, sign in as Alice and Bob; edit a card,
  reload it, complete a mock Conversation turn, and receive a real event over
  nginx and the private worker socket. Reject anonymous and cross-origin access.
- Inspect browser cookie transmission and storage for both production and
  Preview. Verify the trusted identity page/header against the installed receipt.
- Cold-start the slot and check memory/OOM behavior, including two concurrent
  login attempts. Verify production readiness remains unchanged.
- Exercise failed preparation, interrupted switch, successful rollback and
  corrupted-backup refusal with disposable Preview data. Recheck receipt identity
  and account isolation after recovery.

Do not mark the live slot accepted from an HTTP 200 or from the local tests alone.

## Phase 3 checklist: remains disabled

No workflow, environment secret, repository protection rule or automatic
deployment is enabled by this phase. The following is the proposed OAW controller
design, with GitHub event/API semantics linked beside the relevant step.

| Stage/event | Required implementation and acceptance |
| --- | --- |
| Existing PR CI: `pull_request` | Keep builds secret-free. Record PR head SHA and base SHA separately from the synthetic merge commit tested by default checkout (`github.sha`, `refs/pull/<n>/merge`). Revalidate all three for the current open PR. A green old merge build is stale after either branch changes. [Event semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [contexts](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts) |
| Trusted completion controller: `workflow_run: completed` | Maintain reviewed controller code on the default branch and require successful upstream completion. Never checkout PR code, import artifact helpers, restore untrusted executable caches, or execute artifact scripts in the privileged controller. Do not use `pull_request_target` to build/run PR code. [workflow_run security](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run), [secure use](https://docs.github.com/en/actions/reference/security/secure-use) |
| Artifact acceptance | Query GitHub for repository/head-repository identity, contributor/branch allowlist, workflow ID, run ID, attempt, exact artifact ID, expiry and platform digest. Bind the downloaded bytes and inner manifest to the current PR/head/base/merge and current required checks. Reject forks and mismatches; an artifact-supplied PR number/SHA grants no authority. [Artifact API](https://docs.github.com/en/rest/actions/artifacts) |
| GitHub `preview` environment | Configure separate least-privilege Preview deployment credentials and explicit approval/protection rules. Verify repository-plan support before relying on required reviewers. Create a transient, non-production deployment record for the exact accepted SHA; publish `queued`/`in_progress`/`success` with `environment_url` pointing to the shared Preview origin and `log_url` to the controller transaction evidence. [Environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), [deployments](https://docs.github.com/en/rest/deployments/deployments), [statuses](https://docs.github.com/en/rest/deployments/statuses) |
| Shared slot and host transaction | Use one deployment concurrency group shared by Preview and production with `cancel-in-progress: false`; only disposable PR builds may cancel prior builds. Add a host-wide lease alongside environment transaction locks. After acquiring it, recheck latest PR/branch identity, installed baseline and slot generation before switching. Queue order is not proof of freshness. [Concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) |
| Production: target-branch `push` | After merging into the approved Alibaba target branch, build a fresh artifact from that push's actual `github.sha`; bind its required checks and merged PR. Do not promote the temporary PR merge artifact. Require target-branch protection and separately approved production environment credentials. Keep production deployment disabled until its root provisioning broker is independently reviewed. [Push event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push), [protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) |

Before enabling either environment, test the controller's rejection and recovery
paths: forged repository/workflow/manifest identity, expired artifacts, PR
updates/closure, reruns/attempt changes, out-of-order completion, slot replacement,
cancellation, disconnect and lease expiry. A replaced Preview deployment becomes
`inactive`, and its previous acceptance cannot follow the new occupant. Publish
failed installation/recovery as `failure` or `error` with a transaction log link;
if rollback succeeds, report the restored release separately. Failed rollback
keeps the environment stopped and requires inspection, with no blind retry or
success status. These states use GitHub's
[deployment status API](https://docs.github.com/en/rest/deployments/statuses).

Real models/cloud require a separate approved design with dedicated limited
Preview credentials, budget, profile identifiers and Volumes. Verify that Preview
stop/cleanup cannot reach production resources before enabling those capabilities.
