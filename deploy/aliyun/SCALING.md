# Scaling hosted OAW on Alibaba Cloud

Users can start a Run from the existing workspace and return later to check its
progress. The target deployment separates serving the workspace, executing Runs,
and transferring files so each can scale with its own load. Start from the
[ECS hosted deployment](ECS.md) and its existing
[workspace isolation and storage](PERSISTENT_WORKSPACES.md).

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
still has a single writer. Existing Run status APIs remain the way to observe the
result, including a Run interrupted by a worker restart. An HTTP retry does not
replay execution. A crash after the atomic Run reservation but before execution
can leave a Run that never started; startup marks it interrupted, and a retry
still returns that Run ID. Durable acceptance does not yet guarantee execution.
Clients inspect its status before intentionally submitting new work with a new key.

Acceptance covers duplicate requests while a Run is active and after it finishes,
conflicting input, a lost-response retry after restarting the worker, and rejection
of requests outside the caller's authorized scope. This increment covers direct
Run submission. The execution and infrastructure phases below are proposed work.

## 2. Give a separate runner ownership of execution

- Start with one hosted Agent workflow. Keep its Run ID, status, activity, and
  cancel operations. Persist the Run input before acknowledging it, then let a
  separate service claim it. Define which process owns each state transition and
  how the API reads status. Keep the existing SQLite profile owner until shared
  state has a migration path.
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
