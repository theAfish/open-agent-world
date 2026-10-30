# OAW frontend Pack SDK

Use `FrontendPlugin` and `PluginViewProps` from `@oaw/plugin-api`.
Build the default `FrontendPlugin` export with `buildPackFrontendVite` from
`@oaw/plugin-api/build-vite`. The build replaces React, JSX, ReactDOM and runtime SDK
imports with references to the host instances. Do not bundle another React,
use absolute host source imports, or render a second application root.

The host loads the resulting ES module from the installed immutable version.
Relative assets may live in `frontend/` or `assets/` in the `.oawpack`.
Import CSS normally. The helper emits a stylesheet and loads it before the
module finishes loading. Failed stylesheet downloads reject the Pack import.
Import resources with `?url` to receive a URL relative to the installed module
(including prebuilt PDF workers). CSS images/fonts are emitted alongside it.
The generated `frontend-build.json` lists every output file with its size and
SHA-256; include **all** listed files in the archive, preserving paths.

The full project builder supports code splitting, CSS, relative URL assets,
native `new Worker(new URL('./parser.worker.ts', import.meta.url))` entries and
Vite compiler plugins. Source imports cannot escape the Pack project; third-party
imports must be declared in its package manifest. Worker code cannot import
window-only host APIs.

```js
// frontend/build.mjs
import { fileURLToPath } from 'node:url';
import { buildPackFrontendVite } from '@oaw/plugin-api/build-vite';
await buildPackFrontendVite({ root: fileURLToPath(new URL('.', import.meta.url)) });
```

The smaller esbuild helper remains available for simple React projects:

```js
import { buildPackFrontend } from '@oaw/plugin-api/build';
await buildPackFrontend({
  entryPoint: 'index.tsx', outfile: 'dist/index.js',
  workers: { analysis: 'analysis.worker.ts' },
});
// In the view:
new Worker(new URL('./workers/analysis.js', import.meta.url), { type: 'module' });
```

Workers run outside the window and cannot import React or the host SDK.
Additional esbuild compiler plugins can be passed as `plugins`; Svelte sources
require a Svelte compiler plugin supplied by their Pack. The default helper is
for React/TypeScript, not a replacement for every framework build tool.

Each independently built Pack owns a `frontend/package.json` and lockfile.
Declare bundled libraries in `dependencies`, the SDK in `devDependencies`, and
React/ReactDOM as peers. For local SDK development use
`"@oaw/plugin-api": "file:../../../frontend/pack-sdk"`; external projects should
use a packed SDK tarball or published version. Run `npm run build:pack-sdk` in
the host before packaging the SDK. Its runtime export list is generated from
the public SDK source and checked by `npm run test:pack-sdk`. The host's development
server and release build run each source Pack's build script and load its output;
neither compiles Pack source through private host aliases. Source edits rebuild
the affected artifact and reload the page.
Pack build subprocesses use `NODE_ENV=production` even under the development
server, so generated JSX and dependency conditions match the release artifact.
The host retains its development mode; Pack build dependencies are installed normally.

Hosts with Plugin API 1.35 expose `useModelOptions`, `useConnectedCards`, `updateCardConfig`, `useReadingPreferences`
and `setReadingPreferences` expose narrow host presentation/mutation contracts.
They do not grant access to model credentials or bypass host authorization.
Shared UI includes `ModelSelect`, `DocumentMarkdown` and the Card Factory views.
Packs can expose named `FrontendPlugin.services` consumed through
`loadPackFrontendService(owner, name)`; declare the supplying Pack as a dependency.
For example, XRD uses `StructureRendererService` from Structure Viewer without
importing its source or bundling another copy of MatterViz.
Declare `plugin_api_version="1.35"` in the adapter and matching source compatibility
when using these helpers or the frontend service lookup.

Hosts with Plugin API 1.28 also export `ApplicationSurface`, `WorkspacePanel`,
`WorkspaceToolbar`, `WorkspaceEmptyState`, the existing `IconButton` and `Modal`,
and `composeWorkspace`. Declare Python `plugin_api_version="1.28"` when using
these helpers. They use host theme tokens and shared React, and remain optional.
See the [workspace design guide](../../docs/developers/workspace-design.md) for
content templates, optional card/section sizing and one-shot layout recipes.

Plugin API 1.41 adds `Tooltip` and `Popover` with host theme tokens, native top-layer
positioning and keyboard dismissal. They preserve trigger layout, refs and event
handlers. Use `Tooltip` for noninteractive hints and `Popover` for small interactive
panels. Declare the matching adapter/manifest minimum; see the workspace design
guide for examples and focus behavior.
