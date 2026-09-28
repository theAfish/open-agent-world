# Literature Research for OAW

This plugin adds a versioned Research Scope, scoped public Crossref searches,
source-linked evidence, method packages, immutable snapshots, bounded frontier
routes and a reusable MatCreator literature legion. The main canvas AutoResearch
button only changes presentation; it never starts or stops an Agent or search.

## Use

1. Turn on **AutoResearch** at the bottom left of the existing world canvas.
   Select or create a research scope. Save a question, boundaries, optional seed
   Papers and a finite query/Paper budget.
2. Search Crossref. DOI identity prevents duplicate Paper creation. Results show
   registered bibliographic metadata and source abstracts when available. Fulltext
   links are explicitly unverified; import an authorized PDF into its Paper.
3. Read normally, use local citation popovers, or turn on **ADHD** to score the
   current page with the separately configured local causal language model.
4. Record an exact host-generated paragraph and a separately attributed claim.
   Location validity is not scientific verification. PDF replacements invalidate
   previous source positions; immutable previous bytes and reading state remain
   accessible through the Paper's version button.
5. Save a record snapshot, or have an explicitly connected Agent submit a
   source-backed synthesis. Record snapshots contain actual coverage and sources,
   not automatically invented scientific conclusions or a purported core set.
6. Propose a frontier with a query, missing evidence, rationale and explicit
   budget. Explore starts one real bounded search. Failed, cancelled, empty and
   successful searches remain distinct. Local priority is an explainable rule;
   it is neither a success probability nor a scientific quality assessment.

## Basecamp and exploration map

The functional literature Legion is a basecamp. Its `literature.index` card is a
live directory over the Scope document; it does not duplicate Paper documents.
Search results are placed outside the Legion. Synchronizing an older formation
moves its scoped Papers out of that Legion while preserving IDs, reading data and
search receipts. Existing external cards keep their positions on later syncs.

`literature.trail` materializes saved frontier routes. `literature.finding` shows
source-linked methods, web sources, perspectives and explicitly selected core
collections. Methods retain their original MethodSpec and source anchors, and
can be exported or imported into the existing KDG. Multiple routes can point to
the same finding. Connections retain a stated reason; a topology label does not
itself validate scientific support.

The directory and route workspace support creating findings, connecting scoped
entities, selecting a branch's core collection and attaching an existing Barracks
camp. Micro close-reading, method extraction and branch search can be staged on
the existing task board; staging does not execute a worker. Core collections are
curated selections, and field snapshots retain their existing evidence checks.

The coordinator's `literature.coordinate` relationship grants the scoped
`literature.organize` tool. Search workers retain their original narrower grants.
Semantic `literature.*` map edges grant no capabilities. Agent task staging also
requires a task-board grant; using a camp still requires the existing Summoner
and Barracks authorization. Copies remap their Scope reference and clear active
exploration identities, so they cannot silently modify the original map.

AutoResearch keeps all Papers, exploration cards and their workspaces visible.
Its fog and route landmarks follow the actual card positions. Revealing a card
means it can be navigated; it is not a measure of scientific confidence.

The directory is the departure station for ordered `exploration_roads`. Initial
Papers occupy a trunk; frontier signposts can continue that road or branch from a
specific parent member/signpost. A Paper has one navigation location, while its
source records and shared-perspective links remain independent of road order.
Methods stay beside their source Paper unless explicitly mounted on a road.
The directory workspace supports ordering, moving members, changing parent roads
and straight/branch modes. Explicit layout spreads roads outward from the Legion;
ordinary sync preserves existing positions. Only actual frontier search records
assign newly discovered Papers to a route automatically.

AutoResearch draws rounded clearings and continuous corridors along these roads.
The mask is a bounded, low-resolution raster with union compositing; panning and
zooming do not add overlapping fog opacity or enlarge a world-sized bitmap.
Its road overlay replaces duplicate navigation strokes only inside AutoResearch.
The original canvas loading and zoom behavior are unchanged.
The camp's direction entry stays at the same world anchor across zoom levels:
a counted flag at overview scale becomes a 2.5D signpost up close. Materialized
trails use their 2.5D landmark as the node itself, with straight/branch labels.

Fog color and boundary density use a pre-baked, seamless 256-square cloud texture
anchored to world coordinates. A second sampling scale supplies fine wisps; the
outer mask band forms irregular cloud banks while card and road cores stay clear.
The dark palette is charcoal and the light palette is pale grey. The texture has
no idle animation, avoids per-pixel noise generation during gestures, and the
viewport bitmap remains capped at 512 pixels per side regardless of device DPI.

The coordinator's scoped `literature.organize` capability exposes `reorder`,
`move_member`, `reparent`, `route_mode` and `layout`, with revision checks, scoped
members and cycle validation. A real Minister role is separately applied through
the existing host appointment flow; renaming an Agent does not appoint it. The
Minister's normal canvas radius still applies to ordinary canvas tools.

## Permissions and execution

`literature.read` reads only the Scope's Papers, contracts, snapshots and records.
`literature.research` additionally permits budgeted metadata search and attributed
records. Desktop scope edits append intent revisions; workers cannot silently
increase their budget. Provider calls recheck live grants before dispatch and
before Paper creation. Reusing a request ID returns its receipt without another
network call. Interrupted reservations are not automatically retried.

Six ordinary Skill packages can be used separately. Their installed
`references/oaw-runtime.md` maps workflows to the actual host tools. The legion
preset uses the existing MatCreator, KDG, task board, private Summoner and Barracks
workers. Minister appointments require an actual existing Role grant. There is no
new scheduler, invented role, automatic Sandbox permission or background model
spending. Read-only workers return drafts to the coordinator.

Method exports contain MethodSpec, original source hashes, implementation files,
missing parameters and validation records. Host-imported methods default to
**draft**. A separately authorized controlled execution plus an independent
baseline can generate a validated package; a zero exit code alone only establishes
execution. KDG import retains an immutable source snapshot and requires its current
revision. A synthetic arithmetic demo does not reproduce a scientific publication.

The Jev literature rubric/adapter protocol is implemented and unit tested. The
desktop priority operation currently uses the local rule and explicitly records
Jev as unavailable; no paid provider is selected or called automatically.

## Data and migration

Paper migration is lazy and additive. Historical binaries are content addressed
in host SQLite tables, owned by the original Paper document, with reading-state
snapshots. Old documents retain IDs and current notes. Scope snapshots never
overwrite previous versions. Copying a filled Scope archives its original results,
maps included Paper/Task/KDG references, clears active execution records and starts
paused. Copied Paper source anchors must be relocated before new evidence is used.
Historical binary versions belong to the original Paper; a portable card copy
contains its current PDF, not an implicit grant to the original archive.

## Local scorer

Optional `DATA_ROOT/reading-scorer.json` points to an isolated Python runtime and
pinned SmolLM2-135M model. `scripts/install-reading-scorer.ps1` verifies fixed model
hashes, installs the lockfile into `DATA_ROOT/tools/reading-scorer`, and copies model
assets to `DATA_ROOT/models/smollm2-135m`. With `-Offline`, installation uses existing
uv caches. `scripts/reading-scorer/download_model.py --output <model-directory>`
is an explicit setup-time download helper; inference never downloads assets or
uploads PDF text. Missing configuration leaves ordinary reading available.

The fixed causal scorer uses exact source text, CPU float32, context 256 and stride
128 by default; the page's first token is unscored. Scores are negative log2 input
token probabilities, not importance or evidential strength. The primary model
language is English; other-language reading quality is uncalibrated. Coloring
leaves text selection, annotations and citation hit targets unchanged. Cache keys
bind PDF/parser/text/model/runtime/context versions; cancellation stops queued work.

## Verification

Focused tests cover real PDF anchors/rotation, source forgery and replacement,
budget/replay/revocation, snapshot freshness, actual template deployment/copy,
KDG resource inspection, controlled numerical execution and causal scoring.
Browser tests use an isolated profile or explicit mocks, with real PDF.js geometry.
Research-v2 production data is never populated with acceptance fixtures.


## Research hub and scope departure

In AutoResearch, the index is the visible Research hub: directory/roads and
question/budget settings share one entry. A paired legacy Scope card is concealed
only in this view; its document ID, budget, evidence and capability grants remain
unchanged. A standalone Scope also opens the hub. Normal canvas presentation is
retained for compatibility.

Each selected Scope has its own initial signpost beside its Legion, anchored to
that Scope's hub. Independent direction roads have `parent_id: null` and depart
from this Scope origin; existing roads retain their parent until explicitly
reorganized. Road settings can move a direction back to the Scope origin or onto
another road. Source-linked `related`/`supports`/`contrasts` connections allow
cross-road joins without making the parent hierarchy cyclic or duplicating a
shared finding. These connections reveal their navigable corridor.

In AutoResearch a materialized trail is itself a draggable signpost, with a flag
at overview scale and the stone sprite up close. Clicking its title unfolds an anchored detail window wearing the 2.5D stone sign on top.
The compact marker is concealed while open and restored on close; the underlying
road geometry and stored card size remain unchanged. The initial signpost shows its Scope and main roads; direction
signposts show their search controls, sources, findings and topology controls. There is no second floating sign beside a rectangular
trail preview. New core collections stay in the hub until explicitly mounted on
a road, at which point a finding card is materialized. Existing mounted
collections keep their placement.
