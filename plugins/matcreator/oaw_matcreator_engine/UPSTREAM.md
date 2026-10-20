# Pack-owned MatCreator engine

This package contains the research execution code maintained by the MatCreator
Pack. It is loaded only by the Pack's isolated worker. It has no dependency on
an installed `matcreator` distribution and no imports from `backend` or
`open_agent_world`. The host adapter uses the public OAW plugin API.

The initial source was adapted from [AI4MS/MatCreator, commit
0328c8b814499cbf3e49116323cee050145fa08f](https://github.com/AI4MS/MatCreator/tree/0328c8b814499cbf3e49116323cee050145fa08f).
`UPSTREAM.json` records the original file hashes before adaptation. Those hashes
are provenance only: this code is editable Pack source, not a locked dependency.
The inspected upstream commit contains no LICENSE/COPYING/NOTICE file; this
import does not infer or assign a license to that upstream code.

## Retained execution behavior

- Planning and explicit approval, DAG readiness, execution orchestration and
  isolated recursive Step agents.
- Structured results, invocation cancellation, bounded JSON/tool-stream recovery
  and checkpoint-compatible attempt reconciliation.
- Execution graph, session log and trajectory formats needed by existing OAW
  sessions and the host's provider-neutral projection.

## OAW adaptations

- All resource tools are constructed through `resources.py`. Every call travels
  through the Pack's RPC adapter and current host capability authorization.
- `tool_execution.py` contains native tool failures and validates arguments
  before invocation. ADK error callbacks return unknown-tool feedback across
  all three Agent constructors. No error handler retries a side effect or
  swallows Run cancellation. `session_recovery.py` closes unanswered historical
  tool calls with an explicit unknown outcome, without replaying operations.
- The host supplies the selected model and the private per-session metadata
  directory. There is no user-global MatCreator configuration or model routing.
- Artifact URIs are verified through OAW and retained as URIs. Model-supplied
  Sandbox/host paths are never opened as input images or accepted as artifacts.
- Standalone web/API services, local shell execution, native remote providers,
  automatic knowledge extraction and the separate knowledge database are absent.
  Their resource capabilities are provided by connected OAW cards instead.
- `legacy_jobs.py` only reads existing private job records needed by old attempt
  journals; it cannot provision, start or monitor remote compute.
- The worker loads this package directly; no runtime monkey patches of an
  external MatCreator package remain. The narrowly scoped ADK JSON parser
  compatibility adapter remains private to this worker.

## Boundaries and follow-up

OAW core continues to own generic Run lifecycle, capability authorization,
Sandbox operations, Artifact versions, state and read-only execution projections.
This Pack owns the research workflow. Its `native` environment in `oaw.pack.toml`
declares third-party worker libraries and verification. Source setup, desktop
packaging and archive/Store installation use OAW's managed environment provider
to install them privately, verify readiness and track references and active use.
The legacy setup entry point delegates to that provider; engine source changes
alone do not invalidate the dependency cache. The worker never installs into
host site packages or downloads dependencies on startup.

Scientific Skills use explicitly declared profiles inside an authorized Sandbox.
Those profiles install preparation libraries and CLI packages, not external
services or scientific calculators. Model connections, cloud credentials,
licensed executables, pseudopotentials and model data remain prerequisites of
the selected model or compute target. Pack installation does not submit jobs or
prove that those external resources are usable.

The execution-history extension records stable plan/attempt identities, immutable
retry attempts, explicit replan relations, timestamps and atomic history snapshots.
Plan-scoped lookup replaces ambiguous global logical Step lookup. Explicit
`consumed_inputs` delivers known prior results; resource instrumentation records
versioned artifact publication and inspection. These changes remain entirely in
this Pack. The generic host contract transports typed edges and paginated snapshots;
the frontend owns chronological operation history, bounded tracks and direct
dependency navigation. The launcher records task forks and executor ownership;
the adapter exposes sanitized tool operations and retains planned definitions
after their actual attempts begin. Task completion never manufactures a merge.

Recorded access is evidence of delivery, not proof of the model's reasoning.
Unstructured context and pre-migration history cannot provide exact provenance.
The extension does not introduce cross-session result retrieval or a new scheduler.
