# Schema 1 removal follow-up - 2026-10-23

> Historical record, not current product or deployment instructions. Verify against the current source before use.


Current implementation supports Schema 2 content and Schema 3 code-capable Packs
only. Schema 3 is the canonical authoring workflow; Schema 2 remains temporary.
There is no Schema 1 reader, conversion path or archive loader.

OAW changes are in the `dev` working tree. Marketplace changes are in
`D:/AI/oaw-marketplace-pack-distribution` (`codex/pack-distribution`). Existing
interop changes were preserved; the main Marketplace checkout's account and
infrastructure work was not changed. No deployment or GitHub Release was modified.

Removed:

- Schema 1 manifest acceptance and derived backend module names.
- Installed wheel ZIP-import fallback and the old host-only Requires-Dist resolver.
- Schema 1 registration/installation branches and positive acceptance fixtures.
- The raw-directory `open_agent_world.pack.build` helper and CLI command; its last
  consumers were the replaced Greeter and Schema 1 interoperability fixture.
- The obsolete `open_agent_world.setup` executor. Schema 3 preparation already uses
  the installation manager; undeclared setup hooks now fail before installation.
- Greeter's handwritten Schema 1 manifest, optional unused colorama fixture path,
  `--host-only` build variant and hardcoded historical Release digest requirement.

Greeter 0.2.0 uses `oaw.pack.toml`, a real uv-built Python wheel, the SDK frontend
builder and `build-project`; distribution/owner/card IDs are unchanged. CI and
current guides use that workflow. Negative tests keep unsupported Schema 1 input
solely to verify rejection. Historical release evidence below is not a prerequisite.
Marketplace rejects registration and hides unsupported stored versions/downloads;
listings with an unsupported latest version stay hidden until a supported new
version is published. It does not delete database rows or remote artifacts.

Retained mechanisms have separate consumers: Python factories and installed entry
points for source/desktop plugins; the existing PluginRegistry and installation
manager; the independent content exporter and Schema 3 `build-project` compiler;
Schema 3's shared Sandbox Python declarations; and startup/environment receipt
formats, whose version numbers are independent of archive schema. Source Python
package discovery is not a Schema 1 archive reader. MatCreator's old setup metadata
is still stripped from built wheels; it is not executed. The 13 official plugin
implementations were not rewritten.

## Validation actually performed

- Initial OAW run: 77 passed across `test_pack_installation.py`,
  `test_pack_unification.py`, and `test_pack_build_project.py`.
- OAW subsystem run: 206 passed, two symlink-permission skips, and one obsolete
  ownership fixture failed because it omitted Schema 3 backend modules. That
  fixture was corrected. This run covered installation, content, declarative
  Packs, modules, startup, adapters, specifications, source/card ownership,
  lifecycle, plugin discovery and registry.
- Follow-up OAW run: 107 passed across `test_pack_card_ownership.py`,
  `test_plugin_setup.py`, `test_pack_unification.py`, `test_pack_build_project.py`,
  `test_passive_pack_activation.py`, and `test_marketplace.py`. This includes the
  corrected fixture and the setup-hook rejection path.
- Marketplace: 107 passed, one explicitly gated live Release test skipped; SQLite
  inspection, registration, catalog pagination, downloads, GitHub protocol mocks,
  capacity migration and immutability checks. Ruff lint/format passed. Tests ran
  against an isolated copy; all 13 applied files were byte-compared with that copy.
- Cross-repository `scripts/test_oaw_distribution.py`: three passed. Real Schema 2
  export, Schema 3 declarative build and Python-backed build were registered,
  served through Marketplace ASGI, acquired through OAW's Store API, installed and
  registered. Only GitHub HTTP was simulated.
- Strict source registration loaded all 13 official Schema 3 runtime owners.
- External Greeter built successfully with uv/npm and the canonical builder.
  Chromium production-host acceptance passed five phases: local install/restart,
  Store install/restart and Store-offline saved-card use. An initial browser run
  exposed a stale onboarding wait; the helper now dismisses the modal before
  waiting for the Library button. Both complete scenarios then passed.
- After removing the unused raw-directory builder, the declarative and build-project
  CLI tests passed again; invoking the removed `build` command exits with an invalid
  command error.
- `git diff --check` passed in both worktrees; changed JavaScript builders passed
  Node syntax checks.

Browser receipts:

- `.outputs/pack-acceptance/local-1792733290377/receipt.json`
- `.outputs/pack-acceptance/store-fake-1792733147214/receipt.json`

Not performed: PostgreSQL execution, live GitHub Release acceptance, publishing,
production deployment, packaged desktop/Tauri/installer acceptance, or the full
cross-project test suites. Source registration does not validate every scientific
worker, external service or credential-dependent plugin action.

---

> Historical interoperability record follows. Its Schema 1 support claims and
> pinned old release are superseded by the cleanup above. Current contracts are
> documented in [Pack distribution](../../pack-distribution.md).

# Pack Marketplace interoperability — 2026-10-23

Implementation bases were checked against GitHub during this work:

- OAW `dev`: `53ad54b00a7d35c187d9329d461f6151c949f94b`.
- Marketplace `main`: `12bcc0335d46a7552ff0cb7c5c9a853cc8b51eb3`.

Both original working directories had uncommitted changes. Development and
verification used isolated checkouts of these commits. The OAW changes touch
only archive verification, its regression tests and this report; Marketplace
changes are delivered in a separate `oaw-marketplace-pack-distribution` checkout
on `codex/pack-distribution`. Existing Marketplace account/deployment work is
not part of this change. No remote commits, releases, CI runs or deployments
were created.

## Actual incompatibilities and fixes

1. Marketplace `ManifestIdentity` accepted only schema 1. It now accepts integer
   schemas 1, 2 and 3 without interpreting executable/content/runtime internals.
   Unknown fields remain in the original parsed manifest. Unknown schema
   versions and malformed identities still fail inspection.
2. Marketplace's distribution-ID regex rejected repeated/trailing hyphens that
   OAW's archive manifest and prebuilt-artifact builder accept. It now matches
   `PackManifest.PACK_ID`; no IDs or versions are normalized or rewritten.
3. Marketplace limited manifests to 256 KiB, versus OAW's 2 MiB. Inspection now
   uses the OAW limit. Migration `0002_manifest_capacity` widens the serialized
   JSON constraint to 12 MiB to allow serializer escaping, retaining old rows,
   GitHub references, latest-version foreign keys and immutability triggers.
   A real OAW-built advanced plugin with a manifest over 256 KiB passed the
   complete local flow. UTF-8/escaped JSON round trips are also covered.
4. Version pagination was bounded only by row count, allowing several valid
   large manifests to exceed OAW's 8 MiB metadata response cap. Marketplace now
   also bounds each page's manifest payload to 6 MiB and returns a continuation
   cursor for short pages. No checksum work is added to catalog operations.

## Contract and intentional differences

The immutable catalog key is `(manifest.id, manifest.version)`; versions use
canonical PEP 440. Display listing fields are editable independently of the
immutable manifest. Compatibility and declared dependencies remain in the
original JSON. The artifact record retains full-file SHA-256, byte size and
GitHub Release/asset IDs; provider references stay server-side. The original
`.oawpack` bytes, MIME type, release tags, asset names and download API remain
unchanged. No schema conversion or new installer is introduced.

Schema 1 executable Packs, schema 2 exported content and schema 3 declarative
or advanced plugins intentionally have different contents. In schema 3,
distribution ID, runtime owner, provided Pack IDs and card types remain
distinct. Marketplace applies its ID constraints only to distribution keys.
It preserves dependency Pack IDs and optional supplying distribution IDs.
OAW remains responsible for compatibility, owner/contribution rules,
dependency selection, archive safety, runtime preparation and activation.

Marketplace production code imports no OAW runtime code. Its existing bounded
identity parser is the only schema-specific distribution acceptance point;
storage/catalog/downloads retain opaque original JSON. Future card interfaces,
relationships, configuration and runtime declarations can therefore change in
OAW without a Marketplace storage redesign. A genuinely new archive schema
version still requires an explicit acceptance decision. The detailed contract
and opt-in cross-repository check live in Marketplace's
`docs/distribution-contract.md` and `scripts/test_oaw_distribution.py`.

OAW's source `PackSpec` identifier grammar differs from its archive distribution
grammar. This source-authoring inconsistency is documented here, not refactored:
Marketplace follows the emitted archive contract. OAW's Store display DTO also
contains typed runtime fields for its own preliminary installation plan; that
host-side coupling does not require Marketplace to interpret those fields.

## Integrity simplification

`inspect_archive` already verifies every archived file against `checksums.json`.
Environment lock, resource and bundled-executable declarations now compare
against those verified digest values instead of hashing the same in-memory
bytes again. Missing resources, declaration mismatches and executable size
mismatches still fail. Separate adapter-wheel checks remain intact.

Local publication inspection, remote publication verification, server download
verification, OAW download verification and installer inspection remain in
place: each protects a distinct trust or lifecycle boundary. No checksum
database, cache synchronization or alternative runtime registry was added.

## Validation actually performed

Environment: isolated Linux Python 3.12.13 with dependencies satisfying both
repositories' requirements, assembled from local cached packages. Threaded ASGI
tests required execution outside the sandbox because its event-loop wakeup
stalled; fixtures still used temporary local profiles and simulated GitHub HTTP.

| Checks | Result |
| --- | --- |
| Marketplace inspection, capacity/migration, catalog, CLI, downloads and GitHub adapter: six selected test files | 105 passed |
| OAW Pack installation, unification, build-project, content, declarative, Marketplace client and Store: seven selected test files | 160 passed |
| Cross-repository real build/export → inspect → CLI seed with remote verification → catalog/detail/version retrieval → verified download → OAW Store install → installed plugin loading | 4 passed |
| Marketplace Ruff lint (`src tests migrations scripts`) and formatting of all changed Python files | Passed |
| SQLite `alembic check` against an acceptance database | No model/schema drift |
| Both change sets: `git diff --check` | Passed |

The four integration cases use OAW's existing fixtures and actual builders:
schema 1 `open_agent_world.pack.build`, schema 2 `export_archive`, and schema 3
`build_project` for both declarative and advanced plugins. They exercise real
GitHub adapter and publication verification logic with HTTPX MockTransport at
the GitHub boundary, real SQLite transactions and ASGI responses, the actual
OAW Marketplace client and Store endpoint, and the existing installer/loader.
The declarative case uses different distribution, underscore-containing owner,
provided Pack and slash-containing card IDs. Content dependencies survive the
round trip. The advanced case carries a large valid original manifest.

Negative coverage includes unsupported schemas, unsafe/noncanonical identity,
oversized JSON, duplicate publication, failed remote verification without
registration, raw database mutation/deletion, migrated immutable history,
corrupt/truncated/substituted downloads, cancellation, dependency/compatibility
failures, archive checksums and independent runtime digest declarations.
Existing Starlette/AnyIO deprecation warnings remain; no failing assertions or
skips were added to make validation pass. No full cross-project suite was run.

Reproduce the interoperability cases from the Marketplace checkout:

```sh
OAW_SOURCE_ROOT=/path/to/open-agent-world PYTHONPATH=src \
  python -m pytest scripts/test_oaw_distribution.py -q
```

## Remaining acceptance and follow-up

- PostgreSQL was not available locally. Its migration branch and expanded
  metadata persistence require the existing PostgreSQL CI job before merge;
  SQLite migration from a populated v1 catalog was verified locally.
- GitHub Release upload/download against the real service, native desktop UI
  acceptance and production infrastructure were not exercised. Prior published
  Greeter acceptance in Marketplace docs is historical evidence, not a result
  of this run. The same schema 1 format and storage conventions remain covered.
- The independent capacity migration is based on published `main`. If the
  existing uncommitted Marketplace identity migration is landed first, reconcile
  the Alembic revision chain before merging this change. Do not apply two
  independent heads to a deployment without resolving their ordering.
- Install the paired source changes and run normal repository CI; then perform
  explicit live Release/native acceptance in a separate authorized task. No
  website, account, provider, card-system or production work is included here.
