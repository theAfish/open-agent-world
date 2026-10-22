# Batch 0: development and release baseline

Audit date: 2026-10-20. Work branch: `refactor`, initially clean at
`6dd9736b93a33df87edc13d94723c2ba80c0d52e`, verified against GitHub `dev`.

## Before changes

- `main` is `1bce35429a5f9e01282c235abecd6ab5936c0e28` and an ancestor of
  `dev` (0 main-only, 12 dev-only commits). Release integration PRs exist,
  alongside direct development commits. No history rewrite is needed.
- Both branch endpoints report `protected: false`. The only ruleset,
  [Protect main branch](https://github.com/theAfish/open-agent-world/rules/23912326),
  is disabled and has an empty required-check list. The authenticated protection
  endpoint returns 404, "Branch not protected".
- [Latest dev CI](https://github.com/theAfish/open-agent-world/actions/runs/39393500126)
  fails: backend 2593 passed / 2 failed / 60 prerequisite skips; frontend 179
  files passed / 3 failed; Windows source startup and core smoke fail. Linux
  and macOS source startup pass. No failure was hidden by continue-on-error.
- [Latest documentation](https://github.com/theAfish/open-agent-world/actions/runs/39393500063)
  fails English source-language validation; tutorial examples pass, Pages is
  correctly blocked.
- Native checks are path-filtered PR checks, not a release dependency.
  Desktop release runs on `v*` or manual dispatch without running CI first.
  It rewrites four desktop version declarations after checkout, omits the
  Cargo lock entry, and does not update the backend version used for Pack
  compatibility. All app declarations still say `0.1.0`.
- [v0.5.0 packaging](https://github.com/theAfish/open-agent-world/actions/runs/36957149700)
  passed and remains a draft; published v0.4.0 used the same source commit
  `be55502c`. Packaging success alone did not establish test success.
- Existing signing, updater manifest consistency, backup/restore tests and
  draft-only publishing provide useful foundations. They must remain intact.
- The SDK is `@oaw/plugin-api@1.0.0`, backend/frontend Plugin API major is 1,
  and Pack versions are independent (Greeter `0.1.0`). These are not app versions.
- External Greeter build and restart/browser acceptance already exist but
  are not called by CI. Tutorial entry-point tests do not cover that boundary.

## Confirmed failures and repair rationale

| Failure | Classification | Required repair |
| --- | --- | --- |
| ScopedFunctionTool has no `__name__` | Stale test double | Fake runner must call ADK's Tool `name` / `run_async` contract; preserve failure/recovery assertions |
| No adapter `.pyc` under `PYTHONDONTWRITEBYTECODE` | Test environment assumption | Explicitly create the bytecode fixture; retain cleanup/reverification assertions |
| Knowledge Base cannot resolve lucide-react | CI preparation order | Prepare declared Pack dependencies in their own projects before source-import tests |
| PluginSurface rejects openai.codex | Stale catalog fixture | Enroll the real owner and exercise its source component using the existing view registry |
| ExecutionGraph cannot find Study copper | Stale UI assertion | Observe an actual execution node after removal of the redundant graph heading |
| Windows short/long temporary path mismatch | Test path comparison | Canonicalize both paths, preserve exact directory and environment checks |
| Windows smoke prints PASS then exits 1 | Test harness state leak | Restore LASTEXITCODE after failure-simulation shims; keep thrown assertions fatal |
| Chinese prose in card-design.md | Documentation defect | Translate prose in the English page; keep the language gate strict |
| Windows application tests cannot prepare default Packs | CI environment, previously hidden behind the script exit failure | Install declared optional adapters and repository plugin test dependencies in the disposable Windows CI environment, matching the Linux integration job |
| Greeter optional Python bootstrap has no native platform | CI environment/scope | Retain the original native fixture and assertions; build the same external card with `--host-only`, omitting its unused Sandbox requirement only from the external copy. This validates host installation/loading, not native Sandbox provisioning |
| Hosted browser test times out on Carol's welcome screen | Test race | A fresh builder profile always has onboarding. Wait for its Start blank control and dismiss it instead of sampling visibility before the asynchronous onboarding state arrives. CI screenshot confirmed the blocking welcome overlay |
| All three real desktop builds reject a Knowledge Base dependency wheel | Unbounded dependency across breaking upstream releases | PyMuPDF4LLM 2.0, released on October 19, is an empty compatibility package: its implementation moved into PyMuPDF 2.x. Keep Knowledge Base on the standalone converter API before 1.x and Library on PyMuPDF 1.x; retain the Adapter validators and test real local PDF conversion in backend CI |
| Desktop self-test imports a Pack dependency before activating Packs | Outdated packaging test assumption found during source audit | Run the same native PDF probe after `create_services` activates the bundled Library adapter; keep PyMuPDF out of host site-packages |
| macOS Intel rejects a dependency path hook | Platform-specific dependency incompatibility | PyMuPDF4LLM 1.x requires Layout/ONNX. Intel resolves ONNX Runtime 1.23.2, whose coloredlogs dependency ships an executable `.pth`. Retain the rejection and use the existing standalone converter API (0.3.4), where Layout is optional |
| Windows self-test cannot rename its synthetic profile after closing services | Test lifecycle assumption | Imported Pack modules intentionally retain locks until interpreter exit. Prepare the profile in a child interpreter and wait for exit before the existing backup/restore assertions, matching real update shutdown; do not shorten resource leases |
| NSIS cannot open a 261-character resource source path | Windows packaging path limit | Use temporary flat hard links during compilation, mapped to the original installed resource paths. Relative source keys avoid Tauri's Windows drive-prefix normalization. Mark the installer long-path aware and verify every installed payload file plus the existing self-test on the disposable Windows runner. Windows installation requires Win32 long paths enabled; the installer does not change system policy |
| PowerShell on the release runner cannot link a long source path | Native API path handling | Pass an extended Windows path to `CreateHardLinkW` through `New-Item`; a native probe reproduces error 3 for a 327-character plain path and succeeds for the extended path. The script regression now includes a source path over 300 characters |
| Successful full suites consume nearly the entire job budget | CI execution budget | Exact-source and PR runs passed in 39m20s and 44m21s. Allow 60 minutes for the backend job, including setup and artifact upload; leave every test assertion and test timeout unchanged |

## Verification

- Local Windows: 67 focused Python baseline/release tests passed initially;
  the expanded release gate/configuration suite passed 56 tests. Full frontend
  passed 182 files / 1200 tests; TypeScript/Vite build and 12 SDK boundary tests
  passed. Documentation passed 16 validator tests, strict MkDocs, 80 pages /
  9348 links/assets / 33 search pages. Source launcher tests passed with three
  existing POSIX-only skips; Node startup tests and both PowerShell harnesses
  passed with no leaked native exit code.
- First branch CI at `ce0c0cf6`: [validation](https://github.com/theAfish/open-agent-world/actions/runs/39421444778)
  established the source-startup fixes on all three OS runners, frontend
  build/unit/SDK success, and exposed the additional Windows dependency and
  Greeter native-platform assumptions described above.
- [Documentation](https://github.com/theAfish/open-agent-world/actions/runs/39421453416)
  passed on that SHA; Pages was correctly not deployed from `refactor`.
- [Negative release-gate acceptance](https://github.com/theAfish/open-agent-world/actions/runs/39421472247)
  rejected incomplete CI for that exact SHA. All installer and release steps
  were skipped; no formal Release or production deployment was made.
- GitHub administrator permissions were available. Ruleset 23912326 was updated
  and activated for `dev` and `main`, requiring the three aggregate checks from
  Actions app 15368, one PR approval, up-to-date branches, resolved conversations,
  and no deletion/force pushes or bypass actors. Both branch endpoints were
  read back as protected. Ruleset 25474393 blocks updates/deletion of `v*` tags.
- The original Greeter native-Python mode was also tried locally: installation
  passed, but environment preparation exceeded its timeout and shutdown did not
  drain in time. This is not native Sandbox acceptance; that mode remains a
  separate target-environment requirement. The host-only mode makes no such claim.
- Host-only Greeter passed both local production-host browser phases, including
  the declared environment, real card frontend and persisted greeting. Local
  deployment, publication and hosted-account scenarios passed; Chromium shared
  UI passed all three tests. The Linux hosted scenario then exposed the
  deterministic onboarding wait described above; no timeout was increased.
- Release selection also compares attempt start times: a failed rerun of an
  older workflow record blocks release even if its original run ID is smaller.
  Focused release quality/configuration tests passed 58 cases, including
  combined-manifest verification for manual previews without a release tag.
- Current PR checks and final validation results are attached to
  [PR #43](https://github.com/theAfish/open-agent-world/pull/43).
  GitHub reports the PR blocked with review required; no merge was attempted.
- The baseline JUnit contains 2655 cases with zero duplicate `(classname, name)`
  identities. Its 60 existing skips are explicit native Windows/macOS/WSL/Linux
  Sandbox, controlled-network, and separate scientific-environment prerequisites.
  No skip was added. Windows smoke intentionally overlaps the Linux suite to
  exercise OS behavior; PR merge validation and exact-source release validation
  are different checkout contracts, not interchangeable green checks.
- Windows core integration on the branch passed 216 tests with one retained
  platform prerequisite skip after dependency preparation was corrected
  ([run](https://github.com/theAfish/open-agent-world/actions/runs/39422789329)).
  That workflow still failed its hosted browser timing check; this partial result
  is not presented as an overall green run.
- At `41ad117d`, both [exact-source CI](https://github.com/theAfish/open-agent-world/actions/runs/39425701900)
  and [PR merge-tree CI](https://github.com/theAfish/open-agent-world/actions/runs/39425701245)
  passed: backend 2620 passed / 60 existing skips, Windows 216 passed / one
  existing skip, frontend 1200 tests, 12 SDK tests and 11 browser cases.
  JUnit comparison retained all 2655 baseline cases and added 25, with no
  removed cases or new skips. Documentation and all three native targets passed.
- The [real preview](https://github.com/theAfish/open-agent-world/actions/runs/39431614575)
  then passed its source gate but failed all three payload builds on the same
  empty `pymupdf4llm-2.0` wheel. Clean host-only constraints reproduced it locally;
  the existing developer environment had retained 1.28.2 and concealed the drift.
  [The upstream release](https://pypi.org/project/pymupdf4llm/2.0/) documents the
  packaging/API move. The fix belongs to Knowledge Base and Library dependency
  declarations, not host dependencies or relaxed wheel integrity rules.
