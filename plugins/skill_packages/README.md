# Skill Toolboxes

**Tools → Skill Toolbox** creates an empty toolbox. Open its workspace, add skills,
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

Connect an Agent with **Use skills**. Its `read_skills_*` tool lists skill names and
descriptions, plus shared instructions. Supplying `skill_id` reads that skill's full
instructions, defaults, folders and text files. Binary assets are listed with their
media type and size. Supply `skill_id` and `file_path` to retrieve a specific file;
binary contents return base64 and media type. Skills are guidance for the Agent: they do
not register new executables, mount files, or grant Sandbox permissions. Use the
normal world connections for execution. Disconnecting the toolbox revokes reading.

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
type, relationship and capability. It requires host Plugin API 1.4; it does not
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
./backend/.venv/Scripts/python.exe -m pytest backend/tests/test_skill_packages.py
$env:PLAYWRIGHT_CHANNEL = "msedge" # or chrome, if installed
npm --prefix frontend run test:e2e -- skill-toolbox.spec.ts
```

The backend scenarios cover progressive reading, local edits, Legion copies and
plugin export/reload across versions with scripts and binary assets. The browser
scenario edits nested default fields, creates scripts, imports a directory tree
and image, renames a file, downloads the plugin and verifies reload/delete/undo.
