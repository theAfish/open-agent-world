# Pack manifests and card recipes

This is the format reference for card authors. Use
[Create cards and connect them](../developers/card-contract.md) to choose a path,
then [Pack distribution](../pack-distribution.md) for build and installation.
The Python validation models named below are authoritative. Unknown fields are
rejected; unsupported features are never silently ignored.

## Files and compatibility

| Format | Purpose | Authoritative model |
| --- | --- | --- |
| `oaw.pack.toml` with `schema_version = 3` | Source declaration, resolved together with `pyproject.toml` | `backend/packs/spec.py` |
| `manifest.json`, Schema 1 | Existing code Pack archive | `PackManifest` in `backend/packs/manifest.py` |
| `manifest.json`, Schema 2, `kind: "content"` | Host-rendered card recipes and/or Legion templates | `PackManifest`, `Content` |
| `manifest.json`, Schema 3 | Normalized source declaration and compiled adapter metadata | `PackManifest`, `PackSpec` |
| `content/*.json` listed in `content.cards` | A Factory card recipe | `CardRecipe` in `backend/packs/factory_models.py` |
| `content/*.json` listed in `content.legions` | A saved formation | `LegionBlueprintPreset`, validated by `backend/packs/content.py` |

Run `python -m open_agent_world.pack schema` with OAW's backend interpreter to
print the archive manifest JSON Schema. This describes `manifest.json`, not a
standalone TOML grammar or the complete nested Factory recipe schema. Use
`doctor <project>` for source declarations and `inspect <artifact.oawpack>` for
archives; use `check <project>` to validate runtime activation.

`compatibility.plugin_api` declares the minimum API used by a Pack, not the host
version that happened to build it. Existing `1.23` or `1.35` declarations can be
valid for older features. Keep the Python descriptor and archive requirement
consistent when providing code.

| Feature | Minimum Plugin API |
| --- | --- |
| Schema 3 passive card contributions | `1.37` |
| Mixed card contributions and a Python registration hook | `1.38` |
| Declarative traits, passive associations and required owner dependencies | `1.39` |

Schema 3 infers the required minimum when compatibility is omitted. An explicitly
older minimum fails when the declaration uses newer features. Factory exports
record the exporting host's compatibility requirements; inspect the artifact's
actual requirement before sharing with an older installation.

## Source declaration

`[distribution]` supplies `id` and `name`; its version normally comes from
`[project].version` in `pyproject.toml`. With no `[plugin]` overrides, the runtime
owner is the distribution ID and the library Pack is `<owner>.default`.
`[plugin]` can preserve explicit `owner`, `packs` and `node_types` for existing
projects. Multiple card contributions require explicit distinct card IDs.

Distribution IDs use the path-safe dotted grammar
`^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$`, with at most 120 characters.
The Factory's Pack Designer limits authored IDs to 100 characters to leave room
for its generated card and relationship suffixes.
Runtime identifiers use `^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$` and at most
128 characters. Runtime selectors are identities, not filesystem paths.
Use canonical PEP 440 versions; dependency ranges use PEP 440 specifiers.

| Source section | Meaning |
| --- | --- |
| `[compatibility]` | `plugin_api`, `oaw` version range and `frontend_api` (currently `1`) |
| `[[contributions.cards]]` | Passive card definition; see below |
| `[[contributions.associations]]` | Passive relationship rule; see below |
| `[[dependencies]]` | Required or optional `distribution`, `owner` or `pack`, with `id` and optional `version` |
| `[components.backend]` | Python project and host adapter mode; defaults to `pyproject.toml` |
| `[components.frontend]` | Frontend `entry`, optional `project`, and `mode` |
| `[environments.<name>]` | Explicit worker or Sandbox environment requirements |
| `[features.<name>]`, `[prerequisites.<name>]` | Feature/environment requirements and external prerequisites |
| `[dependency-groups.<name>]` | Explicit references to native requirement sources |

Pure declarative projects support required `kind = "owner"` dependencies only;
the builder resolves their library Pack from sibling source declarations.
Optional dependencies and other kinds require a Python entrypoint.
Dependencies must already be installed and compatible; declaring them does not
download plugins. Python and frontend build dependencies remain in their native
project files. Environment declarations do not grant cards execution behavior.

### Card contributions

The card fields are `id`, `name`, `description`, `icon`, `color`, `config_schema`,
`frontend`, `default_name`, `size`, `statuses`, `default_status`, `deck_id`,
`deck_name` and `traits`. Defaults provide a `300 × 200` card with status `ready`.
`default_status`, when set, must be among `statuses`. `config_schema` accepts an
inline object or a project-relative JSON path. See the
[supported schema subset](../developers/declarative-packs.md#minimal-project).
Frontend keys are `preview`, `body`, `settings` and `workspace`.

Traits are markers in the contributing owner's namespace. They express matching semantics only; they do
not supply a document interface, data source, execution provider or permission.

### Passive associations

Declare `id`, `label`, `short_label`, `description`, `source_types`,
`source_traits`, `target_types`, `target_traits` and `directions`.
Use the same owner namespace as the contributing Pack for relationship IDs.
At least one source selector and one target selector are required. Source types
and traits must belong to the contributing owner. Type lists match any listed
type, while trait lists require all listed traits; when both are supplied, both
conditions must hold. Each selector list contains at most 100 unique values.
Directions are `forward` or `bidirectional`; `forward` is the default. `id` and
`label` are required; `short_label` defaults to `Link` and description to empty.

```toml
[[contributions.associations]]
id = "example.connected-notes.about"
label = "About topic"
short_label = "About"
source_types = ["example.connected-notes.note"]
target_traits = ["example.connected-notes.topic"]
directions = ["forward"]
```

These compile to templateable relationships with empty capability grants and
`data_read = false`. Capability grants, `data_read`, executable callbacks,
ports, cardinality and edge payload schemas are not fields in this format.
An association records a connection; it does not move form values or invoke an
operation. Matching and direction use the ordinary relationship validator.

## Factory card recipe JSON

A Schema 2 manifest lists each recipe under `content.cards`. The recipe contains
`id`, `design` and optional `tutorials`; `design` contains `face` and `function`.
The host supplies the renderer. Exported card Packs declare the `oaw.factory`
renderer dependency, alongside dependencies inferred from their saved formations.
Local printed types and their local exact-type targets are embedded when
exporting cards or Legions. Other target providers remain declared dependencies.

```json
{
  "id": "example.forms.greeting",
  "design": {
    "face": {"title": "Greeting", "button_label": "Run"},
    "function": {
      "fields": [{"key": "name", "label": "Name", "type": "text", "default": "World"}],
      "operation": "template",
      "template": "Hello, {{name}}!",
      "associations": [{"id": "about", "label": "About", "target_type": "text"}]
    }
  }
}
```

Fields have `key`, `label`, `type` (`text`, `number`, `boolean`), `default` and
optional `required`. Defaults must match their field types. Operations are
`template`, `sum`, `multiply` and `join`; `template` references declared fields
as `{{key}}`, and `join` uses `separator`. `sum` and `multiply` require a number
field. Function associations have an `id`, `label` and exactly one of
`target_type` or `target_trait`. The host derives a stable, bounded relationship
ID from the recipe and binding IDs, with the recipe type as source and
`forward` direction. Keep binding IDs stable when editing a design.

`face` has ordinary title/style controls and optional `studio` data for the
[visual card design contract](../developers/card-design.md). Saved legacy
`button_bindings` remain inert metadata; they do not execute custom logic.
Connections are separate from buttons and do not transfer the operation result.
Use the Factory exporter to capture layouts, recipes and required dependencies
together instead of hand-assembling production archives.

## Content and archive limits

This table is the shared reference for Factory and content-Pack limits.
Limits apply independently: a valid individual recipe can still exceed its
enclosing archive's aggregate limit.

| Item | Limit | Enforced by |
| --- | --- | --- |
| Factory form fields | 20 per card | `factory_models.py` |
| Factory passive connections | 20 per design | `factory_models.py` |
| Form text / operation template | 10,000 characters | `factory_models.py` |
| Operation output | 200,000 characters | `factory_models.py` |
| Common parameters / source basket items | 30 / 50 | `factory_models.py` |
| Content recipe paths / Legion paths | 50 each | `manifest.py` |
| Recipe JSON | 8 MiB | `factory_cards.py` |
| Legion template JSON | 64 MiB | `content.py` |
| Nodes / edges per content Legion | 101 / 10,000 | `content.py` |
| Shapes / content layers per display mode | 24 / 32 | `face_design.py` |
| Vertices per polygon | 24 | `face_design.py` |
| Embedded PNG | 1 MiB; 2048 × 2048 pixels | `face_design.py` |
| Archive / expanded contents | 128 MiB / 256 MiB | `archive.py` |
| Archive files | 4,096 | `archive.py` |

Automatically embedded local connection targets count toward the recipe limit.

Content archives accept the manifest, checksums, optional README and declared
JSON content. They reject executable wheels, JavaScript modules, undeclared
payloads and install hooks. Source paths and archive paths are validated for
portable spelling and containment. Generated Schema 3 adapter wheels are an
implementation of the host compiler, not a hand-authored extension point;
installation checks their bytes against that compiler.
