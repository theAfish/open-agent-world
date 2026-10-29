# Pack installation and distribution

OAW's user model is **Pack → Card Library → Deck → World**. Local `.oawpack` files
and Store downloads use the same installer, dependency checks, immutable version
storage and activation boundary. Code Packs include their backend and
frontend; [content Packs](creator-packs.md) carry portable Legion templates and
reference their required Packs without bundling executable plugins.

`PluginRegistry`, `PluginDescriptor`, `FrontendPlugin` and runtime owner are
internal implementation concepts. Installed and bundled Packs use the existing
registry, collection, enable/disable controls, lifecycle checks and deck storage.
Schema 3 is the canonical code-capable authoring and distribution format. It
records the distribution, runtime owner and provided Pack IDs independently.
Schema 2 is supported for code-free content creation. Schema 1
is unsupported and rejected; there is no compatibility loader or automatic migration.

## Source declaration and schema 3

Core essentials includes Agent Barracks, Summoning, Skill Toolboxes, Skills and
Task Board. Existing card and relationship IDs are preserved. Saved worlds,
collections and Legion templates migrate the former utility ownership to Core;
existing content archives can still require the incorporated utility versions.
These utilities no longer appear as independently removable Packs.

Published applications pin their complete plugin set. Upgrading an existing
deployment therefore also requires an operator-reviewed update of its deployment
manifest, or rebuilding and republishing it. Preserve all remaining version pins,
permissions and layout. Remove a former Codex pin only after confirming the
deployment contains no Codex cards. The strict startup version check remains in
effect; changing source files alone does not upgrade a published application.

Codex is an optional Schema 3 project in `packs/codex`, outside bundled source
discovery and desktop packaging. Build and install its `.oawpack` using the
[Codex instructions](../packs/codex/README.md). Publishing that archive to the
Marketplace is a separate author/operator action.

See the [source manifest reference](reference/pack-manifest.md) for identity, components,
dependency groups, environments and diagnostics.

## Archive and Python contract

A `.oawpack` is a ZIP with `manifest.json`, `checksums.json`, optional assets and
README, and either code-free Schema 2 content or Schema 3 backend/frontend
artifacts. Generate the contract with `python -m open_agent_world.pack schema`.
Inspection reads metadata without importing Python or evaluating frontend code.

Schema 3's compiled manifest records `owner`, `packs`, `node_types`,
`backend_modules`, entrypoints and dependency/environment declarations from
`oaw.pack.toml`. Module roots must be private `oaw_` packages declared by the
Python project. They need not be derived from the distribution ID. The wheel
contains exactly one `open_agent_world.plugins` factory named for the runtime
owner, for example:

```toml
[project.entry-points."open_agent_world.plugins"]
"example.greeter" = "oaw_pack_example_greeter:create_plugin"
```

The installer verifies and materializes these modules before selection. Startup
uses prepared modules and the existing PluginRegistry; archive ZIP importing is
not supported. Adapter dependencies follow the shared, locked wheel mechanism
in the [source manifest reference](reference/pack-manifest.md). Factories use `open_agent_world.plugin_api`, return `PluginDefinition`,
and register the declared provided Packs and owned card types atomically.
Descriptor identity, version, API and dependencies must agree with the manifest.

Distribution IDs are lowercase dot-separated names (hyphens allowed), and versions
use canonical PEP 440 spelling. Keep distribution, runtime owner, provided Pack,
card and relationship identities stable across releases. Missing, disabled,
incompatible or cyclic Pack dependencies block selection.

`checksums.json` covers every archive file except itself. Existing size, path,
symlink, duplicate-entry and wheel ownership checks apply to both formats.
Checksums detect corruption; they do not authenticate publishers. Python and
frontend code execute as trusted application code, without OS-level isolation.

Core components and configured development sources retain their existing loaders.
Installed discovery uses the installation manager's selected versions and prepared
modules. A broken factory fails closed without replacing the working selection.


## Frontend contract and shared React

Export the existing `FrontendPlugin` with `apiVersion: 1` and named views using
`PluginViewProps`. Publish view references on `NodeTypeDefinition.frontend` as
usual. Build with `buildPackFrontend` from `@oaw/plugin-api/build`:

```js
import { buildPackFrontend } from '@oaw/plugin-api/build';
await buildPackFrontend({ entryPoint: 'frontend/index.tsx', outfile: 'dist/frontend/index.js' });
```

This is a bundled ES module with a small host ABI. Imports from `react`,
`react/jsx-runtime`, supported `react-dom` exports and `@oaw/plugin-api` refer to
the objects supplied at `Symbol.for('oaw.frontend.host.v1')`. The host publishes
its actual React, JSX, ReactDOM and SDK instances before importing a Pack.
The Pack does not ship another React renderer or SDK/context/store implementation.
Unsupported React subpaths fail at build time. Other UI dependencies can be
bundled; do not vendor an extra copy of React under a different name.

The catalog exposes `frontend_modules[owner]` with version, API, optional
`distribution_id` and an immutable
version URL, for example:
`/api/packs/example.greeter/versions/0.1.0/frontend/index.js`.
The existing registry continues to lazy-load bundled views with Vite's build-time
glob and additionally imports installed URLs at runtime. Lazy components are
cached by owner, view and version. The existing surface error boundary contains
missing modules, incompatible exports and rendering failures to the affected view.

Only the version loaded by the current backend is served; selecting an upgrade
does not mix a new frontend with an old backend. Only checksum-listed `frontend/`
and `assets/` files are public, never wheels or metadata. Relative URLs may load
additional assets from those directories. The SDK helper loads imported CSS
before the module finishes loading, resolves `?url` imports relative to the
module, and can build named worker entries. `frontend-build.json` records every
generated file and digest; preserve all of them when packaging. Browser workers
cannot import the window's React/SDK instances. See the [SDK build contract](../frontend/pack-sdk/README.md)
for worker and custom compiler examples. No host frontend rebuild is required.

The SDK tarball is built locally from the existing SDK declarations:

```sh
cd frontend
npm run build:pack-sdk
npm pack ./pack-sdk --pack-destination ../.outputs
```

CI also uploads this versioned tarball, identified by commit, for independent
projects. A public npm release workflow is not yet provided.


## Storage and lifecycle

```text
<data_root>/packs/
  installations.sqlite3          # installed versions, digests, selected versions
  staging/<unique-operation>/    # validated files before publication
  installed/<id>/<version>/      # immutable contents
```

Application installation files remain separate. Normal application upgrades do
not replace user data. Storage relocation copies Pack versions and checkpoints
the installation database along with the existing databases.

**Inspect → plan → prepare required host environments → stage → select → activate or restart → prepare Sandbox when used**

New content Packs and verified pure declarative Packs activate in the running
host immediately. Their catalog and frontend use the existing Library event and
dynamic module loader. Pure declarative adapter bytes must exactly match the host
compiler's output; a claimed manifest label cannot make arbitrary Python safe to
load live. Frontend-only cards use this declaration path. Other code Packs, mixed
Python contributions, and changes to already loaded owners remain pending restart.
No background Agent or Sandbox work is interrupted automatically.

The installer verifies all metadata, both archive layers, checksums, ownership,
Pack dependencies and obvious direct Python conflicts before selection. Files
are staged and renamed into the final version directory on the same filesystem;
a SQLite transaction atomically switches the desired version. Concurrent installs
cannot overwrite a version. Reinstalling an existing ID/version is rejected, even
if it contains identical bytes. A failed check leaves the current selection intact.

### Startup and explicit integrity checks

Full archive and file verification belongs to installation, updates, and explicit
diagnostics. Schema 3 backend modules are materialized before the installed version
is selected. The installer records the validated factory and Python requirements
in the same SQLite transaction as the version selection.

Ordinary startup reads that record, checks compatibility, dependency ownership,
and the top-level import paths. It does not unpack dependency wheels, walk their
file trees, rehash every installed file, or delete generated bytecode. A missing
prepared module is an error; startup does not reinstall it. Older installations
without a startup record undergo one full validation and migration, reported in
the startup log. Subsequent starts use the record. Source adapter installation
receipts already provide the required metadata and need no migration.

Installed code is trusted operator-managed software, like the host's Python
environment. These lightweight checks do **not** detect every edit to installed
code. Protect application and dependency directories from runtime users; hosted
deployments use a read-only application image, isolated profile volumes, and deny
Pack management to ordinary users. A file timestamp or size is not treated as a
security proof. Password hashing, authentication, and profile isolation are
independent of this installation lifecycle.

To explicitly verify the selected Pack archives, their materialized modules,
and prepared source adapter dependencies, use the backend interpreter:

```sh
python -m open_agent_world.pack verify --data-root /path/to/oaw-data
```

For this checkout's default development profile on Windows:

```powershell
.\backend\.venv\Scripts\python.exe -m open_agent_world.pack verify --data-root .open-agent-world/development/profiles/default
```

Use `--plugin-path /path/to/additional-plugins` for extra source plugin directories.
The command exits nonzero on failure, imports no plugin factories, and does not
install or repair code. It leaves bytecode caches intact. Editable dependencies
provided directly by the host have no managed archive to verify. Frontend assets
continue to be checked against their installation checksum when served.

### Version selection and recovery

The Library shows the same installation plan for local files and Store versions,
including missing Pack dependencies, environment targets and blockers. Source
Packs also expose their declared environments in Pack details. A loaded Pack and
an unprepared Sandbox environment are separate states.
Missing required Packs are blockers: the Store does not yet recursively fetch
and install them. External CLIs, services and system packages should be declared
as prerequisites when no supported environment provider can prepare them.

Install operation IDs and progress are persisted before environment preparation.
The Library polls those records, including after returning to the panel; an HTTP
timeout is not proof that installation stopped. It does not submit another install
while the operation remains active. A failed environment keeps its diagnostic
separate from the selected/loaded version. Read-only inventory and plan requests
do not start installation. Multi-Pack automatic acquisition and arbitrary
provider recovery are not implied by these records.

Upgrades retain old versions and select the new one for the next process startup.
**Use this version on restart** selects a retained version for rollback using the
same validation. Deactivation also waits for restart; loaded Python objects are
never hot-unloaded. Uninstall requires removing world objects, relationships,
dependent runtime usages and pending lifecycle cleanup first. It retains the
version files and the existing collection/deck references. An inactive version
can be removed only when it is neither selected nor loaded by this process.
The uninstall preview lists dependency resources that will be removed, retained
for another Pack, or released after restart. A retained, unselected archive does
not pin an unused environment; activating it prepares the required dependencies
again. Source Packs use the same uninstall operation to remove their profile
enrollment, leaving the checkout intact. **Restore Pack** enrolls the source
again. Shared managed resources are removed only after their final reference is
released; external resources and user data are retained.
Desktop application bundles can supply the same verified adapter wheels from a
read-only installation directory. These shipped libraries are preserved on
uninstall and never enter the profile's deletion ledger. Explicit source restore
can copy their verified wheel bytes into the managed profile without downloads.

Uninstall also persists the existing disable setting immediately, preventing new
world instances before restart. Explicitly reinstalling or restoring an unselected
Pack reenables it. Switching versions of an already selected Pack preserves its
existing enable setting.
Identity digests remain after file removal: the same ID/version can be restored
only with its original content. A changed artifact needs a new version.

Persisted cards and connections whose plugin is absent, disabled, no longer
provides their type, or no longer matches their recorded owner load as **MISSING**
placeholders. The canvas shows a black and purple pattern and retains their
original identifiers, configuration, layout, and connections. Missing cards can
be moved, renamed, resized, detached, or deleted; unavailable connections can be
deleted. Their lifecycle callbacks and connection grants remain inactive.
Restoring the matching plugin and restarting makes retained objects usable again.
Deleting missing objects currently clears canvas undo history because restoring
them requires their plugin implementation.

Import, registration and safely compensated optional startup failures leave the
owner Unavailable while independent plugins continue. Use the failure phase and
diagnostic in the Library, repair/select a compatible version and restart. Unsafe
cleanup or persistence errors can still prevent startup. Offline recovery uses
the host interpreter:

```sh
python -m open_agent_world.pack activate --data-root /path/to/oaw-data example.greeter 0.2.0
```

The recovery command loads bundled definitions to protect their identities but
does not import the broken selected external version. Start OAW again afterwards.
There is no automatic rollback, publisher authentication or version migration
framework in this release; authors must preserve their persisted-data contracts.


## Legacy shared Sandbox Python

`runtime.sandbox.python` maps to the existing registry runtime requirements and
`PluginEnvironmentBootstrap`. The host keeps one shared environment per execution
platform, continuing to use platform preparation, host-managed `uv`, durable
receipts, progress logs and the existing cross-process mutation lock.

All enabled owners are aggregated into one request. The offline check catches
obvious incompatible pins and ranges (such as `numpy<2` and `numpy>=2`) before
selecting a new Pack or enabling one. Full transitive/platform resolution happens
with [`uv pip install --dry-run`](https://docs.astral.sh/uv/reference/cli/#uv-pip-install)
before installing the same complete requirement set under the same lock. A
resolution failure leaves working packages unchanged. Agent-requested Python
installs also include all enabled Pack requirements, so they cannot silently
replace a package with a version that violates a Pack declaration.

Only index package names, extras and versions are accepted. No URLs, git/local
paths, installer options, source builds or arbitrary post-install commands are
accepted. This compatibility profile does not create per-Pack venvs. Existing
declarations appear as `management="legacy-shared"`; new Packs should declare
managed environments so their dependencies participate in reference-based cleanup.

Installation and environment preparation are independent. A downloaded local
Pack remains **Installed** when the environment is **Preparing** or **Failed**.
Preparation begins after restart/discovery. Retry uses the existing bootstrap;
it does not reinstall/delete the Pack. All owners participating in an aggregate
request receive its result, with per-platform receipts. Index/network failures
can be retried. Install-time direct checks do not claim to solve transitive or
platform-specific conflicts offline; those appear as environment failures before
the shared environment is changed.


## Build and acceptance

Follow [Build and install a Greeter Card App](developers/card-app.md) for commands
that build a wheel and frontend, package them and verify an installed host.
`doctor` is static inspection; `check --lifecycle` executes trusted plugin code
in a disposable profile. Neither alone proves browser or native runtime behavior.
Use the [existing acceptance runners](developers/testing.md) when changing packaging.

## Store acquisition

The **Store** tab now supports remote catalog search, pagination, Pack details,
explicit Get/Update and installed/restart state. The local backend downloads the
selected Marketplace version and passes its verified `.oawpack` bytes to this
same installer. Local **Install Pack from File...** remains available.

See [Store protocol](pack-store.md) for the HTTP boundary, integrity checks,
development configuration and offline/private Release acceptance commands.

Accounts, publishing, ratings/moderation, paid Packs, automatic updates,
signatures and publisher identity remain deferred. New runtime declarations
are supported only when an actual installation/execution provider implements them.
