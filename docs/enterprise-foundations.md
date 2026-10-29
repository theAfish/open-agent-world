# Request identity and operational boundaries

OAW's local runtime owns one World per profile database. Hosted access can route
users to separate profiles; this is not shared-database multi-tenancy or an
implemented general enterprise RBAC service. See [hosting](contributing/hosting.md).

Socket peer/host-token authentication, deployment sessions and Agent graph grants
remain distinct. Forwarding or caller-supplied identity headers never create authority.
Run caller IDs and state session IDs are not human or tenant identities.

## Trusted request metadata

`ActorRef(kind, id, display_name)`, `TenantScope(organization_id, workspace_id, world_id)` and
`RequestContext(request_id, actor, tenant, auth_method)` are immutable values.
`LOCAL_TENANT` is a compatibility scope within a single profile database.
It is not a globally unique organization identity or a database isolation layer.

The existing `ActorKind`, local principal/scope, `create_local_request_context`
and `bind_request_context` APIs are retained. HTTP/WebSocket correlation uses
one middleware; it never creates an identity. Only successful
server authentication (or an explicitly public route) establishes the context:

| Entry | Actor | Meaning |
| --- | --- | --- |
| Literal local peer | `user` / `local-user` | Existing trusted desktop principal  |
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

## Correlation, errors and health

- Every HTTP response receives `X-Request-ID`; a single incoming ID containing
  1-128 ASCII letters, digits, dots, underscores, colons or hyphens, starting
  with a letter or digit, is accepted. Missing,
  duplicate or invalid IDs are replaced with a `req_`-prefixed random ID. This field conveys no authority.
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

## Durable idempotency: conversation session creation

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
Idempotency records have no automatic expiry for this operation. A future retention
policy must specify when key reuse is allowed and how replayed data is removed.
