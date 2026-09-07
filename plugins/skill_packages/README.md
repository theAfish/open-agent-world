# Skill Toolboxes

**Tools → Skill Toolbox** creates an empty toolbox. Use **Edit toolbox** to add skills,
and give each a name, a description of when to use it, and Markdown instructions.
Import SKILL.md copies its complete text into the instruction editor. **Default
settings** uses named fields with text, numbers, switches, groups and lists; no
JSON editing is required, including for existing nested settings.
Toolbox settings contain shared working instructions, author, package ID and version.
The document limit is 16 MiB for the complete toolbox, including encoded assets.

**Files and folders** is a directory editor. Create your own `scripts/`, `assets/`,
`references/`, `templates/` or other folders; create and edit text files; move,
rename, download or delete files. Import files into the selected folder, or import
a complete skill folder to keep its nested layout. The selected folder's contents
are placed in the current location; a root SKILL.md becomes the skill's instructions.
Existing files are kept; conflicting imports ask you to choose a different location.
Binary files such as images keep their original bytes, and common image formats
have a preview. Empty folders created in the editor are also preserved.

Connect an Agent with **Use skills**. Its `read_skills(toolbox)` tool lists skill names and
descriptions, plus shared instructions. Supplying `skill_id` reads that skill's full
instructions, defaults, folders and text files. Binary assets are listed with their
media type and size. Supply `skill_id` and `file_path` to retrieve a specific file;
binary contents return base64 and media type. A Skill provides instructions and
reusable runtime files. It never executes by itself or grants Sandbox permission.
Disconnecting the toolbox revokes access to its current members.

## Run bundled scripts

Give an Agent access to a Skill (directly or through its Toolbox) and an **Execute**
connection to a Sandbox. Equipping the same resources uses these same capabilities.
The `run_skill_script` tool appears when both capabilities are present. Multiple
Skills and Sandboxes share one tool; each selector lists its authorized resources.
Start the Sandbox, inspect its runtime, then select the Skill and Sandbox using
their listed aliases (exact unambiguous names or node IDs are also accepted):

```json
{
  "skill": "review",
  "sandbox": "python",
  "script": "scripts/check.py",
  "interpreter": ["python3"],
  "argv": ["--output", "report.txt"]
}
```

Choose an interpreter installed and accessible inside that Sandbox. For Windows
batch files, use `script: "scripts/check.cmd"` and
`interpreter: ["cmd.exe", "/d", "/c", "call"]`. Omit `interpreter` for an executable
file supported by the runtime (for example a Linux script with a shebang). Shell
interpreters apply their own quoting and expansion rules. The host never installs
dependencies or falls back to executing on the unrestricted host.

The host lazily materializes `SKILL.md`, bundled files and empty directories under
the Sandbox's private `.oaw/skills/<skill-node-id>/`, outside its mutable workspace.
Linux and WSL expose the selected bundle at `/.oaw/skills/<skill-node-id>/` using a
read-only bind mount. Windows uses a Sandbox-owned directory with command-scoped
AppContainer read/execute grants and explicit write denial. These paths are runtime
state, not generated artifacts, portable configuration or ordinary resource mounts.

Scripts run through the normal Sandbox backend with its workspace as `cwd`, and
return the usual command result and events. Use the script's location to read
references, assets and templates; write relative output paths in the workspace.
Copy individual templates explicitly when they need editing, for example inside
a bundled Python script:

```python
from pathlib import Path
import shutil

bundle = Path(__file__).resolve().parent.parent
shutil.copyfile(bundle / "templates/report.md", Path.cwd() / "report.md")
```

Listing or reading instructions does not start a Sandbox or materialize files.
Repeated execution reuses the current bundle bytes; edits replace its materialization
on the next execution without retaining old versions. Only declared files enter the
bundle: host credentials, environment, node configuration and default settings are
not copied. Runtime filenames must be portable relative paths without traversal,
Windows device names, alternate streams or case aliases.

Every invocation rechecks live Skill and Sandbox access. After a command ends,
ordinary Sandbox commands cannot read its cached Skill files. Disconnecting either
resource prevents new Skill execution; an already submitted command retains its
normal Sandbox lifetime and can be stopped through the Sandbox controls. Destroying
the Sandbox removes materializations. Duplicated and summoned Agents receive fresh
equipped Sandboxes, workspaces and runtime caches.

## Independent cards and open spaces

**Tools ? Skill** creates a standalone card. Drag its header into a toolbox to
join, and drag it out to detach. Skills inside remain ordinary editable nodes
with their own external connections. Moving a toolbox moves its members.

Connect an Agent directly with **Use skill** to read only that skill. This does
not include sibling skills or the toolbox conventions. Connecting the toolbox
with **Use skills** grants its shared instructions and all current members.
Membership changes immediately change that aggregate access; direct connections
survive detaching. Old embedded skills become live nodes on startup.

The plugin API `NodeContainerDefinition` declares accepted member traits. Its
optional `document_field` projects child documents into a collection; `member_type`
is the plugin-owned document-only type used when adding entries. Entries carry a
`name` and a host `node_id`; child documents remain the source of their contents.
Toolbox capture stores metadata while the normal node/edge capture stores children.
Legion and Toolbox share the open container frame, drag membership, resizing,
dissolution and deletion. A toolbox can live inside a Legion. Dragging a skill out
of that toolbox but into the surrounding Legion keeps it as a direct Legion member.
See the [container base contract](../../docs/plugins.md#open-containers-plugin-api-15)
for custom container definitions and default UI.
The bundled Skill type is `oaw.skills.skill`; curated plugins own their corresponding
`<toolbox-type>.skill` types and exact-target reading relationships.

## Share a toolbox

Save your edits and choose **Export plugin**. The downloaded ZIP contains an ordinary
Python plugin package: `pyproject.toml`, a factory, `package.json`, a README and
actual files under `src/<module>/skills/<skill_id>/`. Each skill has a SKILL.md and
its own scripts, assets and other folders. The generated factory reads that tree;
scripts remain files and are not imported or run during plugin discovery.
`package.json` records settings and file paths (null for UTF-8 text, or media type
metadata for binary files). Existing plugins with inline text files still work.
Extract it and use the existing plugin installation flow:

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./path/to/oaw-toolbox-example-review
```

For a permanent checkout-local installation:

```powershell
uv add --project backend --editable ./path/to/oaw-toolbox-example-review
```

Restart the application after installation. Its curated card appears under Tools,
and uses the same editor as an empty toolbox. The published plugin owns its node
type, relationship and capability. It requires host Plugin API 1.10; it does not
depend on the bundled toolbox plugin being installed, inject frontend code, or
introduce a separate plugin loader.

Package IDs identify releases. Keep the ID when updating the same package; change
it when publishing your own fork. The ZIP contains exactly the saved local contents.
Creation copies a preset into a card document, with its source plugin ID and version.
Upgrading the installed plugin changes newly created cards. Existing cards keep
their original content and local edits, even if they were never edited. There is
no automatic merging, hash comparison, or background update mechanism.

Toolboxes and their Agent connections can also be captured in a Legion. Each
instance gets its own document. Canvas deletion and undo preserve edited content.

## Publish directly from Python

The public helper `open_agent_world.skill_packages` builds ordinary plugin
contributions using the public plugin contracts:

```python
from open_agent_world.skill_packages import Skill, SkillPackage, SkillPackagePlugin

def create_plugin():
    return SkillPackagePlugin(SkillPackage(
        package_id="example.review",
        version="1.0.0",
        name="Review Bench",
        author="Example",
        description="A small set of tools for reviewing changes.",
        instructions="Read the diff, inspect the behavior, then report findings.",
        skills=[Skill(
            id="review",
            name="Review a patch",
            description="Before handing over a code change.",
            instructions="Trace the changed behavior. Explain each finding with an example.",
            files={"references/checklist.md": "Check the public API and its callers.",
                   "scripts/check.py": "print('Review the changed behavior')"},
            directories=["assets"],
            defaults={"focus": "behavior"},
        )],
    ))
```

Point an `open_agent_world.plugins` entry point at this factory. A larger plugin
can instead call `register_skill_package(registration, node_type="example.tools",
package=...)` alongside its other contributions. Each toolbox uses an exact target
node type, so its reading relationship cannot accidentally match another plugin's
toolbox with a different document or ownership contract.

The bundled empty-toolbox implementation is itself an installable plugin at this
directory, and uses the same helper. The reviewed frontend is selected by
`ui.skill-package.v1`.

## Verify

```powershell
./backend/.venv/Scripts/python.exe -m pytest backend/tests/test_skill_packages.py backend/tests/test_skill_runtime.py
$env:PLAYWRIGHT_CHANNEL = "msedge" # or chrome, if installed
npm --prefix frontend run test:e2e -- skill-toolbox.spec.ts
```

The backend scenarios cover progressive reading, local edits, Legion copies and
plugin export/reload across versions with scripts and binary assets. The browser
scenario edits nested default fields, creates scripts, imports a directory tree
and image, renames a file, downloads the plugin and verifies reload/delete/undo.

Real isolation tests are explicit opt-ins. Set `OAW_TEST_SANDBOX_RUNTIME` to
`windows`, `linux`, or an installed `wsl:<distribution>` and run
`backend/tests/test_skill_runtime.py -k real_skill`. These exercise the ordinary
capability provider and native backend, including workspace output, write refusal
and refusal to reuse cached paths after access is revoked.
