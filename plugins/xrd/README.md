# XRD workflow

Compare experimental diffraction spectra with reference cards or a local library,
then fit selected candidate structures with the bundled OAW_XRDfit engine.
Matching and fitting use OAW's Run/Stop controls and do not require a model prompt.

Install **XRD analysis** and its required dependencies through the Library. Open
the Pack, collect **XRD 检索与比对**, add it to your deck and place it. For a complete
workspace, use the [reusable XRD workbench](TEMPLATE_CONTRACT.md).

## Compare reference cards

1. Open the match card and click its **实验谱** input to import an ascending,
   nonnegative, two-column
   2theta/intensity CSV or whitespace text, including SmartLab RAS_RAW text.
   Binary RAW/RASX and encrypted TXT must first be exported using the instrument software.
2. Import user-supplied text reference cards containing 2-Theta, d, I and (h k l).
   The supported initial format is the PDF-card text export, not a PDF document.
3. Connect the algorithm **to** one spectrum and one or more references using
   **XRD 输入**. Only these graph-authorized inputs are read by the runtime.
4. Set the experimental wavelength and run matching. Reference angles are
   recomputed from d-spacing. A local-minimum background and Gaussian smoothing
   precede prominence-based peak detection. A one-to-one assignment matches
   peaks within the configured tolerance. Outputs show normalized overlays,
   signed peak deviations and unexplained detected peaks relative to the supplied
   references and detection thresholds.
5. Import an actual CIF and explicitly select its reference-card object in the
   CIF inspector. Connect it, the reference and spectrum to the fit algorithm,
   enable connected input mode, and select the CIF if more than one is connected.
   This association is user-declared provenance, not automatic chemical verification.
   Missing CIFs never invalidate reference matching; fitting reports missing input.

Matching screens connected reference cards or a connected local reference library.
It does not establish phase identity, phase fractions or refinement. It uses a single wavelength, does not automatically
correct zero shift and does not model K-alpha doublets. Thresholds influence both
detected and unexplained peaks. The standalone fitting card accepts one CIF; the match workspace also supports independently processing several candidates.
OAW_XRDfit's lattice-update peak bounds are not a whole-pattern crop range. A completed
run, iteration-limit stop or low Rwp is not evidence of convergence or unique identity.

Inputs retain original bytes, SHA256, parsed data and revision. Each run snapshots
the authorized documents and parameters. A deleted or disconnected reference does
not grant access through a CIF's association; all inputs require their own edge.
Template capture remaps internal reference IDs and clears external references.

The declared `scientific` environment is prepared automatically before the first
worker starts. OAW selects a private Python 3.10/3.11 and installs the pinned
dependencies under the active profile's managed runtime directory. A packaged
desktop may provide the same verified environment offline. Source and `.oawpack`
installations use the same recipe; the host backend never imports this stack.

Scientific data defaults to `<profile>/packs-data/research.xrd`, independent from
the removable dependency cache. Existing users can retain `OAW_XRD_ROOT` and
`OAW_XRD_RUN_ROOT` data paths. `OAW_XRD_PYTHON` remains an optional explicit external
interpreter override; normal use requires no manual Python configuration.
Run directories default to `<OAW_XRD_ROOT>/runs`. Run archives contain result JSON and
full input snapshots; individual history-file downloads are limited to 30 MiB.


## Local reference-library search

In the match node's **输入配置**, click **参考谱库** (or **挂载谱库 ＋** when absent) to open
the library panel. It has two sources:

- **卡片谱库** (your own cards): click **添加卡片 ＋** and choose one or more text
  reference cards (the same `PDF#…` text format as standalone reference cards). Entries
  are listed with a filter and can be deleted; adding a card with an existing ID replaces
  it. Click **附加 CIF ＋** to attach structures: a CIF is matched to the entry whose ID
  its filename starts with (for example `mp-xxxx_Li6PS5Cl.cif`). Candidates from that
  library then supply their own CIF to the structure view, preoptimization and fitting,
  with no COD download or manual CIF connection. Cards whose first line says `calculated` are marked as calculated, others as card
  exports. The data is stored in a private SQLite file under `<OAW_XRD_ROOT>/libraries`,
  never in templates and never deleted by Pack uninstall. It is searched by the native
  (遍历检索) engine even when 快速检索 is selected for mounted libraries.
- **槽位 1–6** (mounted libraries): choose an empty slot, enter the main `.sq`
  file path and click **插入并挂载**. The library is opened read-only.

Import an experimental spectrum, then choose **快速检索** (QualX3) or **遍历检索** (OAW).
The result keeps the best 20 library candidates by default (1–100).
An optional allowed-element list, e.g. `Li Ti P O`, excludes entries containing
other elements. Leave it empty for an unrestricted full-library search.

The adapter supports the SQLite `id`/`infodb` schema with comma-separated
`dvalue` and `intensita` arrays used by QualX's COD reference libraries.
It does **not** read proprietary ICDD PDF-2/PDF-4 files. The word PDF in the
card name describes its place in the diffraction-reference workflow, not an
ICDD data source. QualX3's July 2026 `cod.zip` and `cod_inorg.zip` are calculated
COD libraries; they are not the older POW_COD 2205 release.

Source files are opened read-only and are not bundled into the repository.
Configuration records path, SHA256, file size, modification time, record count
and database date. Missing or changed files require reconfiguration. Paths are
local to the machine running OAW, including after sharing a card template.

For hosted workers, the operator sets `OAW_XRD_LIBRARY_CATALOG` to a read-only
JSON file mapping stable library IDs to labels and absolute `.sq` paths, for example:

```json
{"cod": {"label": "COD", "path": "/srv/reference-libraries/cod.sq"}}
```

The slot form then lists deployed libraries and sends only `library_id` and `slot`.
The backend resolves the ID from that catalog and rejects browser-supplied paths.
Keep all four QualX files together and make both the catalog and library directory
unwritable by workers. An unreadable or invalid configured catalog blocks mounting.
Without this operator setting, local installations retain the path form.

Confirming the same slot and path reuses its existing mount when file size and
modification time are unchanged. It does not recount records or hash the library
again. New or changed files are inspected in a background thread outside the graph
mutation lock; the host checks the document revision again before saving.

Click an occupied slot and choose **卸载此槽位** to remove its mount. Other slots
and candidate structures are preserved. Removing the final slot clears the
legacy primary-library fields too, so it cannot remain as an invisible search
input. Library files remain available in the deployed catalog for remounting.

Toolkit libraries with `source_cards` and `card_id_map` retain their PDF card IDs,
per-card source kind, and source label. Numeric QualX engine IDs are not COD IDs;
these cards do not expose COD links or trigger COD structure downloads.

Library intensities are scaled to a maximum of 100 per entry before thresholding.
The single-candidate score is the geometric mean of matched reference-intensity
coverage and matched experimental-peak-prominence coverage, multiplied by
`1 - mean_abs_delta / (2 * tolerance)`. This is a heuristic ranking, not a
probability, validated phase identification, phase fraction, or multi-phase fit.
`cell_scale_percent` sets a bounded isotropic cell-length search (default ±2%,
maximum ±5%; 0 disables it). All peaks of a candidate share one scale `s`:
`d_aligned = s * d_original`, with angles recomputed using Bragg's law. A coarse
search shortlists scales, then unique peak assignments refine the shared scale in
sin(theta) space. Non-unit scaling requires at least three distinct matched peaks.
Matching remains one-to-one within the configured residual angular tolerance.
Results and scientific context preserve original peaks, raw residuals, the scale,
unscaled score and match count, and whether the scale reached its allowed limit.
The plotted peaks use the aligned positions; source cards and CIFs are unchanged.
QualX only supplies its original prefiltered candidates: scale alignment cannot
recover a card that the engine excluded. Use native traversal for full recall
under the configured scale search. No automatic zero-shift search is performed.
Invalid peak arrays are counted and their IDs
(up to 50) recorded. In full-library mode, plot residuals are relative to the
currently selected candidate; the stored residual list is for the leading one.
The top candidate is not automatically accepted or attached to a CIF.

### QualX3 subprocess engine

QualX3 recalls candidates using its strong-peak prefilter and FOM threshold;
OAW then loads only those IDs from each authorized library and applies the same
peak comparison used for manually connected reference cards. The displayed OAW
score is **not** a QualX FOM. The native alternative visits every library record.
Both routes retain per-library/slot provenance and explicit CIF associations.
No result is automatically accepted as a phase. QualX's stricter prefilter can
miss weak or overlapping minor phases; faster retrieval does not establish equal
recall or better accuracy.

Install the independently built **QualX3 v1.0.5 with the OAW CLI patch** at
`<OAW_XRD_ROOT>/engines/qualx3/bin/qualx.exe` (Windows) or `bin/qualx` (POSIX),
or set `OAW_XRD_QUALX_EXECUTABLE` to that executable. See
[`engines/README.md`](engines/README.md) for source/build details. The stock
v1.0.5 executable is rejected because it cannot select the connected database
without global settings. This plugin does not install or download engines when
a node runs. A missing executable/index produces an actionable run error; it
never silently switches to a slower engine or another library.

Each invocation receives `--database <connected.sq>`, `--settings-dir <run-local>`
and `--wavelength`. The CLI patch bypasses global settings and automatic database
discovery, and opens all SQLite files read-only. Multiple mounted slots are
searched separately. The required files are `.sq`, `.sq.info`, `.sq.infostat`,
and `.sq.search` from the same distribution. The executable path is server
configuration, never an arbitrary command supplied by a node.

The fresh QualX profile uses its default search settings (three strong peaks,
automatic peak-width tolerance, minimum FOM 0.35, maximum 3000 recalled entries).
OAW's tolerance, smoothing, prominence and intensity controls govern its subsequent
comparison/ranking, not QualX's internal peak detection. CLI output is treated as
an unordered candidate set; OAW does not mistake printed order for a QualX ranking.
If the recall cap is reached it is recorded in `engine_runs`.

Run archives include the exact generated XY (with explicit wavelength), QualX
stdout/stderr, isolated settings, executable hash, source-library metadata and
timings. Success requires evidence of actual peak search; a successful metadata
query or truncated output is rejected. Runs time out after 300 seconds per
library, and OAW Stop terminates the worker and its engine subprocesses.
On Windows, if the system temporary path is non-ASCII, `OAW_XRD_QUALX_TEMP` can
point to an ASCII temporary directory for the vendor Fortran input reader.

QualX source and binaries are separate dependencies and are not bundled into the
plugin. The upstream README mentions LGPL-3.0 while its v1.0.5 `LICENSE` contains
GPL-3.0; preserve the actual upstream license and notices when building or
distributing that engine. The small integration patch, build instructions and
Python bridge live here; database files and download archives remain local.

### Download and view a COD candidate

In the matching result, select a COD candidate and click **下载并查看结构**.
OAW downloads that entry's CIF from the official COD HTTPS endpoint, preserves
the original bytes, and opens an interactive MatterViz structure canvas inside
the result. Rotation, zoom and unit-cell controls use the existing structure
viewer. **下载 CIF** saves the original CIF for the visible candidate.

The selected structure persists in the matching card's document. Additional
downloaded candidates remain in the local cache at
`<OAW_XRD_ROOT>/structures/cod/<COD ID>/<SHA256>.cif`;
`OAW_XRD_STRUCTURE_ROOT` can override the cache directory. Later requests reuse
hash-verified cached files without network access. The cache also records the
official source URL and retrieval time. Only the public COD ID is requested;
experimental spectra are never uploaded. A missing entry, invalid CIF, timeout
or rendering error leaves the matching result intact. Downloads are limited to
2 MiB per CIF and checked for the requested COD data block, cell and coordinates.

Downloading does not establish phase identity or silently associate the CIF
with a reference card or a refinement run. The downloaded file is a candidate
structure for inspection; fitting still requires explicit inputs. Retrieval is
asynchronous and releases the graph mutation lock, with revision and editability
checks repeated before saving the selected structure.


# Multi-candidate refinement workflow

The `xrd.match` workspace has four stages: search settings, matching results,
structure preoptimization, and whole-pattern fitting. By default the top three
available matching candidates are selected; the user may select 1–10 candidates.
Each selected candidate is processed independently, not as a simultaneous mixture.

The runtime resolves candidate IDs from an immutable search result belonging to
this node. COD structures use the fixed COD retrieval/cache path; manually imported
references require an associated, authorized CIF. The experimental samples and
wavelength are frozen to the selected search run. Client-provided filesystem paths
and CIF replacements are not used to select a parent run.

Structure preoptimization calls the bundled OAW_XRDfit `StructureSolve`. It can vary the
cell and fractional coordinates within configured bounds. Only an accepted result
is adopted. A rejected attempt retains the original CIF for subsequent fitting;
errors remain visible per candidate and do not suppress successful siblings.
Full-pattern fitting calls `XRDfit` for each eligible candidate, retaining the
starting fractional coordinates while refining lattice and profile parameters.

Candidate directories retain original/starting/output CIFs, hashes, parameters,
solver reports, warnings, failure evidence, and full profile files. Displayed
comparison Rp/Rwp values are recomputed from the full original measured grid using
`w_i = 1 / max(I_observed_i, 1)`; vendor metrics remain in the report. Every compared
candidate uses the same data and weighting. Iteration limits and convergence are
reported separately. A low residual alone is not phase identification or proof of
a physically correct atomic model.

Re-running a search starts a new lineage. Re-running preoptimization invalidates
the displayed downstream fit associated with its old parent. Previous run folders
remain available; failed re-runs do not silently supply a different stage's CIF.

## Use from any Agent

Any core Agent, including the DeepSeek Harness card, can use the XRD tools through graph
connections (requires Plugin API 1.51); the XRD workbench's own Agents keep working unchanged.

| Connect Agent to | Relationship | Tools |
|---|---|---|
| Match node | 运行控制 | `xrd_run_stage` (search, preopt, fit), `xrd_run_stop`, `xrd_run_status`, `xrd_read_results` |
| Match node | 结果只读 / 结果报告归档 | `xrd_read_results`, `xrd_submit_report` |
| Spectrum canvas | 导入实验谱 | `xrd_import_pattern` |

`xrd_import_pattern` takes two-column 2θ/intensity text, so the Agent converts other
formats (xy, xye, xrdml, raw) first; the text is validated and hashed like a UI import.
It does not update earlier search runs: run the search stage again afterwards. Stage
tools start the node's normal run and the pipeline still validates run and candidate
lineage. Two nodes can share only one connection, so 运行控制 also includes result reading;
use 结果报告归档 instead when the Agent should file reports. Runs started by a tool are detached; stop them with `xrd_run_stop` or the node.

## Synchronized scientific frames

The match node opens one `xrd.spectrum-canvas` and one `xrd.structure-canvas`, reusing
them for its entire workflow and linking them through `xrd.frames`. The controller
owns the shared cursor. Scrubbing pins history; “follow live” resumes following new
frames. Frame records are saved under each owned run's `frames/` directory with an
atomically replaced `frames.json` index, independent of the final result.

Search automatically caches all returned COD CIFs (four concurrent downloads,
ranking order preserved). A missing CIF is explicit and never displays another
candidate's structure. Search frames use the reference-card peaks, not a silently
substituted calculated pattern. Preoptimization frames use the same rendered CIF
for both structure and theoretical peaks. Fit frames contain the current lattice,
unchanged input fractional coordinates, and the actual EM/Bragg profile.

The bundled engine includes frame-observation hooks; no external PyWPEM checkout
or manual patch is required. These callbacks do not change the objective or
optimizer. SciPy 1.15 has no least_squares iteration callback: preoptimization
records objective-improving evaluations (relative improvement > 1e-5), **not a
claim that every trial was an accepted optimizer iteration**. A final frame always
records the accepted output or original-CIF fallback. At most 20 intermediate
frames per candidate are retained; long fits sample complete iterations. The
final frame is additional. This is an optimizer trace, not molecular dynamics.

Colors are explicitly heuristic per-stage quality displays, not probabilities:
search uses its existing 0–1 match score; preoptimization uses
`1 / (1 + sqrt(objective))`; fit uses `1 / (1 + Rwp / 100)`. Unknown quality is gray.
Different stages' colors must not be compared quantitatively. Rwp is independently
computed on the original experimental grid before any display downsampling.
Existing runs without intermediate records cannot reconstruct historical steps;
rerun the stage to obtain a trace. Exact CIF bytes and SHA256 are stored per frame.

### Multi-phase screening and joint review

The optimizer constructs candidate combinations and the bounded numerical
profile evaluator scores them. Jev and the independent BO comparison use separate
histories and the configured evaluation budgets. Residual peak summaries support
candidate replacement or addition; unexplained peaks are not proof of impurities.

After screening, users select combinations for OAW_XRDfit joint review. Review
runs only the selected combinations; it does not automatically rerun drop-one
combinations. Search scores and joint-fit metrics remain separate. The engine
runs in an isolated process with explicit candidate/CIF/peak-file mapping,
including candidates with identical HKL sets. Exported profiles are audited on
the original experimental grid. Raw metrics, termination flags, and failures
remain available. Atomic coordinates are not independently refined in this
stage. Historical searches are not automatically rerun or relabeled.

See [TEMPLATE_CONTRACT.md](TEMPLATE_CONTRACT.md) for reusable workbenches,
v0.3.0 compatibility and the run-history storage contract.
