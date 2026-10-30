# Office Skills

An installable Open Agent World Pack containing Word, Excel and PowerPoint skills
and an Office Workbench formation. It uses the existing Skill editor, toolbox,
Sandbox and Conversation attachments. No additional frontend or model provider is
required. Any Agent with the appropriate live connections can use the toolbox.

## First use

1. Install `theafish.office-0.1.0.oawpack` from Library → Packs, then restart OAW.
2. Open **Office Workbench** from this Pack in the Library. Choose your normal
   model connection for its Agent, and configure an available Sandbox runtime.
3. Start the Sandbox. First execution prepares the selected skill's Python
   dependencies using the existing managed Sandbox environment. Package downloads
   require the Sandbox's permitted network access; a declaration is not readiness.
4. Attach a DOCX, XLSX or PPTX to the Conversation, or request a new file.
5. Download the returned attachment. Ask for a focused revision to continue.

Example: “Create an Excel workbook with a Results sheet containing samples A1=10
and A2=15, and a Double column with formulas. Return the XLSX and explain which
results have been calculated.” Expected: a new, structurally readable XLSX attachment
with formulas preserved and an explicit notice that Excel formula recalculation
has not run.

You can also create **Office Skills** alone and connect its **Use skills**
relationship to an existing Agent with Sandbox execution and Conversation access.
Skills inside the toolbox are independent cards and can be connected individually.
Reading a Skill never grants file or execution access by itself.

## Capabilities and limits

| Skill | Built-in helpers | Custom work |
| --- | --- | --- |
| Word | Ordered body text/tables; create paragraphs/tables; edit paragraph/cell text | python-docx scripts, templates, sections, images and formatting |
| Excel | All-sheet inventory; bounded cell windows with formulas and caches; create/edit XLSX | openpyxl scripts for full-range analysis, styling and charts |
| PowerPoint | Slide text, tables and notes; create an editable title/bullet deck; edit text/notes | python-pptx scripts for templates, images, charts and layouts |

Each skill includes `scripts/run.py`, allowing an Agent's workspace Python script
to use that skill's prepared environment. Helpers accept workspace-relative paths,
refuse existing output filenames, and reopen generated output before delivery.
Bundled helper inputs are limited to 64 MiB compressed and 256 MiB expanded.
These helper limits do not change the host's upload/transfer limits.

This release does not include a page renderer, OCR or an Excel calculation engine.
It reports preview truncation and missing formula caches; present caches may be
stale. Structural reopening is not visual validation. Inline text replacement can
reset run formatting. Unsupported embedded content may require custom code or a
separate engine. Old binary formats, macros and encrypted documents are not handled
by these helpers. Original attachments and previous outputs remain retained.

Runtime libraries are installed only in the authorized Sandbox, independently of
the host adapter. No credentials, host paths, model settings or user documents ship
in the Pack. Third-party libraries retain their own licenses.

## Source development

Use the ordinary Schema 3 Pack build and install commands. The adapter has no
third-party host dependencies; Office engines are declared in `oaw.pack.toml`.
The runtime Pack and Marketplace presentation share `src/oaw_office/pack.json`.

Upstream APIs: [python-docx](https://python-docx.readthedocs.io/en/latest/),
[openpyxl](https://openpyxl.readthedocs.io/en/stable/),
[python-pptx](https://python-pptx.readthedocs.io/en/latest/).
