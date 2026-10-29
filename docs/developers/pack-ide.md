# Pack IDE (Experimental): one source project

The unified **Design / Function / Code** workspace and visual logic editing are
experimental and still in development. For full Python/React Card Apps, the
recommended path is an external IDE or Coding Agent using the public SDKs and
[developer guides](index.md), then the standard [build and packaging workflow](../pack-distribution.md).
The visual IDE is optional.

Open the Factory **Pack Designer → Developer tools → Pack IDE (Experimental)**.
It is also available under **Pack & Card Library → Developer tools** at the
bottom of the Library, including when the Factory Pack is not installed.
Create a visual Card project or open an existing Schema 3 source directory. Design, Function, Code and external
editors all work on that directory. The IDE does not recover source from a
compiled `.oawpack`.

`oaw.pack.toml` owns distribution identity, compatibility, dependencies,
contributions and optional backend/frontend components. `pyproject.toml` owns
the version and Python build metadata. Python, React/TypeScript, assets and
documentation remain ordinary files. The IDE only stores chosen project roots
in the existing local profile settings; it does not store a second project
representation.

## Visual authoring

Create a project from the compact launcher or open a recent source directory.
The project tree groups Cards, source, visual designs and resources. Select a
Card to keep its live surface visible in **Design** and **Function**. Click an
element on the Card to inspect its label, style and layout, or insert a field,
button, text or result. Change the surface to edit a different display size.
Undo restores the preceding visual edit.

**Function** shows inputs, exposed actions, state and results. Selecting a
button reveals its binding. Declarative cards use the existing template, join,
sum and multiply operations; **Test action** uses the bounded function evaluator
and displays the result or failure. Python cards expose the statically discovered
action identity and require an installed test Card for real execution. The same
Face Designer and Function Designer models are used by the runtime. Save writes
the real JSON resource, preserving handwritten code.

The top bar shows unsaved/saving/saved/error states. **Check** reports static
errors without importing the project's Python. Python syntax and malformed
design JSON errors include file locations; select a diagnostic to open its
source. Preview exercises bounded declarative functions or an installed Card;
an uninstalled Python preview does not establish runtime readiness.

Build / Export calls the canonical Schema 3 builder. New visual-only projects
receive the existing generated adapter wheel and use the ordinary installer and
PluginRegistry. They do not require an independently compiled frontend. Export
produces a `.oawpack`; the existing Marketplace handoff becomes available after
export for non-`local.*` IDs. The user signs in and selects the exported file on
the Marketplace website. Opening that page does not upload or publish a Pack.

## Binding existing Python behavior

Plugin API 1.39 adds a presentation resource mapping to the source declaration:

```toml
[compatibility]
plugin_api = "1.39"

[designs]
"example.analysis-card" = "designs/card.json"
```

The referenced JSON contains `face` (the existing FaceDesign), `function` (the
existing FunctionDesign), `action`, `result_key` and `surfaces`. An advanced
card's `action` names an existing `NodeResourceAction`, for example
`analyze_structure`. Input keys become that action's arguments; `result_key`
selects the returned result for display. Function button bindings can name other
registered resource actions. A missing action is rejected at registration.
The installed renderer dispatches through OAW's ordinary node resource endpoint,
with the existing authorization and cancellation boundaries.

Presentation overlays are applied after the advanced Python registration hook.
They retain its behavior, state, lifecycle, relationships, capabilities and
execution definitions. They only replace the explicitly selected frontend
slots. Keep a handwritten React `workspace`, for example, by omitting
`workspace` from `surfaces`; an empty list preserves every custom frontend slot.
Complex JSX remains a Code/external-editor workflow.

The [Cell Analysis example](../../examples/packs/analysis-card/README.md) shows
the complete mixed path. Appearance edits only write JSON; they do not regenerate
its handwritten Python. Changes to a Python adapter require a rebuild and
controlled host restart. Dynamic Python registrations cannot be fully understood
by static inspection; an installed test profile validates their actual contracts.

## External tools and Agents

**Code** uses CodeMirror 6 for Python, JavaScript/TypeScript/TSX, JSON, TOML,
Markdown and other text resources. Open files from the project tree and switch
between tabs. Line numbers, syntax colors, indentation, bracket matching,
find (`Ctrl/Cmd+F`), undo/redo and save (`Ctrl/Cmd+S`) use normal editor shortcuts.
Cursor position, scroll, undo history and unsaved content remain available when
switching files or returning from Design/Function. The optional Card preview
can be hidden to give the editor more space.

For a visual-only Pack, **Add Python starter** creates an ordinary Python
module and `PYTHON_SETUP.md`. Follow that guide to connect its entry point and
build a wheel. Merely adding the starter does not replace the working visual
behavior or execute code.

Code edits operate on actual project files. Every read supplies a SHA-256 hash;
save requires the hash that was read. Check and Build also compare the complete
project revision. External changes cause a conflict instead of silently replacing
newer files. Refresh to inspect the changed source and compare disk contents
with the current draft. Reload requires confirmation before discarding the
draft. Project switching also confirms unsaved changes. Native desktop folder
selection reuses the existing folder picker; the browser provides the editable
path fallback. Copy the project path to open it in an external editor.

An OAW Agent uses its existing authorized workspace/file/Sandbox tools on the
same files. A host project handle does not grant an Agent filesystem permission
or permission to execute generated code. External coding agents can edit the
directory directly and use the standard CLI:

```sh
python /path/to/OAW/scripts/oaw-pack.py doctor /path/to/project
python /path/to/OAW/scripts/oaw-pack.py build-project /path/to/project /path/to/result.oawpack
python /path/to/OAW/scripts/oaw-pack.py inspect /path/to/result.oawpack
python /path/to/OAW/scripts/oaw-pack.py project inspect /path/to/project
python /path/to/OAW/scripts/oaw-pack.py project read /path/to/project --path designs/card.json
```

`project write` accepts `--path`, `--content-file` and the `--expected-sha256`
returned by read. Use `init /path/to/new-project --visual` to create the same
visual source skeleton from the command line. For an OAW Agent, choose a project
directory already inside its granted workspace; the default local-profile
project directory is not automatically shared with Sandboxes.

For advanced projects, build Python and frontend code in your external editor
or authorized development environment, then pass `--backend-wheel` and
`--frontend-dir` to `build-project`. **Build settings** accepts the same prebuilt
inputs. A changed Python source requires a new matching wheel before export;
the IDE rejects stale wheels. Successful export reports the archive filename
and downloads it through the browser or desktop's existing download behavior.
It never runs a project's build hooks or imports its source in the main host.
Use `check --lifecycle` only for trusted code in a disposable development process
and profile; unlike `doctor` and IDE Check, this command loads plugin code.
Never use a production profile to test unreviewed code.

## Compatibility and verification boundaries

The IDE emits Schema 3 source projects and retains edits when closed and reopened.
Schema 2 content Packs use the separate [content Pack workflow](../creator-packs.md);
Schema 1 is unsupported. Traditional face design and printing are described in
[Card Factory](../card-factory.md).

Check the exported Pack in a disposable host profile, including installation,
restart and saved-card behavior. See [Card App verification](testing.md).
