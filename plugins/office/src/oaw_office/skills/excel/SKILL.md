---
name: excel
description: Analyze, inspect, edit or create Excel XLSX workbooks, with explicit formula and cached-value handling.
---

# Excel

Materialize attachments into a new directory in an authorized Sandbox. Use
`run_skill_script` with this skill's node ID and `scripts/office.py` so its
openpyxl dependencies are prepared inside that Sandbox. Keep the original workbook.

Inspect a bounded rectangle; the response lists every worksheet and its dimensions:

```json
["inspect","--input","inputs/data.xlsx","--sheet","Results",
 "--start-row","1","--rows","100","--start-column","1","--columns","20"]
```

Rows and columns start at one. Follow `next_row` and `next_column` as needed and
read the requested range on every relevant sheet. A window is at most 200 rows by
50 columns; long cell text has an explicit truncation flag. Use custom code for
full-data aggregations rather than calculating from a preview window.

Formula cells expose both `formula` and the file's cached `value`. `cache_missing`
means that value is absent, not zero or an empty measurement. A present cached value
can also be stale. openpyxl does not evaluate formulas. Preserve formulas when
editing, and report any calculation that requires an unavailable recalculation
engine. For analysis, compute requested results explicitly from source data in
Python and label their provenance. Do not claim Excel recalculated a workbook.

Create from a workspace UTF-8 JSON specification:

```json
{"sheets":[{"name":"Results","rows":[["Sample","Value","Double"],
 ["A1",10,"=B2*2"],["A2",15,"=B3*2"]]}]}
```

Run `scripts/office.py` with
`["create","--spec","data.json","--output","outputs/results.xlsx"]`.
Strings starting with `=` are formulas; write untrusted literal strings explicitly
as text with custom code when they must not become formulas.

For `edit`, additionally pass `--input` and use this specification:

```json
{"cells":[{"sheet":"Results","cell":"B2","value":12,"number_format":"0.00"},
 {"sheet":"Results","cell":"C2","value":"=B2*2"}]}
```

Editing loads the formula-bearing workbook, never the data-only copy. Outputs
must use new filenames. Saving can remove formula caches; charts, drawings and
unsupported workbook features require additional fidelity checks.

For analysis, styling, charts, templates or large ranges, write a workspace Python
script using openpyxl and the standard library, then execute this skill's
`scripts/run.py` with `["work/analyze.py"]` and optional script arguments. That
entrypoint runs inside the skill environment. Additional packages are not implied.

Inspect results and relevant formulas after saving. The helper structurally reopens
its output; it does not verify chart appearance, print layout or formula results.
Publish the finished workbook through `publish_artifact`, then use the returned
attachment reference in `send_message`. Report unprocessed ranges or unsupported
features. Legacy `.xls`, `.xlsb`, macros and encrypted workbooks need a separately
prepared workflow; do not silently round-trip them as XLSX.
