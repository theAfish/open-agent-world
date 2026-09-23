# Local Pack distribution

OAW's user model is **Pack → Card Library → Deck → World**. The Store tab remains
a placeholder for a future online catalog. A `.oawpack` is the supported local
installation unit for a third-party Pack, including its backend and frontend.

`PluginRegistry`, `PluginDescriptor`, `FrontendPlugin` and runtime owner are
internal implementation concepts. Installed and bundled Packs use the existing
registry, collection, enable/disable controls, lifecycle checks and deck storage.
Bundled plugins may still own several Packs. An external distribution owns
exactly one Pack, with the same ID as its runtime owner.

## Version 1 archive

A `.oawpack` is a ZIP with these files at the archive root (no wrapper directory):

```text
manifest.json
checksums.json
backend/oaw_pack_example_greeter-0.1.0-py3-none-any.whl
frontend/index.js
assets/                 # optional
README.md               # optional
```

```json
{
  "schema_version": 1,
  "id": "example.greeter",
  "name": "Greeter",
  "version": "0.1.0",
  "compatibility": { "oaw": ">=0.1.0,<0.2", "plugin_api": "1.23", "frontend_api": 1 },
  "dependencies": { "packs": [] },
  "runtime": { "sandbox": { "python": ["colorama==0.4.6"] } },
  "entrypoints": {
    "backend": "backend/oaw_pack_example_greeter-0.1.0-py3-none-any.whl",
    "frontend": "frontend/index.js"
  }
}
```

The validation model is `backend.packs.manifest.PackManifest`. Generate its JSON
Schema with `python -m open_agent_world.pack schema`. Metadata inspection does
not import or execute the backend wheel or frontend module.

- External IDs contain lowercase alphanumeric dot-separated names, for example
  `publisher.greeter`. IDs and canonical PEP 440 versions are stable identities.
- OAW uses a PEP 440 version specifier. Plugin API compatibility uses the existing
  same-major, host-minor-at-least-requested rule. Frontend API must be `1`.
- A Pack dependency is `{ "id": "oaw.tasks.default", "version": ">=0.1" }`.
  Bundled Pack versions are their owning runtime versions. Missing, disabled,
  incompatible and cyclic dependencies are rejected when selecting versions.
- Unknown fields, including unsupported non-Python runtimes and installation
  hooks, are rejected. A future schema version can add new runtime kinds.
- `checksums.json` maps every file except itself to its lowercase SHA-256 digest,
  including `manifest.json`. The installation database retains these digests;
  editing both an installed asset and its neighboring checksum file is not enough
  to change accepted content.
- Archives are limited to 128 MiB compressed, 256 MiB expanded per ZIP layer and
  4096 entries per layer. Paths are canonical relative paths on Windows/POSIX.
  Traversal, symlinks, special files, encrypted entries, duplicate/case-colliding
  members, ambiguous JSON keys and Windows device/stream paths are rejected.

Checksums detect corruption and unexpected modification. They are **not publisher
authentication**. Packs run as trusted application code in the host backend and
browser; this format does not provide OS-level isolation for Pack code.

## Backend contract

Version 1 accepts one pure-Python `py3-none-any` wheel. Its only Python package is
`oaw_pack_` plus the distribution ID with dots replaced by underscores, e.g.
`oaw_pack_example_greeter`. The other root is its single `.dist-info` directory.
It must declare exactly one factory:

```toml
[project.entry-points."open_agent_world.plugins"]
"example.greeter" = "oaw_pack_example_greeter:create_plugin"
```

The wheel is imported directly from its immutable version path using Python's
ZIP importer. It is not pip-installed into OAW's interpreter. Backend dependency
metadata must be satisfied by the host; private pure-Python helpers can be
included under the Pack's own module. Native backend wheels, `.pth` hooks and
backend dependency URLs are unsupported in v1. Use `importlib.resources` for
wheel resources, rather than treating `__file__` as an extracted directory.

The factory returns the existing plugin interface and imports only the public
`open_agent_world.plugin_api` contract. Its descriptor ID, version, Plugin API and
dependency owners must agree with the manifest. Sandbox requirements come from
the manifest; a nonempty descriptor declaration must agree. Registration must
declare exactly one `PackDefinition` with the distribution ID, and cards must
belong to that owner's namespace. Existing registry validation remains atomic.

Core/bundled packages and configured development paths retain their current
loader. Installed Pack discovery follows from `<data_root>/packs` at startup,
with metadata dependency ordering and duplicate ownership checks. Loading a
broken backend fails closed with the Pack ID/version; no hot reload is attempted.

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

The catalog exposes `frontend_modules[owner]` with version, API and an immutable
version URL, for example:
`/api/packs/example.greeter/versions/0.1.0/frontend/index.js`.
The existing registry continues to lazy-load bundled views with Vite's build-time
glob and additionally imports installed URLs at runtime. Lazy components are
cached by owner, view and version. The existing surface error boundary contains
missing modules, incompatible exports and rendering failures to the affected view.

Only the version loaded by the current backend is served; selecting an upgrade
does not mix a new frontend with an old backend. Only checksum-listed `frontend/`
and `assets/` files are public, never wheels or metadata. Relative URLs may load
additional assets from those directories. Include a relative stylesheet link in
your view if you use a separate CSS output. No host frontend rebuild is required.

The SDK tarball is built locally from the existing SDK declarations:

```sh
cd frontend
npm run build:pack-sdk
npm pack ./pack-sdk --pack-destination ../.outputs
```

This is a local developer artifact, not a new public npm release workflow.

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

**Inspect → validate → stage → select → restart → discover → prepare environment**

The installer verifies all metadata, both archive layers, checksums, ownership,
Pack dependencies and obvious direct Python conflicts before selection. Files
are staged and renamed into the final version directory on the same filesystem;
a SQLite transaction atomically switches the desired version. Concurrent installs
cannot overwrite a version. Reinstalling an existing ID/version is rejected, even
if it contains identical bytes. A failed check leaves the current selection intact.

Upgrades retain old versions and select the new one for the next process startup.
**Use this version on restart** selects a retained version for rollback using the
same validation. Deactivation also waits for restart; loaded Python objects are
never hot-unloaded. Uninstall requires removing world objects, relationships,
dependent runtime usages and pending lifecycle cleanup first. It retains the
version files and the existing collection/deck references. An inactive version
can be removed only when it is neither selected nor loaded by this process.
Uninstall also persists the existing disable setting immediately, preventing new
world instances before restart. Reactivating a retained version preserves that
preference; enable the Pack in the Library when you want to use it again.
Identity digests remain after file removal: the same ID/version can be restored
only with its original content. A changed artifact needs a new version.

If a selected backend prevents startup, stop OAW and use the host interpreter:

```sh
python -m open_agent_world.pack activate --data-root /path/to/oaw-data example.greeter 0.1.0
```

The recovery command loads bundled definitions to protect their identities but
does not import the broken selected external version. Start OAW again afterwards.
There is no automatic rollback, publisher authentication or version migration
framework in this release; authors must preserve their persisted-data contracts.

## Shared Sandbox Python

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
accepted. This is not a new environment manager and does not create per-Pack venvs.

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
own Python project, npm project, manifest and frontend. Copy it outside the host
repository, install the SDK tarball and build there. Package prebuilt files with:

```sh
python -m open_agent_world.pack build /external/greeter/dist /artifacts/greeter-0.1.0.oawpack
python -m open_agent_world.pack inspect /artifacts/greeter-0.1.0.oawpack
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
production launcher, installs through the UI, stops/restarts the process, waits
for real Shared Python provisioning, and exercises collection, deck placement,
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
signatures, publisher identity and additional runtime kinds remain deferred.
