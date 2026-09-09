# Local OAW research plugins

Base: upstream `theAfish/open-agent-world` main `defffb0` (checked 2026-09-09).
Worktree: `open-agent-world-library-xrd`, branch `codex/library-xrd-plugins`.
The original `open-agent-world` worktree, uncommitted changes and database remain untouched.

## Start

From this worktree, on this Windows machine:

```powershell
uv sync --project backend --extra adk --extra litellm
npm.cmd --prefix frontend ci
.\scripts\dev-research.ps1
```

The research launcher installs the editable plugins and uses a separate data root:
`%LOCALAPPDATA%/OpenAgentWorld-Research-v2`. It delegates service management to
upstream's launcher and prints the actual ports (normally 5173 / 8000).
Do not run two launchers for this worktree simultaneously.
LLM agents use OAW model settings; XRD runs locally without an LLM API key.

## Library

`plugins/library` registers `library.region`, `library.paper`, and the explicit
`library.read` capability. PDF bytes, extracted page text, thumbnail, notes and
reading page are revisioned OAW node documents, not browser-only storage.
Each PDF is limited to 25 MiB. Scanned PDFs are viewable but require separate OCR
before an agent can read their text. No automatic web search/downloader is added.

- Create Paper nodes from Objects and import PDFs with the file picker.
- Use native Legion team spaces to organize Papers and Agents. New standalone
  Library Regions are no longer offered; existing migrated Regions retain their
  import picker and background file-drop support for compatibility.
- Paper preview shows a cover thumbnail and a full-card decorative sweep;
  open its inspector or window-local fullscreen reader to read.
- Connect Agent to Paper using **Read paper** to grant page-text access.
- Membership does not automatically grant tools or create an access-control
  security sandbox. Explicit edges continue to work across container boundaries.

Host integration includes container-body `PluginSurface`, reader expansion,
translation settings/routes, PDF.js resolution, palette registration and focused
canvas interaction changes. The legacy file-drop hook attaches to the shared
`.container-frame` shell and handles native file events only.

## XRD

**Early prototype, not yet carefully polished.** This port primarily establishes
the execution and result-delivery path. Its UI, workflow ergonomics, parameter
defaults and broader scientific validation still need dedicated work; it is not
a production-ready, thoroughly validated analysis product.

`plugins/xrd` registers `xrd.analysis` and the `research.xrd` runtime provider.
It calls the existing sibling `XRD/PyWPEM` through its `.venv-xrd` interpreter;
the original XRD source is not modified. Override its location with
`OAW_XRD_ROOT` when launching manually.

Create an XRD analysis node in the XRD deck. In settings, use the official Mn2O3
demo or upload an ascending, headerless two-column 2theta/intensity CSV and one
candidate CIF, set wavelength/angle window/iterations, then use OAW Run/Stop.
Each run has a unique directory `XRD/runs/oaw-<run_id>` with input hashes, logs,
`oaw.json`, `result.json`, fitted profiles and CIF. Completed result archives up
to 30 MiB are downloadable in the node; larger results stay in the local folder.
Stop terminates the child process tree on Windows.

This is **candidate-structure whole-pattern fitting**, not blind identification
of unknown phases and not a claim of full Rietveld refinement. The inherited
workflow handles one candidate CIF; background defaults match the official demo.
Reaching the iteration limit is not convergence. Prompt text does not change the
numerical settings: use the settings fields. It does not consume connected data
nodes automatically; CSV/CIF uploads are the current input interface.

## Migration and verification

`scripts/migrate-library.py` reads the legacy SQLite DB read-only and migrates
into an empty destination via public APIs. It preserves node IDs, copies PDF
documents and text resources, and converts `library.contains` to native
`parent_id` membership. API keys, old runtime history and browser-only state are
not migrated. Do not rerun against a populated world.
The local report is `.open-agent-world/migration-report.json`; it is runtime data
and must not be committed. A new XRD demo node is added separately.

The local one-iteration public Mn2O3 demo smoke test returned
OAW status `succeeded`, Rp 2.9670%,
Rwp 6.1940%, stop_flag 3, **not converged**. This verifies execution and result
delivery only, not scientific accuracy for arbitrary user spectra.

Small isolated checks:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_research_plugins.py -q
npm.cmd --prefix frontend run build
```

No changes are pushed to GitHub. To take future upstream updates, merge/rebase
this branch after reviewing the public plugin contract and host integration points.

## Selectable full-page reader

The reader uses PDF.js canvas + selectable text layers. It supports single-page
and continuous-scroll modes, restores reading progress, and fits the available
window. Fullscreen remains inside the application viewport; Return keeps the
reader mounted. The right-side drawer contains bookmarks and saved annotations.

Annotation mode provides text highlighting, rectangular screenshots and image
import. A selection-adjacent toolbar offers learning-card creation, colors and
an editor for title/content/comment. Text selection defaults to yellow and the
active selection has a blue outline. Backspace/Delete in fullscreen deletes the
selected annotation rather than the outer Paper node; text inputs retain normal
editing. The old standalone reading-notes input is removed without deleting
previously stored note data.

Each Paper owns a persistent study canvas with pan/zoom, draggable excerpt/image
cards and automatic page-grouped mind-map layout. Canvas/card titles are editable;
cards offer preset heading colors and collapsible excerpts. This layout is
deterministic page grouping, not AI-generated semantic clustering.

Translation supports OAW model connections and DeepL Free. API endpoints and
credentials are configured in global OAW Settings (Models / DeepL), using
protected credential storage. The reader's top-right gear selects Library-wide
service/model/target-language preferences only, stored separately as non-secret
preferences. Only selected text and the translation request are sent, not the
whole PDF. OCR and automatic whole-document translation are not included.

Because the upstream plugin API has no translation/secret bridge, the small
`backend/api/library_translation.py` route accesses OAW's protected connection
and forwards selected text. It is registered in `backend/api/router.py`; these
are additional host integration points alongside the container view hook and
reader-expand event. The PDF.js dependency is resolved by the frontend config.
Credential-free/error behavior and annotation persistence are tested locally;
no real paid translation is run automatically as part of verification.

## Conversation meeting notes

Connect a Conversation to a Text node with **Meeting notes** (`conversation_notes`).
Agents connected through Participate inherit read/edit access to that Text only;
unrelated agents do not. Removing participation or the notes edge revokes access
on the next operation. Writing is user-directed, not automatic transcript dumping.

The Text editor reads the authoritative resource endpoint rather than the empty
content/history placeholders in node snapshots. It reloads on resource revision
changes, preserves dirty drafts and saves against the draft's base revision so
concurrent agent writes cannot be silently overwritten. Loading placeholders
cannot be saved. Resource history is loaded from the resource API.

## UI and verification scope

Legion entry/exit hints have boundary glows and an arc-shaped resize control.
Expanded cards use a narrow connection boundary. Agent forms and Text editing
areas disable node dragging/panning. Reader controls use compact icon buttons.

Focused frontend checks include `TextCard.test.tsx` (resource refresh, dirty-draft
protection and loading guards), `worldStore.test.ts`, and TypeScript compilation.
Backend checks include `test_capabilities.py` and `test_research_plugins.py`.
These checks are not a complete browser E2E or scientific-validation suite.

The Codex runtime plugin is inherited from the base version, not newly added by
this PR. A local independent-session smoke test successfully read Legion shared
state through OAW tools; this does not attach the current desktop conversation.
