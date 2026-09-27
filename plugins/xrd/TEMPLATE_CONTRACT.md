# XRD reusable Legion contract (plugin 0.3.0)

A template contains the workflow graph, internal links, Agent instructions, algorithm defaults, review budget, and workspace layout. Source IDs are converted to template keys and then to new instance IDs. References outside the selected graph are cleared.

The library entry `XRD 分析工作台 v1` contains nine nodes: the spectrum document is owned by the spectrum canvas, and library slots are owned by the match card. It has no standalone spectrum input or library-mount cards. Each user imports their experimental spectrum and mounts their own library. Local paths, mounted database contents, experimental inputs, downloaded CIFs, candidate selections, run IDs, trials and review state are omitted. The live source workbench is unchanged. Generic Agent model IDs remain preferences; users supply their own model connections and credentials through OAW.

All XRD nodes and relationships declare template support. Runtime dependencies use the native OAW template dependency contract. Results discussion uses restored graph relationships; stale custom metadata is not carried into conversation templates.

## Bundled engine

`src/oaw_xrd/OAW_XRDfit` contains the modified engine, MIT attribution, example inputs, pinned Python requirements and a SHA256 manifest. Single-phase and joint fitting load this package, never an external PyWPEM checkout. Legacy `PyWPEM` configuration values and `pywpem_review` storage keys remain readable. The public name is OAW_XRDfit.

The wheel contains engine source and dependency specifications, not an OS-specific Python executable or a copied virtual environment. On a new machine use Python 3.10 or 3.11:

```powershell
python plugins/xrd/src/oaw_xrd/install_engine_runtime.py --root <user-data-directory>
```

Configure `OAW_XRD_ROOT` to that directory, or `OAW_XRD_PYTHON` to the installed interpreter. Installation requires package-index access. Existing configured scientific environments continue to work. QualX is a separate library-search dependency covered by `engines/README.md`; spectrum databases are user-mounted and never bundled.

The integration targets OAW v0.3.0 and plugin API 1.23. Workspace layout v1 is read through the upstream v2 migration; adding result discussion preserves existing sections and hidden views.

## Run history

The match card exposes read-only history through native node resource actions. Each run keeps its unique directory under `OAW_XRD_RUN_ROOT` (default `<OAW_XRD_ROOT>/runs`), containing `oaw.json`, frozen inputs, results, failure evidence and available frames. New runs never overwrite previous run directories. Existing records are discovered without rewriting or recomputing them.

History is scoped to the match owner, including multiphase runs executed by a separate optimizer. It lists 50 runs per page and fetches selected files on demand (30 MiB per file), with SHA256 values and original bytes. History is excluded from reusable templates. Opening a record does not restore mutable workflow settings, start computation or resume interrupted runs. Stored running records without a live local task are shown as interrupted, while the original evidence is preserved. Back up the configured XRD run root alongside the OAW profile.

Repeated joint reviews are archived under `review-history/<review_started_at_ns>/` before retrying and after termination. Each attempt retains its state and fitting outputs; the current review can update without replacing the archived attempt.
