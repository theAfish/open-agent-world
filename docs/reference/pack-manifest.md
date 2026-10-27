# Pack source manifest

The source format is `oaw.pack.toml`, Schema 3. [Build a Card App](../developers/card-app.md)
for the complete authoring workflow; [distribution](../pack-distribution.md) defines the archive.

## Source declaration and schema 3

Passive cards can use [declarative contributions](../developers/declarative-packs.md)
without Python registration code. `build-project` generates their adapter wheel;
advanced and mixed plugins continue to pass `--backend-wheel`. Both use the same installer.

New source projects declare installation intent in `oaw.pack.toml`. The source
reader does not import the Pack or run setup hooks. Python dependencies remain
in native `pyproject.toml`/requirements files and frontend dependencies remain
in the Pack's own `frontend/package.json` and lockfile.

`distribution.presentation` may name a Pack-relative JSON `PackDefinition` for
one of `plugin.packs`. Reuse that file when registering the runtime Pack so its
appearance has one authority. The builder embeds it as `manifest.presentation`;
Marketplace previews read its packaging, accent color and card count without
executing Pack code or loading arbitrary artwork URLs. Codex demonstrates this
with `src/oaw_codex/pack.json` and premium packaging.

Default setup prepares Core, bundled Packs and optional runtime SDKs. Use
`--core-only` for Core alone, or `--plugin-path` for selected projects. Schema 3 source loading does not require
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
Packs with a frontend own a package manifest, lockfile and independent build.
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
used for installation. Use `build-project` for code-capable Packs; the retired
raw-directory `build` command is unsupported. Schema 2 uses the separate
[content Pack exporter](../creator-packs.md).

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
