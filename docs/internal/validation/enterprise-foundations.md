# Enterprise foundations: validation record

Date: 2026-09-28. Integrated base: `dev` at
`096e53a08fd31aaa3f1d1f7ad6fe1ff81de18836`.

This is the first foundation slice within a single profile. It does not enable
shared-database tenancy, employee login, RBAC enforcement or multiple workers.

## Local Windows validation

| Check | Result |
| --- | --- |
| Complete backend and public SDK selection (`tests backend/tests`) | 1,410 passed, 0 failed, 45 skipped |
| Frontend Vitest | 810 passed, 0 failed |
| Retained dev tutorial fixtures after removing duplicate PR edits | 19 passed |
| Frontend TypeScript and production build | Passed |
| Playwright deployment acceptance against the production build | 1 passed; runner exited successfully |
| Documentation tests, strict build and generated-site check | 6 tests passed; 61 pages and local links/assets checked |
| Workflow YAML parsing and `git diff --check` | Passed |

Tests ran from an isolated checkout with the existing project Python 3.12
virtual environment and matching frontend dependencies. Pytest temporary files
were contained in that checkout. The browser acceptance used independent
Playwright and Edge with its own mock deployment; it did not use an embedded
browser or a real external model. The initial sandboxed browser run completed
the test but needed assistance terminating its server. A fresh run outside
that process restriction passed and exited normally.

The 45 skips retain existing platform, optional-runtime and native/third-party
acceptance prerequisites. No blanket skip or permissive success status was
introduced. Local Windows results do not establish native Sandbox or Linux
acceptance. Exact-commit remote results are listed in the
[PR checks](https://github.com/theAfish/open-agent-world/pull/30/checks).
The workflow also runs Windows core smoke and Linux deployment acceptance;
adding it does not configure required branch protection.

## Integration and baseline reconciliation

The original September 26 report had 18 local backend failures and the old
Linux CI run had 16. Those records describe the old branch, not this merged
revision. Current dev already repaired queue/delivery/stream fixtures, preset
contracts and cancellation cleanup. This branch retains those repairs.

The expanded CI selection additionally runs root-level public SDK tests, which
the existing dev backend job did not collect. The remaining stale assertions
there are repaired without changing production behavior:

- The default agent model is the configured `oaw:default` reference. The fake
  ADK translation test supplies its own explicit model instead of relying on a
  configured user default.
- Cancelling one command drains that execution's process tree and leaves the
  shared Sandbox ready. The test now checks its job closure, finished receipt
  and empty execution registry, then checks workspace permission revocation at
  the explicit Sandbox stop boundary.

The request-context integration preserves the existing actor enum, local
principal/scope and binding APIs. Only authentication establishes an identity;
HTTP/WebSocket correlation never assigns local authority to a remote request.
Both the original API/context tests and the new ingress, isolation and replay
coverage are retained. Existing frontend GET retries and product fixtures are
preserved, while failed mutations remain unretried.

CI extends the existing workflow and check names, reuses repository plugin
installation, and adds deployment and Windows checks. The parallel workflow
and PDF dependency pin from the old PR are removed.

## Remaining acceptance boundaries

- Idempotency covers conversation session creation only, when the caller
  supplies a stable key. Lost responses, conflicting payloads, live revocation,
  rollback, database reopen and competing SQLite connections are covered.
- Events still publish after commit on a best-effort basis. External effects
  and durable notification delivery require an outbox.
- Tenant scope is compatibility metadata in one profile database. Scoped
  repositories, tenant constraints and cross-tenant isolation are future work.
- Shared-password deployment sessions are ephemeral and are not durable
  employee identities; reauthentication can change the idempotency actor.
- Correlation and health probes do not provide distributed tracing, a metrics
  backend or high availability. Native isolation and real external services
  require their separate acceptance suites.
