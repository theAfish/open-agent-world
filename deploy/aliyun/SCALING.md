# Scaling hosted OAW on Alibaba Cloud

Users can start a Run from the existing workspace and return later to check its
progress. The target deployment separates serving the workspace, executing Runs,
and transferring files so each can scale with its own load. Start from the
[ECS hosted deployment](ECS.md) and its existing
[workspace isolation and storage](PERSISTENT_WORKSPACES.md).

## Pack workers: opt-in shared filesystem executor

The Alibaba host can now resolve `PackWorkerSpec` into a reviewed Sandbox
template and command without a cloud-specific Pack. This is **not enabled on
production by merging code**. Missing configuration rejects hosted Pack workers;
there is no fallback to scientific computation on ECS. Personal execution keeps
its existing environment and directories.

The operator installs `aliyun-pack-workers.json` beside the profile's `data`
directory, with the same ownership/protection as `aliyun-workspace.json`. It must
contain exactly these fields (replace the example identities and recipes):

```json
{
  "version": 1,
  "profile_id": "PROFILE_UUID",
  "volume": "oaw-PROFILE_UUID",
  "mount_path": "/mnt/oaw-workspaces/PROFILE_UUID",
  "verified": true,
  "deployments": [{
    "pack_id": "example.science",
    "pack_version": "1.0.0",
    "environment_id": "science",
    "template": "REVIEWED_TEMPLATE_ID",
    "commands": {"analyze": ["/opt/science/bin/python", "-I", "/opt/science/analyze.py"]},
    "environment": {"OMP_NUM_THREADS": "2"}
  }]
}
```

Only set `verified` after an isolated cloud acceptance. The ECS mount and Sandbox
mount must expose the same dedicated Volume root at the same absolute path.
Pack files live under `packs/<pack ID>`. The executor rejects wrong versions,
undeclared workers, another Pack's directory and absent mounts before allocating
a VM. It verifies bidirectional file visibility before starting the command, then
reuses renewable leases, private networking, non-root execution, logs and confirmed
VM teardown. A lost response is not replayed. This is still an ECS-owned operation:
it does not make an active job survive a profile worker restart.

The template must already contain the exact reviewed Pack version, worker code,
environment and executable. Arguments are appended as argv, never interpolated
into a shell. Any absolute reference paths in job documents must exist in the VM.
Changing Pack code requires a new version and a matching reviewed template; this
adapter does not copy a developer checkout or install dependencies per job.
For XRD, provide both `worker.py` and `multiphase_worker.py` launch recipes through
the Pack's existing `worker_launcher.py`, its scientific environment and QualX.
Keep COD/reference libraries read-only at the same path seen by the Pack. Do not
invent a `readOnly` flag in dynamic AgenticFS metadata; enforce reference access
through the supported storage policy or an immutable template copy.

Keep OAW/profile SQLite, credentials and execution receipts on ECS. The worker
directory holds files, including any Pack-managed file databases; do not assume
SQLite WAL works on NFS. XRD's connected history-store database stays in its
existing host storage. Existing Pack files are not migrated automatically.
Shared tool variables are forwarded through the existing validation; fixed recipe
variables take precedence. Host process secrets are not inherited.

### Next installation and acceptance gate

Before enabling a production profile:

1. Verify the ECS and existing Access Point share a VPC, choose a single isolated
   test profile, inspect its UID/GID and Volume quota, and approve client/mount
   installation. Use the ALINAS client, NFSv3 over TLS, and an Access Point-scoped
   credential. Confirm the installed client supports the ECS OS and temporary
   credential refresh; do not grant storage access to all profiles for this test.
2. Mount the profile Volume at `/mnt/oaw-workspaces/<profile UUID>`, with a
   root-controlled parent and access restricted to the profile worker identity.
   Test VM and ECS UID/GID compatibility before opening the Pack workspace. Do not
   overlay live data directories or change production worker units during this test.
3. Run the fixed candidate code in an isolated non-root diagnostic service, using
   the existing base template and only a small stdlib fixture worker. Verify input,
   output, logs, interactive control, stop and cleanup, then create a second VM to
   verify persistence. Cloud VM count, time and spend need separate authorization.
   The prepared runner is `python -m deploy.aliyun.pack_worker_probe --state
   /var/lib/oaw-pack-probe`. Supply `OAW_ACCEPTANCE_SANDBOX_SETTINGS` and the API key
   using the existing private acceptance environment. It creates at most two VMs,
   each with a 900-second operation budget and 300-second renewable lease; it never
   retries an uncertain operation. Receipts stay outside the mounted directory.
4. Build and verify an immutable XRD template and read-only references. Confirm
   native/remote scientific outputs, failure cleanup and concurrency quotas. Only
   then install the production assignment and migrate a selected Pack file root.
   Account, session and database migrations are separate work.

The isolated stdlib worker has passed real AgenticFS bidirectional I/O, interactive
control, teardown and persistence after VM recreation. That does not establish
scientific-template acceptance. Rollback before activation only
removes the opt-in assignment; after migrating files, restore the previous code and
its recorded data-root mapping together. Never point old code at an empty root.

### Preparing the XRD scientific template

`deploy/aliyun/xrd_template` reuses the Pack's workers, current scientific
dependencies, bundled M3GNet weights and existing QualX patch. Python 3.11 and
transitive Python dependencies are pinned separately from the host interpreter.
The template contains no profiles, credentials, documents or COD database. Build
the image before deployment: the complete scientific installation and QualX
compilation must not run under the template's five-minute initialization limit.

Build from committed files in a reviewed checkout, keeping private/untracked data
out of the Docker context:

```sh
git archive HEAD plugins/xrd deploy/aliyun/xrd_template | docker build \
  --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  -f deploy/aliyun/xrd_template/Dockerfile -t oaw-xrd:reviewed -
```

The official base image and Python dependencies are fixed. Debian packages still
come from that image's configured repositories; this is not a byte-reproducible
Debian build. Validate the resulting image, then push it to an approved ACR
Enterprise repository with a unique tag. Deploy its digest, never a mutable tag.
Creation/purchase of ACR or network bindings requires separate resource approval.

Generate a reviewable request without contacting Alibaba Cloud:

```sh
python -m scripts.aliyun_science_template plan --config PRIVATE_CONFIG.json \
  --name oaw-xrd-UNIQUE-VERSION --xrd-revision "$(git rev-parse HEAD)" \
  --xrd-image INSTANCE-registry-vpc.cn-hangzhou.cr.aliyuncs.com/oaw/xrd@sha256:DIGEST
```

Use the reviewed source commit recorded by the built image. `plan` does not create
resources or reserve a build. After resource approval, use `build` with the same
arguments and a new private `--receipt FILE`; `status` reads that receipt. An
uncertain creation must be reconciled, never repeated with a different receipt.
The recipe requests 4 vCPU, 8 GiB memory and 15 GiB disk. It uses the ACR VPC
address in the same region and the platform's existing registry pull mechanism;
no registry password is included. Template startup only checks the image's source
revision and readiness. The build publishes readiness after scientific imports,
bundled M3GNet checkpoint loading/inference, reference simulation and the patched QualX CLI
checks succeed. A ready image still requires scientific and storage acceptance.

The offline `acceptance.py` entrypoint runs batch matching, interactive CIF
calculation and one demo fitting iteration through the real worker launchers as a
non-root identity. It writes only into a fresh explicitly selected
directory. Compare its numeric outputs on native and cloud execution. It does not
allocate a VM, call models, test full-library search or prove full refinement.
`recipe.deployment(version, template_id)` emits only the Pack launch recipe;
it cannot verify or activate a profile assignment.

Full-library search additionally needs the original four COD database files,
read-only and available at the same absolute paths on host and VM. Full fit and
preoptimization acceptance, quotas and production migration remain required.

Official references: [ECS AgenticSpace mounts](https://www.alibabacloud.com/help/zh/nas/user-guide/manage-agenticspace),
[Sandbox Volume mounts](https://help.aliyun.com/zh/agent-sandbox/mount-agenticfs-volume-1),
[custom templates](https://help.aliyun.com/zh/agent-sandbox/build-and-manage-custom-templates-with-code).

### Preparing an ACS scientific trial

ACS Jobs can run the existing reviewed scientific image and worker acceptance.
Render a request locally before approving cloud installation. Use the source
commit recorded in the image, an actual registry manifest digest, a dedicated
namespace and a unique Job name:

```sh
python -m scripts.aliyun_acs_science_trial \
  --image REGISTRY/NAMESPACE/xrd@sha256:DIGEST \
  --revision IMAGE_SOURCE_COMMIT --namespace oaw-science-trial \
  --name xrd-trial-001 > acs-xrd-job.json
```

Private repositories can use `--image-pull-secret EXISTING_SECRET_NAME`. Create
that Secret separately in the trial namespace using the approved credential
procedure. The rendered request contains its name only. ACS supports ACR personal,
enterprise and reachable third-party registries. Check the actual private-network
pull path before choosing a registry. The ACR Enterprise requirement above is
specific to the current Sandbox template deployment path.

ACR personal edition currently requires an eligible personal-identity account,
is limited to development/testing, and has no public management OpenAPI. Ask the
account owner to check availability in the console. Its shared bandwidth and pull
limits also need review before a concurrent-load trial. ACS cluster creation
includes a billed private CLB for its API server. Review that cost together with
the Pod budget, existing VPC/subnet and registry access before installation.

After approval of the cluster, namespace, image access and bounded trial, submit
the fixed request with `kubectl create -f acs-xrd-job.json`. Inspect its Pod logs
and Job status, record the provider Job UID and actual allocated specifications,
then delete that exact Job with `kubectl delete -f acs-xrd-job.json --wait=true`.
Verify its dependent Pods are gone. An uncertain creation is reconciled under
the original namespace and name. Do not generate a new name to repeat it.

The Job runs one non-root 4-vCPU/8-GiB worker for at most 900 seconds, with no
automatic restart/retry, a read-only image filesystem, temporary scratch storage
and no mounted ServiceAccount credential. It checks the image source before
running the same batch, interactive CIF and bounded demo-fit fixture used locally.
Finished Job objects expire after one hour; collect logs before then. Pending
image-pull time can also be billed. The local renderer creates no cloud resources.

This fixture uses synthetic inputs. User-specific AgenticFS mounts, host/ACS
result transport, cancellation and recovery through OAW's normal task API, GPU
execution and load acceptance still require integration. AgentCore code-package
hosting is another deployment path with its own runtime and archive-size limits.

Official references: [ACS Jobs](https://help.aliyun.com/zh/cs/user-guide/create-a-task-workload-job),
[ACS image sources](https://help.aliyun.com/zh/cs/user-guide/container-mirror),
[ACS billing](https://help.aliyun.com/zh/cs/product-overview/product-billing-rules),
[ACS cluster creation](https://help.aliyun.com/zh/cs/user-guide/create-an-acs-cluster),
[ACR personal edition](https://help.aliyun.com/zh/acr/user-guide/create-a-container-registry-personal-edition-instance),
[AgentCore code packages](https://help.aliyun.com/zh/agentcore/high-code-agent-deployment-guide).

## 1. Make direct Run submission safe to retry

This change adds an optional `Idempotency-Key` to direct Agent Run submission.
Clients generate a key for each intentional submission and reuse it with the same
request when the response is lost. Keys contain 1–128 visible ASCII characters;
invalid keys return HTTP `422`. Within the authenticated tenant, actor, and target
Agent scope, the same key and body return the original `run_id`. Reusing the key
with different input returns HTTP `409`. Requests without a key retain their
current behavior. This is available on both `/api/agents/{agent_id}/run` and the
published `/api/runtime-app/workspace/agents/{agent_id}/run` endpoint.
The key and its Run are recorded in the existing profile SQLite database, which
still has a single writer. For immediate submissions, a crash after the atomic
Run reservation but before execution can leave a Run that never started. Startup
marks it interrupted, and a retry still returns that Run ID. Clients inspect Run
status before intentionally submitting new work with a new key.

Acceptance covers duplicate requests while a Run is active and after it finishes,
conflicting input, a lost-response retry after restarting the worker, and rejection
of requests outside the caller's authorized scope.

## 2. Give a separate runner ownership of execution

### 2a. Persist queued direct Runs

Direct Agent clients can opt into the queue with `enqueue: true`. The hosted UI
uses this path for direct Run actions and generates an `Idempotency-Key` for each
intentional submission. The existing Agent status and Stop controls still use
the same workspace APIs. The UI does not automatically resend a failed mutation.
API clients can repeat an uncertain submission with the original key and body.

The pending backlog defaults to 128 Runs per profile. Operators can set the
positive integer `run_queue_limit` in that profile's `worker.json`; this bounds
waiting work, independently of execution concurrency or total website users.
A full queue returns `503` without consuming the submission key. Replays of
accepted requests still return their Run ID when the queue is full.

```http
POST /api/runtime-app/workspace/agents/AGENT_ID/run
Content-Type: application/json
Idempotency-Key: workspace-analysis-001

{"prompt":"Run the configured analysis","enqueue":true}
```

In a published workspace, the submitter can read a queued Run at
`GET /api/runtime-app/workspace/runs/{run_id}` or cancel it with
`POST /api/runtime-app/workspace/runs/{run_id}/cancel` while the Agent remains
published and the caller has access.

The profile worker saves the input with its Run ID and claims it outside the
request. Pending work survives an owner restart. A claimed attempt with an
unconfirmed outcome is interrupted during recovery. It is never automatically
executed again. The existing profile worker still owns SQLite and execution.
The gateway already runs independently of the profile workers.

Check pending recovery, same-ID replay, cancellation before claim, and prevention
of duplicate execution after an owner restart. Before rolling back to code that
does not understand queued Runs, stop admitting new queue requests and drain or
cancel the existing queue. Preserve the profile database and submission receipts.
Remove the optional `run_queue_limit` configuration key before starting worker
code that predates this option. During worker shutdown, explicit HTTP cancellation
returns `503`; retry against the ready worker to cancel any preserved pending work.

### 2b. Separate the API and runner processes

The execution service and infrastructure phases below are proposed work.

- Let a separate service claim a queued Run. Keep its Run ID, status, activity,
  and cancel operations. Define which process owns each state transition and how
  the API reads status. Keep one SQLite profile owner until shared state has a
  migration path.
- Record the execution attempt and provider resource IDs. When a claim expires or
  a provider response is missing, reconcile the attempt before starting any new
  work. Resume a supported remote operation only after checking ownership and
  confirming its state. Record an interruption when recovery cannot be confirmed.
- Reuse the per-workspace AgenticSpace, access point, and named Volume. Derive the
  mount from the authenticated workspace. Check the access point, VPC, RAM role,
  Team, and template. Release the Sandbox after execution and verify its cleanup.
  Check actual Team quotas before increasing parallelism. See Alibaba Cloud's
  [Sandbox lifecycle](https://help.aliyun.com/zh/agent-sandbox/lifecycle),
  [AgenticFS Volume mounts](https://help.aliyun.com/zh/agent-sandbox/mount-agenticfs-volume-1),
  and [Team quotas](https://help.aliyun.com/zh/agent-sandbox/quota-management).

Accept when closing the browser and rolling the Web/API service both leave an
active Run observable under the same ID. Test runner failure separately, including
a lost create response, cancellation during startup, and prevention of duplicate
execution. Verify cross-tenant access denial and file integrity after replacing a VM.

## 3. Move attachment traffic to OSS

- Start with Conversation attachments. Authorize a workspace-specific object
  reservation and issue a short-lived upload grant to a private OSS bucket. Bind
  completion to the reservation, verify the object and its expected integrity
  metadata, and publish the existing artifact reference only after it is ready.
- Use short-lived download grants after authorization. Add multipart uploads when
  needed and expire unfinished parts. Keep historical artifact versions readable
  during rollout. AgenticFS remains the writable project directory, while OSS
  carries the bytes of uploads and downloadable artifacts. See Alibaba Cloud's
  [signed upload URLs](https://help.aliyun.com/zh/oss/user-guide/upload-files-using-presigned-urls),
  [multipart upload](https://help.aliyun.com/zh/oss/user-guide/multipart-upload),
  and [CORS settings](https://help.aliyun.com/zh/oss/user-guide/configure-cross-origin-resource-sharing).

Accept when the browser transfers bytes directly, authorized Runs consume the
registered object, and foreign, expired, or incomplete uploads cannot be published.

## 4. Move XRD computation off the Web host

Keep the existing Run and artifact interfaces. Run the XRD pipeline in a dedicated
execution pool with pinned dependencies and resource limits. Stage immutable inputs
for the attempt and publish validated outputs. Record external job IDs so retrying
after a timeout can inspect an existing job. Consider
[E-HPC Instant](https://help.aliyun.com/zh/e-hpc/e-hpc-instant/user-guide/action-plan)
when job size or duration warrants a separate batch service.

Accept when representative search and fit cases match current scientific results
within agreed tolerances and preserve logs, output access, and cancellation. Check
that an XRD load does not exhaust resources needed for normal workspace requests.

## 5. Share control state and scale API replicas

- Move tenant routing, sessions, and Run control records that need shared ownership
  to [RDS PostgreSQL](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/rds-high-availability-edition).
  Backfill and compare records per workspace, then cut over each dataset to one
  authoritative writer. Preserve IDs, tenant boundaries, and existing artifact links.
- Put API replicas behind [ALB](https://help.aliyun.com/zh/slb/application-load-balancer/create-and-manage-listeners).
  Serve static assets independently. Reconnect status streams to another replica
  using durable Run status and event cursors. Size the execution pool separately
  from API replicas. Add a dedicated message broker if the durable queue needs it.
- Rehearse a point-in-time database restore and a failover. Verify that two replicas
  serve the same workspace state without duplicating mutations or Runs.

## 6. Prove capacity and cut over in stages

Use synthetic workspaces in staging. Test idle sessions and connection bursts,
authenticated reads and writes, task submissions, and concurrent execution
separately before combining them. Measure API and Run start latency, queue age,
success and cancellation rates, WebSocket reconnects, Sandbox allocation failures,
orphaned resources, and database and host saturation. Compare normal and burst
load, including a saturated execution pool. Use Alibaba Cloud
[PTS for HTTP](https://help.aliyun.com/zh/pts/performance-test-pts-3-0/user-guide/interface-pressure-measurement)
and [WebSocket load](https://help.aliyun.com/zh/pts/performance-test-pts-2-0/user-guide/create-a-stress-testing-scenario-1).

Route a small cohort to the new path, compare outcomes and isolation against the
existing path, and expand in stages. Drain or reconcile active Runs before moving
their workspace back. Preserve authoritative writes, Run IDs, and artifact references
through the cutover. Use measured workload limits and recovery results to decide how
far to expand.
