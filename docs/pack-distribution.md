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
Schema 2 remains temporarily supported for code-free content creation. Schema 1
is unsupported and rejected; there is no compatibility loader or automatic migration.

## Source declaration and schema 3

Passive cards can use [declarative contributions](developers/declarative-packs.md)
without Python registration code. `build-project` generates their adapter wheel;
advanced and mixed plugins continue to pass `--backend-wheel`. Both use the same installer.

New source projects declare installation intent in `oaw.pack.toml`. The source
reader does not import the Pack or run setup hooks. Python dependencies remain
in native `pyproject.toml`/requirements files and frontend dependencies remain
in the Pack's own `frontend/package.json` and lockfile.

Setup defaults to Core. Pass `--with-plugins` to prepare all official plugins or
`--plugin-path` for selected projects. Schema 3 source loading does not require
editable installation. The setup scripts prepare dependencies in the same `--profile`
that the backend will use. Development startup never installs dependencies. For manual preparation,
run `scripts/install-plugins.py --profile my-dev plugins/my_pack` with the backend
interpreter. Without `--profile`, preparation uses `OPEN_AGENT_WORLD_DATA_ROOT` or
the standard application data root. Uninstalled source Packs stay uninstalled
until explicitly restored; restarting development does not prepare them again.

```toml
schema_version = 3

[distribution]
id = "example.research"
name = "Research"
# Version defaults to project.version in pyproject.toml.

[compatibility]
plugin_api = "1.35"
oaw = ">=0.1.0,<1"

[plugin]
owner = "example.research"
packs = ["example.research.default"]
node_types = ["example.research.analysis"]

[components.backend]
project = "pyproject.toml"
mode = "host-adapter"

[components.frontend]
project = "frontend/package.json"
entry = "frontend/index.tsx"
mode = "pack-build"

[dependency-groups.compute]
sources = [{ path = "requirements/compute.txt", kind = "requirements" }]

[environments.engine]
target = "host-worker"
provider = "python"
include = ["compute"]
prepare = "install"
verify_imports = ["numpy"]

[environments.analysis]
target = "sandbox"
provider = "python"
include = ["compute"]
python = ["ase>=3.22"]
prepare = "on-use"

[features.research]
environments = ["engine", "analysis"]
```

`plugin.node_types` declares the exact card types owned by the Pack. Source and
archive registration check this list before publishing contributions, so existing
card IDs can survive a distribution rename. Core card types cannot be claimed;
they may be included in a Pack collection without changing their ownership.
Omitting this list retains the owner namespace rule for archive card types.

Put `numpy` and its version constraint once in `requirements/compute.txt`. Both
environments consume that declaration; `ase` is a Sandbox-only addition. Source
references can also select a project's runtime dependencies (`kind="project"`),
an optional extra (`kind="optional", group="analysis"`) or a Python dependency
group (`kind="group", group="analysis"`). Named groups use `include` to compose
other groups. Cycles and missing references are rejected, and diagnostics retain
the origin of each requirement.

Sandbox does **not** inherit OAW's host packages, all installed Packs, development
dependencies, or the developer's current environment. Reusing a declaration does
not copy installed packages: each execution target prepares its own environment.
For example, a Sandbox-only CLI can be recorded in a separate `provider="node"`
environment with `node=["package-name@version"]`. Declaring it does not imply
that a provider supports installing it. Unsupported providers and lockfile
installation formats are reported explicitly. Host worker providers prepare Python
environments and verified executable artifacts. Arbitrary host npm or system-package
installation commands are outside the declaration contract.

Host adapters execute in OAW's Python process through its public Plugin API.
Their dependencies use an explicitly shared, version-compatible library layer:
mandatory host packages are reused, and additional libraries ship as hash-locked
wheels under `backend/dependencies/`. Installation verifies and extracts those
wheels into `<data_root>/packs/adapter-wheels/<sha256>`, without modifying host
site-packages. Conflicting versions, hashes and module ownership fail before
activation. On a host that already enables OAW's declared `hosting`, `adk`, or
`litellm` extras, their exact-version dependency closure is explicitly classified
as **Provided by OAW**. Its installed file ownership, module origins, versions,
and direct-source provenance are checked; OAW retains responsibility for those
host files. The Pack still ships its locked wheels as a fallback for clean hosts.
This exception does not apply to unrelated packages in `site-packages`, which
must match the locked wheel bytes. Host-provided libraries survive Pack uninstall.
This shared namespace is not process isolation; incompatible engines
belong in a separate host worker. AtomSculptor and Card Factory use public SDK
facets. Frontend libraries are bundled by the author; end users do not run npm. All repository
Packs with a frontend now own a package manifest, lockfile and independent build.
They import host contracts through `@oaw/plugin-api`. XRD obtains the Structure
Viewer's renderer through a named frontend service, without importing its source
or bundling its scientific viewer dependencies a second time.

Run `npm run dev` or `npm run build` in `frontend/`. Setup/production build explicitly
prepares dependencies; ordinary dev only checks them and executes each changed
Pack's own `npm run build`. Development
serves the resulting `dist` artifact; production copies the same artifact into
the host assets. Source edits rebuild the affected Pack and reload the page.
Adding a new source Pack requires restarting Vite. An existing checkout may
reuse exact installed lockfile versions through project-local cache links;
otherwise preparation runs `npm ci --ignore-scripts` in that Pack. Build scripts
run explicitly, and dependency lifecycle scripts do not run automatically.
Root dependencies retain libraries actually used by host UI, including Three
for Pack artwork; MatterViz, Svelte and PDF.js belong to their Pack projects.

Inspect a source project or the entire source directory without installing:

```sh
python -m open_agent_world.pack doctor plugins/atomsculptor
python -m open_agent_world.pack doctor plugins --data-root /path/to/oaw-data
```

`doctor` reports identities, expanded environments, requirement origins, host
compatibility and available environment receipts. A declaration alone is not a
readiness check for a particular Sandbox, model connection or credential.

To use a managed Sandbox environment, bind it to the executable Skill in its
Skill package. The owner identifies the runtime plugin, not its distribution:

```json
{
  "id": "analyse-structure",
  "runtime_requirements": [
    { "owner": "example.research", "environment": "analysis" }
  ]
}
```

This field is part of the normal Skill entry alongside its instructions and
files. When `run_skill_script` executes that Skill, OAW checks the live Pack,
Skill and Sandbox grants, composes its declared environments, and prepares and
runs it inside the same authorized Sandbox command. Python packages use a private
environment without host site packages; npm packages use a private prefix. The
Sandbox's network policy and command timeout still apply. A failed preparation
does not run the Skill, and an interrupted attempt can reuse verified receipts.

Runtime providers can also select default profiles on their Pack descriptor:

```python
PluginDescriptor(
    id="example.research", version="1.0.0", plugin_api_version="1.35",
    sandbox_environments=("analysis",),
)
```

These names reference the owning Pack's declared Sandbox environments. Ordinary
commands from that runtime and its Skill scripts prepare those profiles on use;
Skill-specific requirements compose into the same private environment. The host
resolves the current invoking Run's provider and rechecks the live Pack and
Sandbox grants before dispatch. Direct `python`/`python3` commands preserve
working-directory scripts, `-c`, `-m` and literal arguments. Shell commands inherit
the private environment's `PATH`; explicit paths inside shell text retain normal
shell semantics. Direct custom Python interpreter paths/versions and `-S` are
rejected because they bypass the selected environment. `python_environment=none`
cannot be combined with declared profiles. AtomSculptor uses this mechanism for
its modelling commands instead of adding libraries to shared Sandbox Python.

Durable job providers can declare their own `sandbox_environments=("bohr",)`
on `BackgroundJobProvider`. The host binds these profiles when the provider calls
`context.execute`, using the persisted job's provider owner, Agent and Sandbox.
Submission, later monitoring and cancellation use the same declaration even
outside the submitting Run. Live job/session permissions and Pack availability
are rechecked after preparation waits. These provider profiles are independent
of the Agent runtime's defaults; they do not grant additional Sandbox access or
change submission deduplication and uncertain-result reconciliation.

Skill binding accepts Python, Node, `sh` or `bash` interpreters without custom
flags. Sandbox preparation must use
`prepare="on-use"`. Python dependencies accept index package names and version
constraints, without URLs or markers; npm accepts registry names with optional
versions. Node/npm must already exist in the Sandbox. Lockfiles, extra runtime
resources, verification scripts and external prerequisite orchestration are not
implemented by this Sandbox provider and are reported as unsupported. Sandbox
caches remain writable by that Sandbox; they are not a security boundary.

Model credentials, licensed files, GPUs and services belong in `prerequisites`
and feature references. Executable environments can declare HTTPS or Pack-local
artifacts with SHA-256, platform, archive format and executable path, or explicitly
allow an existing verified executable. External executables remain user-owned.
A declaration alone does not prove a configured service is usable.
Legacy imported scientific Skills without `runtime_requirements` retain their
explicit preparation instructions until migrated.

Build the adapter wheel and frontend, resolve adapter libraries in a private
wheelhouse, then create a schema 3 archive:

```sh
uv build /path/to/research --wheel --out-dir /path/to/research/dist
npm --prefix /path/to/research/frontend ci
npm --prefix /path/to/research/frontend run build
python -m open_agent_world.pack resolve-adapter /path/to/research /artifacts/research-wheels
python -m open_agent_world.pack build-project /path/to/research /artifacts/research-1.0.0.oawpack \
  --backend-wheel /path/to/research/dist/adapter-1.0.0-py3-none-any.whl \
  --frontend-dir /path/to/research/frontend/dist \
  --dependency-wheels /artifacts/research-wheels
```

This packages prebuilt artifacts, compiles the source declaration, checks owned
module roots and wheel metadata, and includes declared runtime resources. It
does not run arbitrary build or setup commands. Declare adapter package roots in
`tool.hatch.build.targets.wheel.packages`; host-private `backend.*` imports must
be migrated before release. The final archive is checked by the same validator
used for installation. The old raw-directory `build` command has been removed;
author code-capable Packs with `build-project`. Schema 2's existing content
exporter remains code-free while future visual authoring is designed separately.

`resolve-adapter` is an explicit author/setup operation. It uses isolated build
tools, pins the current shared host versions, and builds pinned HTTPS Git sources
into wheels. End-user archive installation performs no Git clone or build hook.
Standalone console entry points are omitted from the adapter artifact while
their modules remain available. Packs without custom frontend declarations can
omit `--frontend-dir`; the builder emits an empty frontend contribution.

Native dependency wheels bind an archive to their Python ABI and platform tags.
Build and smoke-test on each supported target. The current Store version points
to one artifact and does not automatically select platform variants; incompatible
hosts reject that artifact before preparation. A pure-Python adapter wheel does
not make its native dependencies portable across platforms.

Author builds validate delivered wheel bytes and dependency closure. The
`inspect` and installation commands additionally check libraries already present
in the target host. Outside the explicitly classified host-provided closure,
even a same-version library with different code or resources cannot shadow the
locked artifact. Validate a release in a clean host
profile/interpreter; an altered development library can legitimately block local
installation without changing the published archive.

Source setup uses the same adapter preparation and environment providers and
records managed dependencies under the selected OAW profile. Schema 3 source
discovery needs no editable install; desktop assembly installs the adapter code
with `--no-deps`. Setup hooks are unsupported. Normal discovery never downloads packages. A
developer's already installed compatible libraries remain usable, with exact
provenance checks for pinned direct references.

Host worker preparation reuses a matching application-bundle environment before
downloading dependencies. It also migrates pre-unification OAW Python workers
from `oaw-runtimes/<owner>/*` when their v2 receipt, interpreter, complete named
dependency closure and runtime checks match the current declaration. Migration
copies the verified environment into the selected profile; the original remains
untouched. Incompatible or unverifiable legacy environments are not adopted.
Host site-packages are never inherited by these workers. uv's configured download
cache is shared with normal setup, while installed libraries remain profile-owned.
Source setup prints each preparation stage, elapsed time and a periodic heartbeat.

Removal previews show dependent Packs and shared resources. Uninstall changes
the profile's selection/enrollment; it preserves source files, retained archives,
world data and collection/deck intent. A shared resource is removed only after
its last selected/source consumer and active process lease are gone. Loaded
libraries remain pinned until restart. Sandbox profiles are collected at the
next admitted profile command. `uninstall-plan` and `gc` are available in the
local CLI; removal itself uses the running host's world and active-work checks.

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
above. Factories use `open_agent_world.plugin_api`, return `PluginDefinition`,
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

The [external Greeter fixture](../examples/packs/greeter/README.md) contains its
own Python project, npm project, `oaw.pack.toml` and frontend. Copy it outside
the host repository, install the SDK tarball and build there. Package with:

```sh
python -m open_agent_world.pack build-project /external/greeter /artifacts/greeter-0.2.0.oawpack \
  --backend-wheel /external/greeter/dist/backend/oaw_pack_example_greeter-0.2.0-py3-none-any.whl \
  --frontend-dir /external/greeter/dist/frontend
python -m open_agent_world.pack inspect /artifacts/greeter-0.2.0.oawpack
```

In OAW, open **Pack & Card Library → Packs → Install Pack from File...**. Review
the name/version and trusted-code notice, install, and restart OAW. Once its
environment is ready, open the Pack, add Greeter to the active deck, place the
card and use **Greet**. The saved greeting survives reloading the browser.

For repeatable host acceptance, build the host frontend and SDK tarball first,
then from the root run `python scripts/build-external-greeter.py`; from `frontend`
run `npm run test:e2e:packs`. The build helper copies the fixture to an OS temporary
directory outside the checkout and builds from public dependencies. The runner
uses a fresh OAW data directory, serves the already-built frontend through the
production launcher, installs through the UI, stops/restarts the process,
and exercises collection, deck placement,
view loading and persisted interaction. Results are under `.outputs/pack-acceptance`.
This is production-host acceptance, not an installer rebuild or all-platform test.

Contract/backend tests live in `backend/tests/test_pack_installation.py`; shared
environment and frontend failure-isolation tests remain beside their existing
host suites. The browser acceptance belongs in `frontend/e2e` because it verifies
cross-cutting host installation, restart, library and deck behavior.

## Store acquisition

The **Store** tab now supports remote catalog search, pagination, Pack details,
explicit Get/Update and installed/restart state. The local backend downloads the
selected Marketplace version and passes its verified `.oawpack` bytes to this
same installer. Local **Install Pack from File...** remains available.

See [Pack Store Client V0](pack-store.md) for the HTTP boundary, integrity checks,
development configuration and offline/private Release acceptance commands.

Accounts, publishing, ratings/moderation, paid Packs, automatic updates,
signatures and publisher identity remain deferred. New runtime declarations
are supported only when an actual installation/execution provider implements them.
