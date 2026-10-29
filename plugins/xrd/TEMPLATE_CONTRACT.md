# XRD reusable Legion contract (plugin 0.3.0)

A template contains the workflow graph, internal links, Agent instructions, algorithm defaults, review budget, and workspace layout. Source IDs are converted to template keys and then to new instance IDs. References outside the selected graph are cleared.

The library entry `XRD 分析工作台 v1` contains nine nodes: the spectrum document is owned by the spectrum canvas, and library slots are owned by the match card. It has no standalone spectrum input or library-mount cards. Each user imports their experimental spectrum and mounts their own library. Local paths, mounted database contents, experimental inputs, downloaded CIFs, candidate selections, run IDs, trials and review state are omitted. The live source workbench is unchanged. Generic Agent model IDs remain preferences; users supply their own model connections and credentials through OAW.

All XRD nodes and relationships declare template support. Runtime dependencies use the native OAW template dependency contract. Results discussion uses restored graph relationships; stale custom metadata is not carried into conversation templates.

## Bundled engine

`src/oaw_xrd/OAW_XRDfit` contains the modified engine, MIT attribution, example inputs, pinned Python requirements and a SHA256 manifest. Single-phase and joint fitting load this package, never an external PyWPEM checkout. Legacy `PyWPEM` configuration values and `pywpem_review` storage keys remain readable. The public name is OAW_XRDfit.

The wheel contains engine source and dependency specifications. OAW prepares the
declared scientific environment on first use and chooses a compatible private
Python automatically. To prepare it ahead of time with the host Python:

```powershell
python -m oaw_xrd.install_engine_runtime --data-root <OAW-profile-directory>
```

Installation requires package-index access unless the verified environment is
already bundled or cached. `OAW_XRD_ROOT` controls retained scientific data only;
`OAW_XRD_PYTHON` remains an optional externally managed interpreter override.
QualX is separately declared as an executable and can adopt an existing compatible
installation after checking its bridge flags; OAW does not redistribute its binary.
Spectrum databases are user-mounted and never bundled or deleted by Pack uninstall.

The integration requires plugin API 1.35 for managed environment preparation and profile binding. Workspace layout v1 is read through the upstream v2 migration; adding result discussion preserves existing sections and hidden views.

## Run history

The match card exposes read-only history through native node resource actions. Each run keeps its unique directory under `OAW_XRD_RUN_ROOT` (default `<OAW_XRD_ROOT>/runs`), containing `oaw.json`, frozen inputs, results, failure evidence and available frames. New runs never overwrite previous run directories. Existing records are discovered without rewriting or recomputing them.

History is scoped to the match owner, including multiphase runs executed by a separate optimizer. It lists 50 runs per page and fetches selected files on demand (30 MiB per file), with SHA256 values and original bytes. History is excluded from reusable templates. Opening a record does not restore mutable workflow settings, start computation or resume interrupted runs. Stored running records without a live local task are shown as interrupted, while the original evidence is preserved. Back up the configured XRD run root alongside the OAW profile.

Repeated joint reviews are archived under `review-history/<review_started_at_ns>/` before retrying and after termination. Each attempt retains its state and fitting outputs; the current review can update without replacing the archived attempt.

## Shipped workbench (0.3.1)

The former `XRD 分析工作台 v1` was an author-local saved formation, not a distributed preset. The plugin now registers `research.xrd.workbench` in the XRD Pack. Open **Library → XRD analysis**, collect **XRD 分析工作台**, add it to the XRD deck, and place it to create the complete workspace.

The preset contains ten cards and nine links, including a fresh SQL database for the match card's history and report grants for the analysis agents. It uses the host's current configuration defaults and layout v2. Each instance has its own IDs and empty inputs; configure the user's models, library mounts and numerical runtime before running. SQL template capture copies configuration only, never database files or rows.

The SVG cover is packaged as `assets/workbench-cover.svg` and served through the plugin asset API. Both the Pack and the formation detail preview use it.

For a separately installable content Pack, run from this checkout:

```powershell
backend/.venv/Scripts/python.exe scripts/export-xrd-workbench.py --output dist/xrd-workbench-v2.oawpack
```

This uses the standard inspected/checksummed OAW content Pack format. It requires the versions of core, XRD and SQLite listed in its manifest; it is not an engine installer. The built-in preset and SVG are included in the XRD Python package. A clean-profile test installs the exported Pack, restarts the recipient, and places all ten cards.
