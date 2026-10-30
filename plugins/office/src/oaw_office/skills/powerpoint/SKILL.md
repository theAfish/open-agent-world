---
name: powerpoint
description: Read slides and speaker notes, edit presentation text or create PowerPoint PPTX presentations.
---

# PowerPoint

Materialize the supplied attachment into a new workspace directory in an authorized
Sandbox. Run this skill's `scripts/office.py` with `run_skill_script` and the skill's
listed node ID. python-pptx is prepared in the Sandbox on first use.

Read slides in windows:

```json
["inspect","--input","inputs/deck.pptx","--offset","0","--limit","5"]
```

Follow `next_offset` until the requested slides are covered. Slides in results are
numbered from one; `offset` counts skipped slides and starts at zero. Text frames
have stable inspected `shape_id` values. Speaker notes and top-level tables are
included. Long text, tables and shapes have a bounded preview; `truncated` also
signals cuts within a slide. Use custom code for the full relevant contents.
Extraction does not inspect images, chart internals, grouped shape children,
animations or layout. Never describe those as visually checked from text alone.

For a simple editable 16:9 deck, create a UTF-8 JSON specification:

```json
{"slides":[{"title":"Research update","bullets":["Result one","Next experiment"],
 "notes":"Explain the measurements and limitations."}]}
```

Run `scripts/office.py` with
`["create","--spec","deck.json","--output","outputs/update.pptx"]`.
This helper uses PowerPoint's standard title/content layout. Keep bullet text short;
use custom layouts for polished decks, long prose or image-heavy presentations.

For `edit`, additionally pass `--input` and specify inspected shape IDs:

```json
{"text":[{"slide":1,"shape_id":2,"text":"Updated research title"}],
 "notes":[{"slide":1,"text":"Updated speaker notes"}]}
```

Text replacement resets inline formatting in the changed shape. Use run-level
custom editing to preserve mixed styling, links and fields. Keep the original;
helper outputs must have new filenames.

For template-based generation, images, charts, tables or detailed formatting, write
a workspace Python script using python-pptx. Run this skill's `scripts/run.py` with
`["work/build_deck.py"]` to retain the declared dependencies. Templates can be
materialized attachments or explicitly copied skill assets. For mixed Office work,
exchange files between skills in the same workspace.

Reopen the result and inspect slide count, text and notes. Structural verification
does not detect clipping, missing fonts, overlapping shapes or visual regressions.
Use a compatible renderer when available and requested; otherwise state that layout
has not been rendered. Publish the finished PPTX with `publish_artifact` and send
the returned attachment reference. Legacy `.ppt`, macros and encrypted decks require
an explicitly prepared conversion workflow.
