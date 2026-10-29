# Declarative Notes Pack

Copy this directory anywhere outside OAW. It has no Python plugin module,
manual registration or imports from the host frontend. Its distribution ID is
also its default plugin owner and single card ID; its Pack ID defaults to
`example.notes.default`. Version comes only from `pyproject.toml`.

Use an OAW checkout/interpreter that supports Plugin API 1.37. Define `OAW_PYTHON`
as that interpreter and `OAW_PACK` as the absolute path to `scripts/oaw-pack.py`.
The wrapper invokes the same `python -m open_agent_world.pack` CLI and keeps your
working directory. `pack init /path/to/my-notes` creates a new project with a
directory-based local ID plus a random suffix, matching Python/npm project names,
and updated commands in its README. A manual copy keeps the example identity;
change it and both project names before creating independent releases. Keep the
chosen identity stable across versions. Download the versioned SDK artifact from a successful OAW CI
run and pin its tarball in your lockfile. SDK tarballs can also be built with
`npm --prefix <OAW>/frontend run build:pack-sdk` followed by
`npm pack <OAW>/frontend/pack-sdk --pack-destination <output>`.

From this external project directory (POSIX syntax shown):

```bash
npm install /absolute/path/oaw-plugin-api-1.0.0.tgz
npm run build
"$OAW_PYTHON" "$OAW_PACK" doctor .
"$OAW_PYTHON" "$OAW_PACK" check . --lifecycle --card example.notes
"$OAW_PYTHON" "$OAW_PACK" build-project . dist/notes-0.1.0.oawpack --frontend-dir dist/frontend
"$OAW_PYTHON" "$OAW_PACK" inspect dist/notes-0.1.0.oawpack
"$OAW_PYTHON" "$OAW_PACK" install dist/notes-0.1.0.oawpack --data-root /absolute/path/test-profile
```

Start OAW with that offline test profile, open Notes in the Pack & Card Library, add a
Note, edit its text and reload. No host frontend rebuild is needed. Use the
Library installer instead of CLI installation when the profile is already open:
the first installation activates immediately in the existing page and backend.
Updates to an already loaded Pack require backend restart.
For a new release, increment `project.version`, rebuild, inspect and install.

For backend source iteration, set `OPEN_AGENT_WORLD_PLUGIN_DIRS` to this project's
root or parent folder and restart the host. The loader discovers `oaw.pack.toml` without
an editable Python install. To test the independent frontend, install a built
archive in a dedicated profile; frontend hot reload for external source folders
is not provided by this example. Avoid loading the source and installed archive with
the same identity in one profile.

Every config property needs a valid default for palette creation. The initial
schema subset supports objects, arrays, strings, numbers, integers, booleans,
null, scalar enums and common bounds. Unknown keywords fail validation. Use a
Python config model and the advanced SDK for custom validators, lifecycle,
relationships, capabilities, documents, providers or cross-plugin dependencies.
API 1.38 can combine declarative cards with those advanced contributions in one
atomic registration; no second manifest or Registry is needed.

Python adapter dependencies remain in `project.dependencies`; named worker and
Sandbox environments use existing `[environments.<name>]` declarations. Prepare
them with the existing installer/`resolve-adapter`; they never imply an install
into the host interpreter. This sample needs neither.
