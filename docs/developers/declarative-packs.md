# Declarative cards

Use Schema 3 contributions for passive cards with saved configuration and an
optional independent UI. OAW derives the plugin owner, Pack and card metadata
and compiles them into the existing `PluginDefinition`, `NodeTypeDefinition`,
`PackDefinition` and `PluginRegistry`. No additional plugin manager is involved.

For a complete external project, run `pack init /path/to/my-notes`. It uses
[`examples/packs/notes`](https://github.com/theAfish/open-agent-world/tree/dev/examples/packs/notes)
and assigns a directory-based ID such as `local.my-notes-<random suffix>`, with
matching Python and npm project names. The suffix distinguishes separate projects
even when their directories have the same name. The generated README uses the new
identity. If copying the example manually, change `example.notes` and both project
names before installing it alongside another project. Keep the chosen identity
stable across releases.
For lifecycle callbacks, relationships, capabilities, documents, execution,
providers or custom configuration validators, keep using the
[advanced Python API](../plugins.md).

## Minimal project

`pyproject.toml`:

```toml
[project]
name = "example-note"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []
```

`oaw.pack.toml`:

```toml
schema_version = 3

[distribution]
id = "example.note"
name = "Note"

[[contributions.cards]]
config_schema = "config.schema.json"
```

`config.schema.json`:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "message": { "type": "string", "default": "Hello", "maxLength": 2000 }
  }
}
```

This creates owner/card `example.note` and Pack `example.note.default`, with
the host's standard configuration editor. No Python entrypoint, wheel build or
frontend is required. When more than one card is declared, give each an explicit
`id`. Give `[plugin]` explicit `owner`, `packs` and `node_types` only when
preserving identities that differ from the defaults. Existing advanced Schema 3
declarations also accept omitted owner/Pack/backend defaults.

Each card can set `name`, `description`, `icon`, `color`, `frontend`, `size`,
`default_name`, `statuses`, `default_status`, `deck_id` and `deck_name`.
`config_schema` accepts a relative JSON file or an inline table. Paths stay
inside the project. The initial config subset supports objects, arrays,
strings, integers, numbers, booleans, null, scalar enums, numeric bounds,
string lengths/patterns and array lengths. Every property needs a valid default
for palette creation; nested objects can derive defaults from their children.
Objects reject undeclared properties unless `additionalProperties=true`.
Unknown keywords fail explicitly rather than weakening validation.

Declarative cards require Plugin API 1.37, inferred when `[compatibility]` is
omitted. Pure declarations own one Pack and have no Python entrypoint or
cross-plugin dependency. Advanced plugins keep their existing compatible API
versions; mixing contributions and Python requires API 1.38.

## Mixed contributions (API 1.38)

Declare multiple cards with repeated `[[contributions.cards]]` tables and distinct
stable `id` values. Owner, version and the default Pack still come from the project
and distribution. Do not repeat `plugin.node_types`; if specified, it must describe
the entire declared and advanced contribution set.

For custom lifecycle, state, capabilities or relationships, keep the existing
`pyproject.toml` plugin entrypoint. Manifest cards populate the same registration
collector before its Python `register` hook runs. Extend a declared definition
without repeating its presentation or configuration metadata:

```python
from dataclasses import replace

def register(self, registration):
    card_id = "acme.notes"
    registration.nodes[card_id] = replace(
        registration.nodes[card_id], lifecycle=self.lifecycle,
    )
    # Existing register_relationship / register_capability / provider APIs work here.
```

The descriptor must declare API 1.38. Both halves commit atomically; a failed hook
publishes neither. If no Pack is explicitly registered, the Registry supplies its
existing default Pack. Structure Viewer demonstrates mixed presentation metadata
and advanced state, file preview, deployment and tutorial contracts. These Packs
contain Python code and require restart after installation.

## Build, install and diagnose

Use the backend interpreter that belongs to OAW. From an external project,
`scripts/oaw-pack.py` is an absolute-path wrapper around the existing
`python -m open_agent_world.pack` module; it does not change the working directory.

```bash
"$OAW_PYTHON" "$OAW_PACK" doctor .
"$OAW_PYTHON" "$OAW_PACK" check . --lifecycle --card example.note
"$OAW_PYTHON" "$OAW_PACK" build-project . dist/note-0.1.0.oawpack
"$OAW_PYTHON" "$OAW_PACK" inspect dist/note-0.1.0.oawpack
"$OAW_PYTHON" "$OAW_PACK" install dist/note-0.1.0.oawpack --data-root /path/to/test-profile
```

Use CLI installation with an offline test profile, then start OAW on that profile.
In a running application use **Pack & Card Library > Install Pack from File**:
a new pure declarative Pack activates immediately, including its independently
built frontend. Updates to already loaded owners require restart. Each build needs a new output
path; increment `project.version` before distributing an update. `build-project`
generates a standard adapter wheel and uses the same archive validator and
installer as advanced plugins.

For a custom UI, declare `[components.frontend]` and map a card's named surfaces,
for example `frontend = { body = "note" }`. Build using the distributed
`@oaw/plugin-api` package, then pass `--frontend-dir dist/frontend`. The Notes
example demonstrates independent React compilation, host configuration updates
and UI loading without rebuilding OAW. CI publishes the versioned SDK tarball as
an artifact named `oaw-plugin-api-1.0.0-<commit>`. Pin the chosen tarball in your
project lockfile; the repository's `file:` dependency is not required. A public npm
release and a standalone installable Python CLI distribution are still deferred.

Python dependencies belong in `project.dependencies`; runtime environments stay
in existing `[environments.<name>]` declarations. Use `resolve-adapter` and
`--dependency-wheels` for adapter dependencies. Build commands never implicitly
install into OAW. Source backend discovery uses `OPEN_AGENT_WORLD_PLUGIN_DIRS`
(project roots or parent directories containing projects); source Python changes
require a host restart. External frontend iteration uses build/install in a
dedicated profile, with restart for subsequent versions. External source frontend
hot reload is deferred.

`doctor` inspects declarations without importing plugin code. `check` performs
strict activation in its own CLI process and fails if any optional plugin fails.
`check .` restricts discovery to your project; `--lifecycle --card <type>` adds
saved-card recovery and shutdown in a temporary profile. Repeat `--card` for
multiple types. Pass `--data-root` only for an offline test profile; these checks
execute trusted code and can change that profile's test data.
`verify` performs explicit integrity verification. Runtime activation failures
remain visible in the Library with their phase and repair/restart instruction;
saved cards and connections remain recoverable and cannot grant permissions.

## Validation boundaries

Follow [Pack verification](testing.md) to build and install your project in a
disposable profile. Check configuration persistence after restart as well as
initial activation. This contribution form does not isolate Python processes
or support source hot reload.
