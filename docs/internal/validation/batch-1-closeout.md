# Batch 1 closeout audit

Date: 2026-10-21. Audited `feat/batch-1-plugin-lifecycle` at `9fc0d86b`
against freshly fetched `origin/dev` (`7cf8a821`, includes merged Batch 0 PR #43).
The earlier [Batch 1 checkpoint](batch-1.md) describes the implementation before
this closeout. This report separates new work from retained contracts.

## Findings and disposition

| Finding | Disposition |
| --- | --- |
| Saved-card `on_startup` could abort Core after successful plugin registration | Isolate optional owners only after successful bounded compensation; mark downstream owners unavailable |
| One `on_shutdown` error skipped remaining callbacks | Drain other callbacks/services and report aggregate failure |
| Optional Runtime Provider creation/restoration could fail through a Core Agent | Diagnose provider failure, revoke the optional owner, remove attempted transient Agent records, preserve Core and saved Agent data |
| Work-source recovery and background probes could hide persistence failures | Propagate unsafe failures; stop unsafe monitoring, retain durable jobs and never resubmit uncertain external work |
| Missing cards could break unrelated uninstall usage checks | Resolve missing-card ownership from persisted placeholder metadata |
| Default setup prepared every official plugin | Core is now default; `--with-plugins` is explicit; `--plugin-path` prepares only requested projects |
| Schema 3 source discovery duplicated editable package enrollment | Remove that install step; keep managed dependency/environment preparation and desktop target installation |
| Manifest cards could not share registration with Python | Compile them into the same staged collector before the existing advanced hook; API 1.38 |
| Installation always required restart | Activate new verified passive/content owners through the existing Registry, installer, Library transaction and catalog event |
| External frontend preparation assumed checkout `file:` SDK | Accept pinned package/tarball dependencies; keep local symlink as checkout optimization; upload versioned SDK artifact in CI |
| External tooling required several low-level discovery steps | Add `pack init` and project-scoped `pack check --lifecycle --card`; reuse existing build/install commands |
| Store, startup failures, setup and restart documentation was stale | Correct Card Library, plugin reference, distribution, setup, tutorial and Notes guides |

Schema 1/2 readers, Legacy Entry Point discovery, legacy setup hooks, old Pack
ownership/configuration migrations, deletion recovery journals and existing
resource leases remain. Tests and source/desktop callers still use them. Shared
legacy Sandbox preparation remains a compatibility contract, not the recommended
Schema 3 environment model. No second Registry, installer, Sandbox or permission
authority was introduced. Ordinary dev startup still does not install dependencies.

## Failure and recovery guarantees

Optional startup failure records an explicit `Unavailable` diagnostic, without
changing the user's durable enable setting. Existing WorldStore placeholders and
live Capability derivation make its cards/relationships non-executable. Cleanup
runs in reverse order for both completed and attempted restorations. Dependent
owners are denied too; independent owners remain usable. Restart after repair
reuses original IDs, configuration, connections and data.

The new public `PluginIntegrityError` declares uncertain plugin persistence or
compensation. Database errors (including wrapped errors), unfinished host
transactions, unresolved deletion journals, security violations, resource
exhaustion and failed/timed-out cleanup do not become optional warnings. Core
callbacks remain fatal. Strict mode also rejects safely isolated optional errors.

This is cooperative isolation of trusted code in one Python process. It cannot
undo arbitrary module globals, kill a callback that blocks the interpreter,
recover native crashes, or recover resources hidden by a factory that raises
before returning them. Such factories must perform their own compensation.
There is no forced `sys.modules` replacement or Python hot unload.

## Installation activation matrix

| Type | Running host behavior | Evidence / boundary |
| --- | --- | --- |
| New Schema 3 pure declarative cards | Immediate, including their UI | Real Edge Library install, same-page catalog update, create/edit/reload, then persistence across restart |
| New Schema 2 content Pack | Immediate | HTTP integration: install, collect and instantiate Legion without service restart |
| JS/CSS-only card implemented through pure contributions | Immediate first install | External Notes independently builds against packed SDK; host frontend is reused |
| Advanced or mixed trusted Python Pack | Selected, pending restart | Arbitrary code and modified generated wheel tests prove no live import |
| Upgrade of any already loaded owner | Selected, pending restart; old version remains active | Old frontend URL and saved config remain valid; selecting retained version cancels pending upgrade |
| Unload, source changes, held native libraries/background tasks | Restart boundary remains | Existing ownership, work blockers and process leases retained |

Immediate registration is staged in a copy of the existing Registry and published
only after Library persistence succeeds. Pure adapter contents must match the host
compiler exactly, not merely claim a passive label. Existing install verification
and immutable receipts remain authoritative. There is no automatic backend restart
and no interruption of active Agent/Sandbox work. The Library's selected/loaded and
restart-required display is retained. A safe Desktop Apply Changes operation,
loaded-version replacement, and failed-upgrade automatic rollback need separate
work; manual selection of retained versions already works.

## Official Pack migration inventory

All 13 official projects have Schema 3 declarations. IDs and stored formats are
unchanged. Python registration remains appropriate wherever it expresses real
behavior. No official project is fully passive after accounting for state,
deployment, tutorials or execution contracts.

| Project / owner | Classification | Closeout decision |
| --- | --- | --- |
| `structure_viewer` / `science.structure-viewer` | Simple UI plus advanced metadata | Actually migrate presentation/UI/size/category to contributions; retain Python state, tutorials, traits and deployment. Same card and default Pack IDs |
| `agent_barracks` / `oaw.barracks` | Medium container behavior | Keep advanced API; possible later metadata-only reduction |
| `skill_packages` / `oaw.skills` | Medium lifecycle/state | Keep advanced API |
| `task_board` / `oaw.tasks` | Medium documents/execution | Keep advanced API |
| `sqlite` / `data.sqlite` | Medium data/query | Keep advanced API |
| `visualization` / `data.visualization` | Medium multi-card data/remapping | Keep shared Python definitions; avoid duplicating generated definitions in TOML |
| `codex` / `openai.codex` | Complex Runtime Provider | Keep advanced API and client environment |
| `card_factory` / `oaw.factory` | Complex authoring | Keep advanced API |
| `library` / `research.library` | Complex PDF/document research | Keep advanced API |
| `knowledge_base` / `knowledge.base` | Complex PDF/data research | Keep advanced API and explicit adapter preparation |
| `atomsculptor` / `atomsculptor` | Complex research | Keep scientific business logic and 11 environments |
| `matcreator` / `matcreator` | Complex research | Keep advanced SDK and 7 environments; real managed worker compatibility checked |
| `xrd` / `research.xrd` | Complex research with Host coupling | Keep behavior; `runAnalysis`, `getInputs`, `stopAnalysis` boundary remains Batch 2 |

The external Notes example is the fully declarative reference. Multiple cards use
repeated contribution tables with explicit card IDs; owner, version and default
Pack need not be repeated. Advanced Python may extend the staged definitions or
register additional contributions in the same transaction. Legacy advanced
plugins without contributions are unchanged. Empty contribution defaults do not
change old source dependency receipts.

## Validation

Local results are scoped evidence, not proof of real external models/cloud work
or installed Desktop behavior. Suite counts below overlap and should not be added.

| Check | Result |
| --- | --- |
| Clean Core interpreter HTTP startup | Passed: 25 locked Core distributions, zero optional plugins, 14 Core card types |
| Lifecycle/loader/Registry/state/install/background regression | 222 passed (`.outputs/batch1-close-regression.xml`) |
| Final fault injection, mixed CLI and setup regression | 101 passed (`.outputs/batch1-close-final.xml`) |
| Final declaration, file-preview and installation checks | 59 passed (`.outputs/batch1-close-receipt-final2.log`) |
| Frontend unit suite | 182 files, 1200 tests passed |
| Frontend SDK/startup Node checks | Passed (`.outputs/batch1-close-node-tests.log`) |
| TypeScript and Core production frontend | Passed (`.outputs/batch1-close-frontend-build.log`) |
| External declarative Notes | Three Edge phases passed; first install/use share one backend process and existing page |
| Advanced MatCreator managed worker | 3 passed, real isolated worker/SDK with scripted local HTTP model |
| Migrated Structure Viewer | Real wheel + frontend archive built; clean Core interpreter installed/loaded/created a card/served assets and restored saved config |
| Documentation | 16 tests, strict build and rendered local-link checks passed |

Fault injection covers second-card restoration after successful registration,
downstream denial, compensation failure, shutdown failure, provider restoration,
unfinished transactions, security/SQLite failures, strict-mode rejection and
background observation corruption. Persisted cards, edges, user files and live
grants are checked before failure and after repair. Mixed advanced-hook failure
publishes neither of its two declared cards. A modified generated wheel cannot
qualify for immediate activation.

Browser receipt: `.outputs/pack-acceptance/local-1792552701049/receipt.json`.
External artifact SHA-256:
`350648838957ecce1a9f91c4995d50eb4316146a93b7825c7e251a2bf70d7852`.
The host frontend was built before installation. The external project lived in
the OS temporary directory, not `plugins/`.

Reproduce focused checks with the OAW backend interpreter:

```sh
python -m pytest backend/tests/test_plugin_lifecycle_isolation.py backend/tests/test_plugin_failure_isolation.py backend/tests/test_passive_pack_activation.py backend/tests/test_declarative_packs.py backend/tests/test_background_jobs.py
python scripts/oaw-pack.py check examples/packs/notes --lifecycle --card example.notes
python scripts/build-external-notes.py
node frontend/scripts/run-pack-e2e.mjs --declarative
```

The GitHub `Pull request checks` workflow retains strict official plugin activation,
all official frontend builds, backend/public SDK suites, launcher OS matrix and
production UI acceptance. It now uploads the public SDK tarball and tests Notes
without a restart between installation and use. Consult the workflow run for the
tested commit; local checks alone do not establish a remote CI pass.

The first complete CI attempt on `dd55852f` exposed a Notes browser-test deadline:
the response wait started before the full official catalog had finished rendering
the clickable installer. The server returned 201 after about 3.6 seconds, but the
15-second browser deadline had already elapsed. The test now waits for the panel
and uses the existing installation-acceptance 120-second response budget, while
still asserting the real response, same-page catalog and persistence. Core runtime
failure and provider cleanup timeout checks also passed in a separate 21-test run.

Full Linux CI then completed 2660 tests with 60 retained skips and one teardown
error: a Sandbox cancellation test left its injected cleanup timeout active during
the application's newly bounded shutdown. The injection is now scoped to the
cancel operation. Its conflict, pending-cleanup and live-command assertions remain;
normal fixture shutdown uses the actual cleanup implementation.

## Remaining independent work

- Publish a supported standalone Python CLI and public SDK release channel.
  The current wrapper still requires an OAW checkout/interpreter; CI SDK artifacts
  remove the need for local SDK packing but are not an npm registry release.
- Add external source frontend hot reload. Independent build/install works now;
  updates to a loaded release still require a process restart.
- Design Desktop Apply Changes around existing task admission and restart ownership.
  No safe cross-platform automatic restart contract was established in this audit.
- Address XRD Host-specific APIs in Batch 2, and stronger crash/native-library
  isolation in a future Extension Host design if needed.
- The existing developer environment has `pymupdf4llm` outside Knowledge Base's
  declared range. It is diagnosed as optional-unavailable; this audit does not
  downgrade the user's host environment to make it pass.
