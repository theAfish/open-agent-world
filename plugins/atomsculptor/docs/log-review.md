# Report Agent

Report Agent turns native OAW records into a simple sequence for a later article:

**Structure → brief decision summary + actual operation/result → Structure → …**

The card uses OAW's native Agent runtime, model selection, private Skill toolbox,
Run export and Sandbox Python. It does not run the modelling Planner/Builder team.

## Use

1. Select a model on Report Agent. Connect **Read run history** to the source Agent.
2. Connect an output Sandbox with **Execute + Start/Stop**, or **Execute** if it is
   running. Also grant access to the source Sandbox to collect its saved files;
   one Sandbox can serve both purposes. Skills are equipped internally.
3. Ask: “整理最新任务，按结构→简短思考摘要与操作→结构展示，保留结构文件和图片，导出文章用数据。”

The Agent exports the native task, inspects key operations, then builds the report.
A brief decision summary uses only retained evidence and labels inference or unknown
motivation. It need not explain every read or rewrite a full manifest. Missing data
is reported without repeated rebuilds. An explicitly supplied archive also works.

## Outputs

- `index.html` / `report.css`: only saved structure images and a short thought/action
  transition between them. Unchanged validation/import does not have a separate
  visible node. Candidates stay together; clicking a caption downloads its structure.
- `records.html`: complete operation details, results, source identities, warnings
  and the full timeline. These records stay separate from the minimal main report.
- `结构演变.md`: the same sequence with images and original-file links for editing.
- `flowchart.svg` / `flowchart.mmd`: sequence diagram and Mermaid source. SVG
  embeds every available preview so it can be taken out of the report folder;
  its text remains editable. Multiple candidates share one level.
- `trajectory.csv` / `trajectory.json`: ordered structure/action data for article
  figures and tables: decision summary, operation, result, original file, preview,
  available atom count/composition/cell/PBC, provenance and native record identity.
  Candidates from one operation share an order value; they are not serial changes.
- `structures/` and `previews/`: retained structure bytes and saved-file renders.
- `attachments/`: referenced scripts, measurements and other data, copied as evidence.
- `raw/`, `sandbox files/`, `review.json`, `操作说明.md`: complete source records and
  detailed operation facts, kept available without crowding the main sequence.
- `review.zip`: the report, data, structures, images and evidence together.

The default Skill path is inspect_archive → build_review with optional inline
short-ID summaries. It reuses the same native plan for inspection and rendering.
Existing manifest and incremental-note calls still work for saved reports.

## Provenance and access

Only native task_id or explicit continuation_run_id links group attempts. Similar
prompts do not merge independent runs. An active latest Run is reported rather than
silently replaced with an older one. Read run history belongs to Report Agent;
source and destination Sandbox permissions are checked independently.

Exports use the existing indexed native layout and chunk transfers. Workspace-bound
Runs contribute assigned directories and explicitly read shared inputs. Older
unpartitioned runs retain a labelled full-workspace snapshot. Export/report paths
are relative to Report Agent's assigned workspace, claimed before writing.

A preserved execution-time version is historical. A file collected later is a
collection_snapshot. Lost versions are missing: a final file at the same path is
not reused as an earlier geometry. Rendered images are file previews, not historical
UI screenshots. Archived code is never executed and missing structures are not
regenerated. Validation or import alone does not create another geometry frame;
sequence arrows do not prove a dependency. Unknown usage stays unknown.

Skill version 0.3.2 provides this minimal sequence. New cards receive it; existing
editable private toolboxes and saved Agent instructions are preserved. To adopt the
new version, create a new Report Agent or explicitly update its instruction/toolbox.
The native default run_execution_deadline_seconds=0 disables a total Run deadline;
Stop and native stream-health checks remain active.

## Design references

- [ASE trajectories](https://wiki.fysik.dtu.dk/ase/ase/io/trajectory.html): retain a
  sequence of saved structures rather than reconstructing past configurations.
- [AiiDA provenance](https://aiida.readthedocs.io/projects/aiida-core/en/stable/topics/provenance/concepts.html):
  distinguish structure data from the operation acting on it; use native OAW records.
- [OVITO export](https://www.ovito.org/manual/usage/export.html): keep per-frame
  structure files and export available metadata in tabular form.
- [Chemiscope (JOSS)](https://doi.org/10.21105/joss.02117) and its
  [viewer-grid code](https://github.com/lab-cosmo/chemiscope/blob/main/src/structure/grid.ts):
  keep structures prominent, group comparison views and offer an image export.
- [NGLview (Bioinformatics)](https://doi.org/10.1093/bioinformatics/btx789) and its
  [image-export code](https://github.com/nglviewer/nglview/blob/master/nglview/widget.py):
  use a clean molecular figure and high-resolution saved images. Report still
  renders saved data with the existing ASE/Matplotlib Skill at 300 dpi; it does
  not introduce NGLview or a browser rendering dependency. Its SVG combines
  vector labels with embedded raster previews, not all-vector atom geometry.

These references inform the presentation only. No additional platform, database,
AiiDA/OVITO dependency or scientific computation is introduced.
