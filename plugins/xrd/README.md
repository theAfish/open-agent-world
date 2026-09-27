# XRD workflow

The plugin integrates experimental input into `xrd.spectrum-canvas` and library mounts into `xrd.match`. `xrd.reference` and `xrd.cif` remain available for explicit reference inputs; legacy pattern/library cards remain readable. The `xrd.analysis` fit card uses the bundled OAW_XRDfit engine. Both algorithms use OAW Run/Stop without an artificial prompt.

1. Import an experimental spectrum: ascending, nonnegative, two-column
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
PyWPEM's lattice-update peak bounds are not a whole-pattern crop range. A completed
run, iteration-limit stop or low Rwp is not evidence of convergence or unique identity.

Inputs retain original bytes, SHA256, parsed data and revision. Each run snapshots
the authorized documents and parameters. A deleted or disconnected reference does
not grant access through a CIF's association; all inputs require their own edge.
Template capture remaps internal reference IDs and clears external references.

The normal OAW backend loader discovers this plugin in `plugins/xrd`; the regular
frontend build includes its UI. No additional web service or alternate port is
required. New cards are available from OAW's XRD card pack after rebuilding the
frontend and restarting the existing application against its existing data store.

`OAW_XRD_ROOT` points to the scientific runtime/data directory with
`.venv-xrd/Scripts/python.exe` on Windows or `.venv-xrd/bin/python` elsewhere.
`OAW_XRD_PYTHON` can override the scientific interpreter location. The modified
OAW_XRDfit source is bundled in the plugin; install its scientific dependencies
using the runtime installer described in [TEMPLATE_CONTRACT.md](TEMPLATE_CONTRACT.md).
Matching uses NumPy/SciPy in that environment, without adding scientific
dependencies to the OAW backend. `OAW_XRD_RUN_ROOT` optionally isolates result
directories (default: `<OAW_XRD_ROOT>/runs`). Run archives contain result JSON and
full input snapshots; individual history-file downloads are limited to 30 MiB.


Validation: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_xrd.py -q`;
`npm --prefix frontend run test:e2e -- xrd-match.spec.ts` with the installed browser
channel selected through `PLAYWRIGHT_CHANNEL`. The end-to-end test uses an isolated
OAW store and should set `OAW_XRD_RUN_ROOT` to a test output directory.
Scientific-runtime tests explicitly skip when no configured interpreter exists;
document and capability tests still run. Real experimental files and local result
directories are not distributed with the plugin or used as required CI fixtures.
# Local reference-library search

Add **XRD PDF / 参考谱库**, enter the main `.sq` file path, and click **加载谱库**.
Connect an `xrd.match` card to one experimental spectrum and the library using
`XRD 输入`. Select **QualX3 快速检索** (default) or **OAW 逐条检索**.
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

Library intensities are scaled to a maximum of 100 per entry before thresholding.
The single-candidate score is the geometric mean of matched reference-intensity
coverage and matched experimental-peak-prominence coverage, multiplied by
`1 - mean_abs_delta / (2 * tolerance)`. This is a heuristic ranking, not a
probability, validated phase identification, phase fraction, or multi-phase fit.
Matching remains one-to-one within the configured angular tolerance. No automatic
zero-shift search is performed. Invalid peak arrays are counted and their IDs
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

Structure preoptimization calls the local PyWPEM `StructureSolve`. It can vary the
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

Focused regression checks:
`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_xrd_pipeline.py backend/tests/test_xrd_refinement.py -q`
and the XRD frontend tests under `frontend/src/plugins`.

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

The local PyWPEM checkout requires the observer hooks recorded in
`engines/pywpem-live-frames.patch`. These callbacks do not change the objective or
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
