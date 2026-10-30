---
name: word
description: Read, edit or create Word DOCX documents with paragraphs, tables and reusable document code.
---

# Word

Use an authorized Sandbox. Materialize conversation attachments with
`materialize_artifact` into a new workspace directory. Keep the source file.
Run this skill's `scripts/office.py` with `run_skill_script`; use its returned
node ID as `skill`. The Pack prepares python-docx in that Sandbox on first use.

Helpers accept workspace-relative paths and return JSON. Example argv:

```json
["inspect", "--input", "inputs/report.docx", "--offset", "0", "--limit", "50"]
```

Read subsequent windows using `next_offset`. Text, tables and table cells can also
be truncated inside a window; use custom code to read the relevant full content.
Inspection covers main-body paragraphs/tables in order. It does not read headers,
footers, tracked changes, embedded charts or images, or verify page layout.

For a new document, write a UTF-8 JSON specification in the workspace:

```json
{"blocks":[{"type":"paragraph","text":"Quarterly report","style":"Title"},
 {"type":"paragraph","text":"Results","style":"Heading 1"},
 {"type":"paragraph","text":"Revenue increased."},
 {"type":"table","rows":[["Metric","Value"],["Revenue","120"]]}]}
```

Run `scripts/office.py` with
`["create","--spec","report.json","--output","outputs/report.docx"]`.
For a small edit, `edit` additionally takes `--input`. Its specification is:

```json
{"paragraphs":[{"index":0,"text":"Updated title"}],
 "cells":[{"table":0,"row":1,"column":1,"text":"130"}]}
```

Paragraph, table, row and column indices start at zero. Replacing `.text` resets
inline formatting, fields and links in that paragraph/cell. Use run-level custom
code when those must survive. The helper refuses existing output filenames.

For templates, precise formatting, images, sections or other advanced work, write
a workspace Python script using python-docx, then run this skill's `scripts/run.py`
with `["work/build_report.py"]` (append script arguments as needed). This preserves
the skill's prepared dependencies; ordinary `execute_command` does not select them.
Templates and assets belong in the authorized workspace or this skill's files.

Reopen and inspect the result. Helpers perform a structural reopen before saving;
that is not a visual check. Report layout as unverified unless a real compatible
renderer was used. Finally publish the finished DOCX through `publish_artifact` and
send its returned attachment reference in the conversation. A sandbox path is not
a downloadable attachment. Legacy `.doc`, encrypted files and macro-enabled files
require a separate, explicit conversion workflow.
