# Enterprise foundations: validation record

Date: 2026-09-26. Base: `dev` at `10b58ea90db3627a619a08b824f791b0ca54759a`.

This records local Windows results. The first GitHub Actions run on `2dc0aec`
passed the Linux frontend/build/deployment job and Windows core smoke job;
the full Linux backend job was still running at this checkpoint. Required
branch checks have not been configured. This slice
does not enable shared-database tenancy, employee login or RBAC enforcement.

## Results

| Check | Result |
| --- | --- |
| HTTP foundations, request context, idempotency, control plane, deployment, card state, conversation groups, persistence/events | 105 passed, 1 skipped |
| All backend and public SDK tests | 1,318 passed, 18 failed, 45 skipped |
| Frontend Vitest | 734 passed, 0 failed |
| Frontend TypeScript and production build | Passed |
| Playwright deployment acceptance | 1 passed |
| Documentation tests, strict build and generated-site check | 6 tests passed; 60 pages and 5,754 local links/assets checked |

The focused backend skip is the existing opt-in native deployment-clone test
(`OAW_DEPLOY_NATIVE` was not enabled). Other full-suite skips retain their
existing platform, optional-dependency or opt-in conditions; they were not
removed to obtain a green result.

Local backend validation used Python 3.12.3 from an existing project virtual
environment with provider adapters installed. PyMuPDF 1.26.7 was installed in
an isolated task dependency directory. The original checkout and its virtual
environment were not modified. Frontend dependencies came from `npm ci` in
the task clone. The deployment runner's `OAW_TEST_PYTHON` override selected
that existing Python environment. Clean locked installs also succeeded on
all three CI runners in the first run; local results and remote results remain
separate evidence.

All 18 backend failures were independently reproduced against an unchanged
worktree at the base commit. No additional backend failures were observed in
this local comparison. This is evidence of a pre-existing failing baseline,
not evidence that the failures are harmless or that Linux will behave identically.

## Backend baseline backlog

| Area | Count | Observed failure on both revisions | Next investigation |
| --- | ---: | --- | --- |
| `tests/test_agent_runtime.py` | 2 | Old default-model expectation and fake provider model configuration disagree with current defaults | Align fixtures with the supported default-model contract and verify provider translation |
| `tests/test_sandbox_workspace.py` | 1 | Cancellation leaves a different sandbox lifecycle state than expected | Verify process-tree termination and final state before changing assertions |
| `backend/tests/test_context_compaction.py` | 1 | Run collection is empty immediately after enqueue | Await the actual queue transition and confirm compaction across tool events |
| `backend/tests/test_conversations.py` | 4 | One status expectation differs; three delivery assertions observe claimed deliveries | Verify admission and terminal delivery transitions; distinguish timing from lost cleanup |
| `backend/tests/test_matcreator_workspace.py` | 1 | Preset source pack is no longer opened by the fixture | Update fixture to the current explicit pack/deck contract |
| `backend/tests/test_preset_library.py` | 4 | Old template payloads fail current validation | Repair representative fixtures while retaining migration and availability assertions |
| `backend/tests/test_run_cleanup.py` | 3 | Run collection is empty immediately after enqueue | Exercise asynchronous admission, then verify provider context cleanup |
| `backend/tests/test_skill_runtime.py` | 1 | Cancellation returns while runtime state differs from expected | Confirm teardown completion and revocation ordering |
| `backend/tests/test_streaming_messages.py` | 1 | Timeline has two messages where the test expects three | Verify stream identity and delivery ordering |
| Total | 18 | | |

Prioritize cancellation, delivery and stream cleanup because they can affect
runtime correctness. Then reconcile model, preset and asynchronous admission
fixtures with the intended contracts. Keep assertions about persisted data,
permission revocation and cleanup; do not replace them with unconditional
waits, blanket skips or permissive status lists. Re-run the complete backend
suite and both CI operating systems before requiring the new checks on `dev`.

## Frontend baseline repairs included

The original base independently had eight failing frontend tests. This change
repairs their fixtures: the activity-stream file lacked its DOM environment;
the tutorial cancellation test lacked the library-loading phase and resources;
Legions tests still assumed the removed automatic-deck UI. The replacement
fixtures exercise the current library/deck flow and preserve cancellation,
availability, drag, and deletion checks. Product UI behavior was not changed
to accommodate those tests.

The deployment browser test also replaced a removed participant-summary label
with the visible participant row and mention control. It still checks login,
conversation sessions, tasks, plugin notes/downloads, persistence after reload,
mobile layout, logout, and hidden engineering routes.

The documentation build also exposed missing navigation entries for the two
new enterprise pages and two existing performance reports. All four pages are
now included in navigation; strict missing-page validation remains enabled.

## Remaining acceptance boundaries

- Idempotency covers conversation session creation only. A response loss and
  replay, conflicting payload, revocation, rollback, database reopen and
  competing SQLite connections are covered. External side effects and event
  publication still need an outbox.
- Tenant context is fixed compatibility metadata within one profile database.
  Scoped repositories, tenant columns/constraints and cross-tenant isolation
  tests are future work.
- Deployment sessions remain ephemeral shared-password sessions. Their actor
  changes after login or restart; they are not durable employee identities.
- HTTP correlation and readiness probes do not provide distributed tracing,
  a production metrics backend or high availability.
- This branch should remain a draft until the backend baseline is repaired
  and the new CI workflow has been evaluated on GitHub.
