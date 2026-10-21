# Batch 1: optional plugin startup and declarative development

Base: `dev` at `7cf8a8211c0377d55075b58d7ed44cc6406b8edb`, the merge of
[Batch 0 / PR #43](https://github.com/theAfish/open-agent-world/pull/43).

## 1A audit and implementation

Core is the `open-agent-world.core` registration: world persistence, native card
and relationship contracts, permissions, conversations, execution services,
Sandbox interfaces, HTTP/UI and Pack installation. Its 14 card types do not
require any package under `plugins/`. ADK and LiteLLM are optional runtime SDKs;
their adapters remain lazy. Core initialization, database corruption, ownership
reconciliation and resource-accounting failures must remain visible failures.

All 13 official source plugins already have Schema 3 declarations. The existing
installer already owns dependency wheels, environments, immutable releases,
receipts, recovery and resource leases. `PluginRegistry.install` already stages
contributions. `WorldStore` already retains unavailable cards/edges and the
Capability broker already excludes their grants. No replacement manager,
permission layer, Sandbox or parallel contribution registry is needed.

| Path | Before | After 1A |
| --- | --- | --- |
| Source discovery | Adds import paths during discovery; one invalid manifest aborts | Static pass before import paths; per-source diagnostics |
| Source activation | Adapter/import/registration failure aborts the process | Failing owner is unavailable; independent owners load |
| Dependency order | All factories count as available before registration succeeds | Only committed registrations satisfy downstream dependencies |
| Registration | Sandbox requirements can fail after contributions commit | All loader validation precedes commit; Pack catalog construction is staged |
| Installed Packs | Whole selection and all startup receipts checked as one fatal operation | Existing manager checks each ready candidate against loaded selections; failed dependency consumers remain unavailable |
| Retained resources | Reconciliation assumes discovery is complete | Partial discovery retains existing source claims and defers collection |
| Development preflight | Requires every editable plugin and prepared environment | Core prerequisites only; loader owns optional diagnostics |
| Vite | One optional build or manifest can stop the host | Dev reports the failing Pack and serves other artifacts; stale failed assets return 404 |
| Setup | Full optional preparation can stop Core setup | `--core-only` omits optional Python/Pack installation; normal setup reports independent optional failures |
| CI | Product startup also acts as the strict gate | Explicit strict activation plus strict frontend builds; separate clean Core HTTP probe |

`GET /api/packs` exposes failure origin, phase and a repair/restart action. The
Library displays unavailable entries even when no plugin code registered.
Existing restore/install/retained-version activation operations perform explicit
preparation; restart retries activation. There is no in-process hot reload.
Startup does not install packages or silently repair corrupt receipts.

Run `scripts/setup.py --core-only` for minimal setup and set
`OPEN_AGENT_WORLD_CORE_ONLY=1` when starting a deliberately Core-only source
checkout. Normal source startup discovers optional folders even if their Python
packages were not installed editable. Missing dependencies become unavailable
diagnostics. Existing full setup remains supported, including optional runtime
extras. Ordinary dev/start remains installation-free.

## Validation

The fresh `.tmp/batch1-core` interpreter installed only the 25 applicable locked
Core distributions, using the existing uv cache after network downloads stalled.
It contained no plugin entrypoints, ADK, LiteLLM, NumPy, ASE or PyMuPDF.
`scripts/check-core-startup.py` started a real uvicorn child process and read
`/api/catalog`, `/api/packs` and `/api/world` over localhost HTTP: passed, with
14 Core card types. The script shuts the server down through its lifespan and
also runs in CI in a separate minimal environment.

Fault-injection tests stage a card, relationship and Capability before raising;
assert that none are registered; reopen the same SQLite workspace; verify saved
configuration/geometry and IDs, missing placeholders and absence of grants; then
repair the plugin and verify recovery. Tests also cover discovery/import errors,
dependency consumers, strict failure reporting and fatal Core/database failures.

Focused backend checks passed for loader, registry, installed startup, installer,
source ownership, missing plugins, source launchers and lifecycle recovery.
The real Vite test serves an independent artifact while a sibling build fails,
and verifies that the same defect fails a strict build. Card Library and plugin
surface tests passed (26 cases); TypeScript checks passed.

A lifecycle recovery test exposed the local `pymupdf4llm` version (1.28.2)
conflicting with Knowledge Base's `<0.4` declaration. That plugin is correctly
unavailable; this must not block restoring unrelated Barracks. The restoration
check now considers loaded source adapters plus the explicit recovery target.
No host dependency was downgraded to conceal this condition.

## Migration inventory and follow-up boundaries

| Official source | Category | Existing IDs and migration decision |
| --- | --- | --- |
| `agent_barracks` | Medium | `oaw.barracks`, 2 types; retain container behavior |
| `skill_packages` | Medium | `oaw.skills`, 2 types; retain lifecycle/state behavior |
| `task_board` | Medium | `oaw.tasks`, 1 type; retain document/execution behavior |
| `codex` | Complex runtime | `openai.codex`, 1 type; retain Runtime Provider and client environment |
| `card_factory` | Complex | `oaw.factory`, 6 types; retain content authoring |
| `sqlite` | Medium | `data.sqlite`, 1 type; retain data and query contracts |
| `structure_viewer` | Simple UI with advanced metadata | Migrated redundant owner/Pack/backend declarations to defaults; `science.structure-viewer` and its default Pack are unchanged; Python tutorials/deployment remain intact |
| `visualization` | Medium | `data.visualization`, 5 types; retain data consumer/remapping contracts |
| `library` | Complex research | `research.library`, 2 types; retain PDF/document data |
| `knowledge_base` | Complex research | `knowledge.base`, 1 type; PDF adapter dependency requires explicit preparation |
| `atomsculptor` | Complex research | owner `atomsculptor`, Pack `atomsculptor.default`, 4 types; retain 11 environments |
| `matcreator` | Complex research | owner `matcreator`, Pack `matcreator.default`, 11 types; retain 7 environments |
| `xrd` | Complex research | `research.xrd`, 9 types; XRD-specific Host SDK helpers (`runAnalysis`, `getInputs`, `stopAnalysis`) remain a boundary blocker |

The batch does not change existing card, relationship or Pack IDs, saved config
formats, layouts or schema migrations. In-process Python cannot undo arbitrary
module side effects or isolate incompatible native libraries. Registry atomicity
is a contribution guarantee, not an independent-process host. Legacy shared
Sandbox Python still prepares one shared dependency set; its affected owners
share an environment failure, without stopping Core or unrelated owners. A
separate Host/Plugin boundary batch is required for XRD and stronger runtime
isolation. Real external models, cloud execution, macOS and installed desktop
acceptance are not established by these local checks.

## 1B implementation

Schema 3 now accepts `[[contributions.cards]]`. Identity/version defaults are
resolved by the existing static reader; configuration compiles to Pydantic
models, and registration uses the same atomic Registry path. The initial form
deliberately covers passive cards, one Pack and an optional independent UI.
Advanced factories remain unchanged. API 1.37 adds the declarative adapter;
old plugins keep their declared API versions. Empty contribution defaults are
excluded from the source digest to preserve existing preparation receipts.

`build-project` generates a normal pure Python wheel when there is no Python
entrypoint, including normalized contribution metadata and standard RECORD
hashes. Archive validation, ownership, installation, receipts, source/installed
activation and permission checks are shared with advanced plugins. The new
`scripts/oaw-pack.py` wrapper allows the existing CLI to run from an external
directory. No repository registry edit is required.

The Notes example was copied to a real directory outside the repository,
installed the packed SDK from `.outputs`, and built its frontend without host
source imports or Python plugin code. Static diagnosis, `.oawpack` build and
archive inspection passed. Source/installed catalog equivalence, JSON config
constraints and restart persistence passed in backend tests. The existing
Structure Viewer manifest uses the new defaults while preserving all IDs and
business behavior; broader official migrations remain separate work.

## Recorded local results (2026-10-21)

| Check | Result and evidence |
| --- | --- |
| Clean Core HTTP startup | Passed twice in the fresh 25-package interpreter; 14 Core types and zero optional plugins |
| Focused backend integration | 200 passed, 1 skipped; `.outputs/batch1/focused-tests.xml` |
| Final isolation/declaration/source-launcher review | 47 passed, including stale editable metadata, three ambiguous source owners, retained Core-only resource claims and fatal accounting errors; `.outputs/batch1/final-audit.xml` (overlaps the focused suite) |
| Advanced MatCreator/Task Board compatibility | 39 passed, 1 skipped; `.outputs/batch1/advanced-tests.xml` |
| MatCreator managed worker | 3 passed using the existing isolated environment, real worker/SDK and a local scripted HTTP model; checkpoint/executor/recursive child/tool failures and live tool revocation; `.outputs/batch1/managed-worker.xml` |
| Frontend SDK and startup scripts | 16 passed, including actual Vite serving after a sibling build failure |
| Library and plugin surfaces | 26 passed |
| TypeScript and Core production UI | Passed; Core artifact in `.outputs/batch1/frontend` |
| External declarative Notes | 3 real Edge browser phases passed: Library installation, activation/edit/reload and another host restart; same card identity/configuration/geometry, no horizontal overflow |
| Documentation | 16 tests, strict MkDocs build, 81 rendered pages and 9540 local links/assets passed |

Notes evidence: `.outputs/pack-acceptance/local-1792546880145/receipt.json`
and `declarative-note.png`. The artifact is
`.outputs/declarative-acceptance/oaw-external-notes-geny7j3y.oawpack`, SHA-256
`36e9f411eb37ee6d069b66d2ae8d9262180078022af804513d71a8f8ba31ca80`.
Its project was built in the system temporary directory outside this checkout.
The host frontend was built before installation and reused across both restarts.

Representative reproduction commands (POSIX syntax; select the corresponding
`Scripts/python.exe` and `PLAYWRIGHT_CHANNEL=msedge` on Windows):

```bash
.tmp/core-only/bin/python scripts/check-core-startup.py
backend/.venv/bin/python -m pytest backend/tests/test_plugin_failure_isolation.py backend/tests/test_declarative_packs.py backend/tests/test_source_scripts.py
node --test frontend/pack-sdk/build.test.mjs frontend/scripts/source-startup.test.mjs
npm --prefix frontend run build:pack-sdk
npm pack ./frontend/pack-sdk --pack-destination .outputs
backend/.venv/bin/python scripts/build-external-notes.py
OPEN_AGENT_WORLD_CORE_ONLY=1 npm --prefix frontend run build
PLAYWRIGHT_CHANNEL=chromium node frontend/scripts/run-pack-e2e.mjs --declarative
OAW_MATCREATOR_MANAGED=1 backend/.venv/bin/python .github/run-backend-tests.py plugins/matcreator/tests/test_native_runtime.py -k 'pack_engine_planner_checkpoint_executor_recursive_child_and_oaw_tool or bridge_resolves_live_tools_after_revocation'
```

The managed-worker command requires an already prepared MatCreator environment.
The Core interpreter must be prepared from the locked no-extra/no-dev export as
shown in `.github/workflows/ci.yml`. Local tests are scoped evidence: the entire
repository suite and remote GitHub Actions have not run for this branch. CI now
includes strict official activation/build gates and the external Notes flow;
their remote results remain to be obtained after publication.
