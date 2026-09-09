# MatCreator on OAW

This plugin publishes four portable Toolsets and an evolving Know-Do Graph.
MatCreator source: `theAfish/MatCreator`, `devel` commit
`a1a57688cdb7fc476498476cc388f932b6e83d6a`.

The package loader uses OAW SkillPackage directly. Materials Core contains
structure manipulation/conversion, plotting and supporting guides; Atomistic
Simulation contains ASE, DFT/MD, phonons and equations of state; Materials AI
contains ML potentials, training and the complete MatterGen family; Research and
Remote Compute contains database/search and remote-provider skills, including the
complete Tavily family. Nested SKILL.md files, scripts, references, binary assets,
frontmatter and source hashes are retained. OAW's internal Skill resource members
remain inside their Toolset; there is no separate published card type per skill.

## Start the demo

From the OAW repository:

```powershell
backend/.venv/Scripts/python.exe plugins/matcreator/create_demo.py
$env:OPEN_AGENT_WORLD_DATA_ROOT = (Resolve-Path .open-agent-world/matcreator-demo).Path
./scripts/dev.ps1
```

Restart the frontend and backend after adding the plugin. Local plugin discovery
loads it automatically; no MatCreator runtime, ADK tools, graph database or remote
provider is installed by importing the plugin. The demo creation command creates
new cards each time; use a fresh data root for an isolated repeat.

The wheel contains the backend and all four packages. The frontend extension is
included in the source distribution; keep its `frontend/` folder under the local
plugin directory and rebuild OAW, as with other OAW frontend plugins.

Configure an actual Agent model in OAW before asking it to work. The creation
script uses the mock provider only to create the world without calling an LLM;
the acceptance test invokes the real Agent capability provider directly and is
not a claim of autonomous LLM task completion.

Choose Ubuntu WSL for the first scientific demo. Bind a dedicated existing
Windows folder as the Sandbox's read/write workspace. Provision Python/ASE
explicitly. The validated test copies already installed Ubuntu packages into
`python-libs` inside that folder using `provision_science.py`; it does not install
dependencies or grant access to the host home. For example, when Ubuntu already
has ASE, NumPy and SciPy:

```powershell
wsl -d Ubuntu -- python3 /mnt/d/AI/open-agent-world/plugins/matcreator/provision_science.py --destination /mnt/d/your-demo-workspace/python-libs
```

Select the connected Local ASE Environment when executing. It sets
`OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`. This profile is an explicit
invocation choice. Start the Sandbox after configuring its runtime and folder.

1. Connect Materials Agent to Materials Core using **Use skills**, to KDG using
   **Use and learn**, and to Sandbox using **Execute**. The creation script sets
   these links and the Environment link.
2. Ask: “Use Local structure demo to build a 2×2×2 copper supercell. Run its
   `scripts/build_structure.py` through the Sandbox with interpreter `python3`,
   the Local ASE Environment and argv `['--repeat','2','--output','copper2',
   '--library-path','python-libs']`. Verify the report and publish all three
   output files as an Artifact.” Expected atom count: **32**.
3. Open the KDG's **Open workspace** Window. This switches the container itself
   to an inline circular relationship graph that moves and scales with the world
   canvas. **Show member cards** restores its spatial Skill members. Drag a Toolset
   directly from the Tools deck into the KDG body to preview and confirm importing
   its package; no temporary world Toolbox is created. Alternatively use **Assimilate a Toolset**, choose
   Materials Core, preview and confirm. Alternatively drag the Toolset header
   into the KDG body, away from its connection boundary, and confirm. Ordinary
   connections never assimilate anything.
4. Search `copper` or `structure`; click an entry to inspect it and double-click
   to expand its neighborhood. The consumed Toolset and its old relationship
   are removed after commit. No manual source deletion is necessary.
5. Ask for the related **3×3×3** task. The Agent uses `knowledge_search` and
   `knowledge_inspect`, then passes the returned `skill_node_id` to OAW's normal
   `run_skill_script`. Expected atom count: **108**. Publish CIF, extxyz and JSON.
6. **Use and learn** permits recording experience. **Curate knowledge** is a
   separate explicit relationship granting durable edits and distillation. The
   Window's Review mode also supports explicit memory promotion with evidence.

The native acceptance test verified both structure roundtrips, real Artifact
publication, and denial after revoking KDG access. The Windows AppContainer
baseline test also passed, but this ASE distribution's ctypes/SciPy dependency
did not load under its existing isolation mitigations. The scientific demo is
therefore validated on WSL; Windows isolation settings were not weakened.

## Knowledge and resource lifecycle

Publisher knowledge is immutable. Edit mode can save a user-owned copy, create
new entries and deliberately create/remove typed relationships. Memories remain
pending until reviewed into a Heuristic or Procedure. Promotion records evidence
and `derived_from` relationships; it never marks knowledge scientifically tested
without an explicit editor decision. Agent inspections increment usage metadata.

Assimilation is a pure plugin conversion plus a host transaction. The target
document, recreated Skill resource members and source consumption commit together.
The host refuses non-document sources or sources with external lifecycle effects,
checks both document revisions, and buffers mutation events until commit. Scripts
remain Skill files, not graph-node text. Meaningful procedure references become
Procedure nodes; other support files remain addressable resources.

Palette drops use the same transformation endpoint with `source_type` instead of
`source_id` and `source_revision`. The host accepts only user-creatable inert
document types with declared initial content. Preview is read-only; confirmation
checks the destination revision and writes the graph and its members atomically.
Palette provenance records the type and plugin, with no source node ID.

Each imported package has an immutable SHA-256-keyed source snapshot, including
complete original adapted package bytes and source provenance. Download snapshots
from the Window. A snapshot's `package` value is an ordinary `SkillPackage` and can
be exported using `open_agent_world.skill_packages.export_plugin`. This is recovery
of the consumed source package; publishing selected evolved graph knowledge is
not implemented in this milestone.

Search/expansion responses are bounded (default 60, maximum 200 plus explicitly
requested roots), with type, relationship, source, trust and memory filters.
Full bodies and resources load only on inspection. Backend queries currently scan
the host-owned document; they do not require a second persistence database.
The Window renders the progressive working subgraph using OAW's existing React
Flow, preserves node positions while expanding and culls offscreen nodes. A
1,200-entry contract fixture verifies bounded paging; it is not a performance
benchmark of all possible graph shapes.

## Verification commands

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_matcreator.py
$env:OAW_MATCREATOR_PYTHON = 'python3'
$env:OAW_TEST_SANDBOX_RUNTIME = 'wsl:Ubuntu'
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_matcreator.py::test_native_ase_before_after_assimilation_and_artifact
```

The native test needs a real desktop account with WSL access and provisioned
Ubuntu packages. It creates a bound temporary workspace, copies those packages,
uses normal capability calls, publishes actual artifacts and destroys its Sandbox
after successful validation. It never calls an LLM or sends data to a remote
compute/search provider. UI acceptance: `frontend/e2e/matcreator.spec.ts` against
isolated OAW backend/frontend servers on ports 8017/5177.

Regenerate bundled source packages explicitly with:

```powershell
backend/.venv/Scripts/python.exe plugins/matcreator/build_packages.py path/to/MatCreator
```

The maintainer importer requires PyYAML. Check out the pinned revision first;
normal plugin startup only reads the generated JSON packages and requires no YAML
loader, network, scientific runtime or MatCreator installation.
