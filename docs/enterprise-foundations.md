# Enterprise foundations: implementation sequence

Baseline: `dev` at `10b58ea` (2026-09-26). This change is the first foundation
slice, not an enterprise deployment release. The current database still owns
one world per profile; shared-database multi-tenancy is not enabled.

## Verified starting point

- `backend/control_plane.py` authenticates the actual socket peer or a host
  bearer token. Forwarding headers do not confer local trust.
- `backend/deployment_runtime.py` checks an in-memory shared-password session,
  release surfaces and allowed nodes. A session is not a named employee.
- `backend/api/dependencies.py` previously carried only a card-state session.
- `backend/services.py` constructs SQLite stores and orchestrates mutations.
  The stores generally do not accept tenant or human authorization context.
- `backend/persistence/database.py` uses a connection-level `RLock`, SQLite
  transactions and additive startup migrations. An `RLock` is not an asyncio
  task lock. Never keep these synchronous transactions open across `await`.
- `/api/health` already exists as a compatibility liveness endpoint.
- Existing backend, frontend and deployment tests had no independent PR CI.
- Run `caller_id` identifies an execution trigger (such as a conversation),
  and card-state owner/session identifies state placement. Neither is a tenant
  or a human principal; they must not be repurposed as one.

## This slice

### Trusted request metadata

`ActorRef(kind, id)`, `TenantScope(organization_id, workspace_id, world_id)` and
`RequestContext(request_id, actor, tenant, auth_method)` are immutable values.
`LOCAL_TENANT_SCOPE` is a compatibility scope within a single profile database.
It is not a globally unique organization identity or a database isolation layer.

Only successful server authentication establishes the context:

| Entry | Actor | Meaning |
| --- | --- | --- |
| Literal local peer | `local_host` | Existing trusted desktop host |
| Valid host bearer | `host_credential` | Host integration credential |
| Valid deployment cookie | `deployment_session` | Pseudonymous shared-password session |
| Explicit public deployment metadata/login route | `anonymous` | Public visitor, no operator authority |

Cookie/token contents are never the public actor ID. Neither `X-Actor-*` nor
`X-Tenant-*` selects identity or data scope. `X-OAW-State-Session` retains its
existing state-selection meaning. HTTP dependencies bind and reset the context;
desktop WebSockets retain the same trusted ingress boundary.

Context variables are convenience for request-local diagnostics. Background
tasks can inherit them, so future business authorization must receive an
explicit context/provenance and revalidate current permissions. Persisting an
old request context does not preserve authorization after membership revocation.

### Correlation, errors and health

- Every HTTP response receives `X-Request-ID`; a single incoming ID containing
  1–128 ASCII letters, digits, dots, underscores or hyphens is accepted. Missing,
  duplicate or invalid IDs are replaced. This field conveys no authority.
- Framework/domain error envelopes include `request_id` and `retryable`.
  Existing `detail` and sandbox feedback fields remain available. Validation
  errors omit raw inputs; unexpected failures return a generic message.
- Server request logs carry the ID, method, status and elapsed time, without
  request bodies, credential headers, URL paths or query strings. Frontend
  `ApiError` retains correlation ID, error code and retryability metadata.
- `/health/live` returns process liveness. `/health/ready` returns 503 until
  startup and recovery finish, while the database probe fails, or during
  shutdown. The probe checks the migrated database; it does not contact optional
  model providers or start sandboxes. Responses disclose no internal details.
- Desktop probes retain control-plane authentication: run them on loopback or
  with the existing host credential. Deployment probes expose only minimal
  status. Existing `/api/health` behavior remains compatible.

Retryability is conservative. A server error alone does not prove that a
mutation had no effect. The client performs no new automatic POST retries.
Uniform timeouts for long-running commands, frontend cancellation semantics,
diagnostic exports and metrics require separate work.

### Durable idempotency: conversation session creation

The conversation session creation API accepts optional `Idempotency-Key`.
Old clients keep working without it. The client API accepts an explicit key;
callers must retain the same key and payload when retrying one logical action.

The key scope is:

```
organization + workspace + world + actor kind/id + operation + key
```

A canonical request hash distinguishes a replay from conflicting key reuse.
The session, its state scope and serialized response commit in one synchronous
SQLite transaction. A second connection waits for that transaction and replays
the committed response. Failure before commit rolls everything back. Reopening
the database preserves replay behavior for the same stable actor. Deployment
cookie sessions are still in memory: restarting or logging in again changes that
actor, so deployment idempotency across reauthentication is not guaranteed yet.
Existing graph authorization is checked
on every attempt, including replay; request IDs may change between attempts.

This guarantee applies only to the implemented session operation. It does not
cover node creation, message delivery, run starts, plugins or external effects.
Events are still emitted after commit on a best-effort basis; a transactional
outbox is needed to repair a crash between commit and event publication.
Idempotency records have no automatic expiry in this slice. A future retention
policy must specify when key reuse is allowed and how replayed data is removed.

### CI

The PR workflow checks the locked backend and frontend dependency installations,
backend tests, frontend type/build and Vitest checks, deployment E2E, and Windows
backend smoke coverage. Plugin-only test dependencies are separately pinned in
the CI requirements file. Checks must pass; no `continue-on-error` bypass is used.
Repository administrators still need to configure these checks as required on
`dev`; adding a workflow does not alter GitHub branch protection.
The local validation results and pre-existing backend failures are recorded in
[the validation report](enterprise-foundations-validation.md). The new workflow
does not make the existing backend suite green by itself.

## Next changes, in dependency order

| Priority / change | Concrete work | Acceptance gate |
| --- | --- | --- |
| P0: restore the backend baseline | Resolve the 18 reproduced failures, separating stale fixtures from runtime cancellation/delivery defects; run the new Linux and Windows jobs before making checks required | Full backend suite passes without blanket skips, weakened assertions or `continue-on-error` |
| P0: durable identity and provenance | Bootstrap per-profile organization/workspace/world and local principal, add a versioned migration ledger and backup/restore procedure; add initiating actor/request columns to messages, runs and resource history without changing existing trigger IDs | Old profile migration preserves resource IDs, messages and run semantics; restart preserves identity |
| P0: tenant-owned repositories | Introduce explicit `TenantScope` on repository boundaries, backfill business tables, add composite keys/FKs, cover attachments/state/secrets/settings/deployment copies | Knowing another tenant's UUID returns the same 404 as absence; cross-world edges/session/artifact links fail in the database |
| P0: AuthorizationService | Apply the contract below at use-case boundaries; require scoped repository lookups before policy evaluation | HTTP, Agent tools and background calls cannot bypass checks; desktop compatibility has explicit local policy |
| P1: RBAC and audit | Persist users/groups/service accounts, role permissions and scoped bindings; append audit events in the business transaction; add WebSocket subscription filtering | Role removal affects subsequent operations and running Agent tool calls; no cross-tenant events |
| P1: durable operations | Extend idempotency by operation, add outbox/replay and frontend resync; persist requester provenance and worker leases before multiple workers | Lost response/restart does not duplicate mutations; lost event repairs; only one worker holds a valid lease |
| P1: enterprise adapters | OIDC and durable sessions, PostgreSQL repository/unit-of-work adapter, object storage and external secret store; separate releases from runtime data | SSO/session restart, restore, upgrade/rollback and two-instance integration tests |

Do not advertise a multi-user enterprise mode until tenant ownership,
authorization, event isolation and durable identity/session gates pass. Swapping
the SQLite driver alone cannot satisfy these conditions.

## RBAC architecture contract

The next use-case boundary should take explicit context and ask one service:

```python
authorization.require(
    context,
    action="run.start",
    resource=ResourceRef(kind="agent", id=agent_id, tenant=context.tenant),
)
```

This is the proposed contract, not an authorization implementation in this PR.
The resource tenant in this example must be verified by a scoped repository
lookup; constructing `ResourceRef` from caller input is not sufficient.

1. Resolve the resource within the trusted context scope; foreign and missing
   resources both return 404. Never perform an unscoped existence check first.
2. Resolve current subject memberships and role bindings for the organization,
   workspace and world hierarchy. Unknown actions and missing grants deny.
3. Intersect human grants with release surfaces and enterprise policy. Agent
   tool calls additionally require the current graph-derived capability.
4. Revalidate the initiating user's authority for each delegated sensitive
   operation; do not copy user permissions into an Agent's capability set.
5. Write an audit event with actor, initiator, delegation chain, request ID,
   run ID, action and scoped resource. Never log secret values or credentials.

Suggested first roles: organization owner/admin, workspace admin, builder,
operator, member, viewer and auditor. Bind roles to an explicit scope; a workspace
binding cannot grant organization administration. Start with a finite permission
registry (`world.read/edit`, `conversation.read/post/manage`, `run.start/cancel`,
`sandbox.execute`, `secret.use/manage`, `plugin.install`, `release.publish`,
`member.manage`, `audit.read`) and prohibit undefined/wildcard expansion by default.

`secret.use` and `secret.manage` remain separate. Release allowlists and Agent
capabilities continue to restrict access even when a human has a broad role.
Shared deployment cookies are transitional credentials, not employee accounts;
OIDC identities and durable sessions will replace them for enterprise use.
