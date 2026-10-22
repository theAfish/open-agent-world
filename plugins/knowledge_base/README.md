# Knowledge base

An automatically discovered OAW plugin (`knowledge.base`). One card owns one SQL
file and carries three related workflows over the same collection:

- **Literature** — raw data → markdown → full-text search, or → structured JSON
  projection → draft graph → human review → published knowledge graph.
- **Experiments** — upload the files of one experiment (a photographed table, a
  spreadsheet, a note) and they become one **upload**; **Extract records** confirms
  its name, description and the schema describing one specimen record, then
  projects the files, read as one document, into one or more
  structured records — through a connected **Lab assistant** Agent when there is
  one, else through one model call — → a person reviews each as a table or in a
  schema-driven form (or by asking the Lab assistant) → **publishes** it as approved data.
- **Insights** — the meta-knowledge layer over the Experiment workflow: the card
  computes statistics over the published records of one schema, several, or all of
  them (distributions, outliers, correlations — and, across schemas, correlations
  between fields of different schemas over the specimens they share by name), a
  model — the Lab assistant or one call — proposes **draft insights** (an anomaly, a
  rule, a trend, a correlation, a threshold, a note), each evidence-linked to the
  records it rests on → a person edits and **publishes** each one, which writes it,
  the records it cites and the fields it is about into the knowledge graph's
  **experiment layer**, beside the literature facts. Asking the Lab assistant "any
  anomalies in July?" or "what drives yield?" reads those published insights.
- The knowledge graph is opt-in, not required by either workflow: search answers
  document-level questions and published experiment records answer structured ones
  without ever touching a draft or a review. What *does* reach the graph from the
  Experiment workflow is a published insight — never a record on its own.

This is a proof of concept built on the `mat_know_base` (MKB) Python SDK. It runs
the pipeline in process, stores every byte in one SQLite file, and needs no Docker,
no MinIO, no PostgreSQL and no object store. The plugin imports MKB as a fixed,
installed dependency; it never modifies it — full-text search, experiment records,
insights and the multi-format converters below are all implemented in this plugin,
in `search_store.py`, `experiment_store.py`, `insight_store.py`, `insight_stats.py`
and `markdown.py`, alongside MKB's own tables in the same SQLite file, the same way
the knowledge graph already is.

The same package runs two ways. **Embedded**, as the card described below.
**Standalone**, as a small HTTP service, an MCP server and a `kb` CLI over the same
operations — see [Running it without OAW](#running-it-without-oaw) — so the
pipeline can be driven from a terminal or reached by another agent system with no
backend running.

## Install the engines

MKB, the PDF reader and the spreadsheet reader are declared in this Pack's
`pyproject.toml`. Its `oaw.pack.toml` uses the same managed adapter layer for
source development and `.oawpack`/Store installation. Adapter libraries share
OAW's Python process, so versions and package ownership must be compatible;
they are stored outside host site-packages and tracked for shared use and removal.

For a source checkout, run `scripts/install-plugins.py plugins/knowledge_base`
with the backend interpreter (`backend/.venv/Scripts/python.exe` on Windows,
`backend/.venv/bin/python` on Linux/macOS). Setup scripts prepare all bundled
Pack declarations. Source discovery itself never installs dependencies.
Published schema 3 archives include verified dependency wheels and use the same
preparation checks. See [Pack installation and distribution](../../docs/pack-distribution.md)
for building, dependency resolution and uninstall behavior.

A missing or incompatible declared adapter dependency is an installation/startup
error. The card's named missing-engine errors remain a diagnostic for legacy or
incomplete environments, not the normal installation workflow.

For the optional remote PDF engine, open the card's **Settings**, set the
**MinerU base URL**, enter **MinerU token** and choose **Save token**. The token is
encrypted in the host credential store, scoped to this card. Leaving it blank
preserves the saved value; **Remove token** restores external fallback. Resolution
order is card Secret, global `OAW_MINERU_TOKEN` setting, then server environment:

```sh
export OAW_MINERU_TOKEN=...
```

For images (a photographed lab notebook, a chart, a gel image), a vision-capable
model transcribes the picture — OCR cannot read handwriting, so this is the only
image engine. Inside OAW it is chosen in the card's **Settings** under **Vision model
for images**: any OpenAI-compatible connection from OAW's model catalog, and when left
on the default it uses the **OAW default model** (Settings → Models). The plugin
resolves that connection at conversion time through a host callback; the key never
enters the card, a job or a response. If no connection resolves, the plugin falls
back to three variables — a card secret, a global **Settings → Environment
variables** entry (plain or Secret), or the backend's process environment, in that
order — which is also how the standalone service is configured:

```sh
export OAW_VISION_API_KEY=...
export OAW_VISION_BASE_URL=https://api.openai.com/v1
export OAW_VISION_MODEL=gpt-4o-mini   # optional, this is the default
```

Saved MinerU tokens are never returned to the frontend or stored in card state,
job parameters or Legion templates. Global Value settings remain ordinary values;
use the card's Secret field for encrypted storage. `OAW_MINERU_TOKEN` in global
settings is host-only and is not injected into Sandboxes.

## The Lab Manager formation

The card is most useful with someone to ask about it. The plugin registers a Legion
preset, **Lab Manager**, in the Legion deck: deploying it places a knowledge
base, a **Lab assistant** Agent and a Conversation, already wired and already laid out —
the card on one side with a rail for its two workflows, **Literature** and
**Experiment**, and the conversation beside it.

**You drive the pipeline; the Lab assistant reads, and drafts experiment records
only when you ask.** Uploading a file, converting it, running a projection, building
a draft, approving it and publishing a record are buttons in the workspace. The Lab
assistant holds **Knowledge experiment**: every read tool — it can tell you what the
base contains, search or read a converted document back to you, read experiment
records and insights (and the statistics behind them) and query the published graph
— plus the experiment half: it is what the Experiment workflow's **Extract records**
and **Extract insights** buttons start when it is connected, and the one you ask in
the conversation to fix a draft record or a draft insight. It cannot ingest,
convert, project, build a draft graph, approve or publish. Nothing enters the graph,
and no record or insight is published, except by your click. Connect a second Agent with
**Knowledge read** if you want a strictly read-only one beside it.

Saving your own knowledge Legion works too, but note what a copy is: the node type is
templateable while the database is not captured, so **a deployed copy is an empty
knowledge base** with the same name and wiring — no sources, no groups, no facts, and
settings back at their defaults. This is the same promise the card's deletion warning
makes.

## Using the card

Open the **Knowledge base** pack in the card library, place a card, then open its
workspace. The card's own rail switches between its two workflows; each workflow
keeps its own tab strip of sections, so only what belongs to the workflow you are in
ever shows.

### Literature

1. **Sources** — organize documents into **groups** (create, rename or delete one
   from **Manage groups** beside the group selector), then add PDF,
   markdown, text, CSV, TSV, Excel (`.xlsx`), image or JSON files up to 32 MiB each.
   Uploading never converts a file on its own: select one or more sources and press
   **Process** — alone or in a batch — to queue their markdown conversion. The
   conversion jobs and their progress or failures live in this same section, right
   below the source list, not in a tab of their own. Sources also holds full-text
   **search** over every already-converted document — ranked, each result carrying
   the source filename and the heading it falls under, and a click jumps straight
   to that source. Click a converted source to read its formatted document (a
   real table for spreadsheets, a model's transcription for images). **Markdown**
   switches to the raw text. The reader also offers an ad hoc, single-document
   **Project to JSON** against any schema — batch-projecting several sources at
   once lives in Projections and Graph instead, scoped to the schema each is for.
2. **Schemas** — a schema is a JSON Schema object plus the system prompt used to
   extract it. Every schema is **literature**-kind by default (**experiment**-kind
   ones belong to the Experiment workflow); exactly one literature schema can be
   **the graph schema** at a time, chosen in Graph — every other one is a
   **custom** schema, run and reviewed in Projections. Reachable from both
   workflows, since they share the same schema collection. Reuse one before
   creating a near-duplicate.
3. **Projections** — custom, domain-specific structured extraction (e.g.
   materials properties) that never reaches the published graph. Pick a custom
   schema, select one or more already-converted sources and **Project N
   selected**; the resulting projections list right below, each opening its
   validation, evidence and JSON on click.
4. **Graph** — the one pipeline that is always on: pick which literature schema
   is **the graph schema**, project sources against it (**Project all pending**,
   no manual selection needed), select the resulting projections and **Build
   draft**, then submit, reject or approve that draft — approving asks for an
   explicit confirmation and is the **only** thing that writes the knowledge
   graph. The published graph itself is drawn as a map below all of that: click
   an entity to read its type and properties beside it, double-click to traverse
   outward from it.

### Experiment

1. **Experiments** — drop files onto the strip under the toolbar (or **Choose
   files**): a photographed handwritten table, an Excel sheet, a PDF, a note. One
   experiment's files become one **upload**, created on the spot and named after
   its files; each file converts to markdown as soon as it lands (images through
   the vision engine). Uploads line up as chips below the strip; pick one to see
   its files, drop or **Add files** onto it, or edit its details — the strip's
   **New upload from files** starts another upload instead. Once every file is converted, **Extract
   records** opens a window to confirm the upload's name, description (units, which
   specimen a photo shows) and **experiment**-kind schema (choose one in the window,
   or **New schema** first: one JSON Schema object describes one specimen record;
   the schema is fixed once the upload has records). Confirming reads the whole
   upload as one document, projects it onto the schema and saves one draft record
   per specimen, each linked to the files it was read from — by starting the
   connected Lab assistant when one is connected, or else with one model call
   through the selected model. The records list below as folded rows; the toolbar's
   schema filter (or **All schemas**), the upload chips, a status filter and a
   search box (over names, values, file names and notes) narrow them, and a toggle
   shows the same records as a table with one column per schema field. Unfold a
   record to read its data as a table (nested lists and objects as sub-tables,
   missing required fields flagged), then **Edit** it in a form generated from the
   schema (typed inputs per field, a **JSON** toggle for the raw data) or ask the
   Lab assistant to in the conversation, and **Publish** — after an explicit
   confirmation — to mark it approved data. **Re-extract** offers to replace an
   upload's drafts and leaves its published records alone; **Unpublish** reopens a
   record; **Remove upload** deletes its records and keeps its files out of
   Literature. An experiment record never touches a draft, a review or the
   published graph.
2. **Insights** — the toolbar's schema selector sets the **scope**: one experiment
   schema, or **All schemas (cross-schema)** — every experiment schema that has
   published records, read together. The scope's **published** records (optionally
   one group's) are read as one table per schema together with **Statistics** the
   card computes over them — per schema and numeric field the count, mean, std,
   min, median and max, outliers beyond |z| > 2.5 (click one to open the record),
   pairwise correlations with |r| ≥ 0.6, value counts for categorical fields; and,
   across schemas, how many specimens appear in more than one schema (records
   matched by name — the sample id) and the correlations between fields of
   *different* schemas over them, so "crystallinity rises with sintering
   temperature" can be seen even when synthesis and XRD live in separate schemas.
   **Extract insights** hands all of that to the connected Lab assistant (or makes
   one model call) and saves what comes back as **draft insights**: each has a kind
   — `anomaly`, `rule`, `trend`, `correlation`, `threshold` or `note` — a title, a
   statement, the fields it is about (`<schema name>.<field>` when it spans
   schemas), an optional confidence and its **evidence**: the records it rests on,
   each with a role (supports, contradicts, observed_in), shown as chips that jump
   to the record. An insight is listed under every schema it involves.
   A kind filter narrows both lists. **Edit** a draft (or ask the Lab assistant to),
   **Delete** it, **Publish** it after an explicit confirmation — or **Publish all**
   under one — and it becomes part of the knowledge graph's **experiment layer**:
   one node per insight (typed by its kind), one `field` node per field it involves,
   one `experiment_record` node per record it cites, and `involves`, `supported_by`,
   `contradicted_by` and `observed_in` edges between them. Published insights are
   drawn as a map below, click one to read its statement and evidence; **Unpublish**
   removes exactly what publishing wrote and drops record and field nodes nothing
   cites any more. A draft whose cited record was since unpublished is marked
   *unpublished* on the chip and cannot be published until its evidence is fixed.
   Re-extracting with **Replace this schema's draft insights** replaces the drafts
   and never a published insight.
3. **Schemas** — the same schema list as Literature's, so an experiment-kind
   schema created here shows up there too, and back.

   A worked example lives in [`examples/solid_electrolyte/`](examples/solid_electrolyte):
   `schema.json` (a solid-electrolyte production batch: feedstock, yield,
   conductivities, moisture, XRD, particle size) and a simulated
   `production_log_2026-07.txt` to upload and extract.

The group selector — in Sources, or the toolbar for other sections — narrows every
section to one group at a time, or shows everything across every group. A source
belongs to exactly one group for its whole life; markdown, projections, drafts,
experiment uploads and records and the entities and relations a draft publishes all
trace back to the group their source came from, so a knowledge base with several
document sets stays as easy to browse group by group as it is to see as a whole. A
file uploaded into an experiment upload stays in the Experiment workflow: Literature's
source list leaves it out (`sources` with `workflow="experiment"` or `"all"` lists it).

Model output is never presented as fact. A projection and a draft are candidates; a
published experiment record is a person's checked judgment, not a graph fact; a
published insight is a person's checked statement *about* records, in the graph's
experiment layer and traceable to every record it cites; a published fact revision
is what an approval produces, and every published entity traces back through its
draft and projections to the page it came from.

## Where the model call happens

Projection needs a configured model, and plugins never see API credentials. The
**Project to JSON** button posts to `POST /api/knowledge/{card_id}/project` in the
OAW backend, which reads the prompt from the card, resolves the model connection,
calls the provider, and writes the result back through the card's own
`save_projection` action. Provider response bodies are never returned to the
browser — they can carry request credentials.

A connected Agent takes the other path: `knowledge_projection_prompt` returns the
prompt and markdown, the Agent produces the JSON, and `knowledge_save_projection`
stores it. Either way the projection is validated and evidence-linked the same way.

**Extract records** has two paths over the same two actions.
`experiment_extract_prompt` returns the schema, the uploader's notes and every
file's markdown joined under `## File:` headings (windowed with `offset`/`has_more`,
so a long upload is read in pages); `experiment_save` stores the records produced,
each validated against the schema and evidence-linked to the files it cites. With a
**Lab assistant** connected, the card asks the host to start it (`host.runAgent`)
with a delimited `EXPERIMENT EXTRACTION REQUEST` naming the upload, and the agent
does the work through those two tools, paging through the document itself; the card
only watches the agent until it is idle again. Without one, `POST
/api/knowledge/{card_id}/extract` pages through the prompt in the backend, makes one
JSON-mode call to the selected model and writes the result back through
`experiment_save`. Either way the card never saves a record itself, and
`knowledge_experiment_update` is how a connected agent edits a draft when asked to
in conversation.

**Extract insights** is the same shape a third time. `insight_prompt` takes a
scope — one `schema_id`, several `schema_ids`, or neither for every experiment
schema with published records — and returns each schema with its statistics, the
cross-schema statistics, the insights already published, and the records as one
markdown table per schema with a `record_id` column (windowed); `insight_save`
takes the same scope and stores the insights produced, refusing any whose evidence
is not a published record of those schemas in that group. With a Lab assistant
connected the card starts it with an `INSIGHT EXTRACTION REQUEST`; without one,
`POST /api/knowledge/{card_id}/insights` makes one JSON-mode call and writes back
through `insight_save`. Publishing — `insight_review` — is the one write to the graph
from the Experiment workflow, and it has no tool name on any transport.

Outside OAW, `kb project` and `kb insights extract` are the other callers of those
same prompt builders, using a model from your own shell (`KB_MODEL_BASE_URL`,
`KB_MODEL_API_KEY`, `KB_MODEL`). The standalone service never holds model
credentials and never calls a provider.

## Deploying it as a locked application

The card declares a `NodeDeploymentDefinition` (Plugin API 1.21+, see
[plugin deployment](../../docs/plugin-deployment.md)), so a saved Lab Manager
Legion can be [published](../../docs/deployment.md) as a locked, password-protected
application the same way a Conversation or a Text card can. Every business action a
person uses to drive the pipeline publishes: upload, convert (alone or batched),
organize sources into groups, search, project, choose the graph schema, build a
draft, create an experiment upload and add its files, review, publish or unpublish
its records, extract and publish insights over them, and submit, reject or approve
a draft — approving a draft and publishing an insight are still the only things
that write the graph. The **Settings**
section never publishes: the collection name, PDF engine and MinerU URL stay
engineering-only and the section does not even mount in a deployed release,
regardless of whether its pane stays visible in the published layout.

A deployment mounts no live model picker (`docs/deployment.md`: "it does not mount
... model settings"), so **Project to JSON**, **Extract records** and **Extract
insights** there always use one model chosen ahead of time: set **Default model for
deployment** in the engineering Settings section before publishing. `POST
/api/knowledge/{card_id}/project`, `.../extract` and `.../insights` are
engineering-only routes; a deployment reaches the same underlying calls through
`POST /api/runtime-app/workspace/knowledge/{card_id}/project`, `.../extract` and
`.../insights` instead, each gated on the release granting both halves of the
pipeline it drives. A release
carries no agent edges, so extraction there is always the one-call path.

See [`examples/knowledge-legion-deploy`](../../examples/knowledge-legion-deploy/README.md)
for a runnable end-to-end example — it also installs this plugin and its declared
dependencies into `backend/.venv` if they are missing
before publishing and serving a release.

## Running it without OAW

The pipeline does not import OAW at all — only `plugin.py` and `lifecycle.py` do,
and neither is loaded outside the backend. So the same code runs as a small
service with an HTTP API, an MCP server for an agent harness, and a `kb` CLI:

```sh
python -m venv .venv && .venv/bin/pip install -e 'plugins/knowledge_base[service]'
```

A **store** is a directory holding one `knowledge.db` plus a small settings file;
it defaults to `$KB_SERVICE_STORE`, else `~/.local/share/oaw-knowledge`. One store
can hold many **collections**, named per call (`--collection`, or the URL path) —
this is the same unit the OAW card calls a collection, and each one can in turn hold
several **groups** (`kb groups`), the same source-organizing unit the card's group
selector manages. A file belongs to exactly one group; a fresh collection starts
with a single unnamed default group, so nothing about groups is required to use
the store the way earlier versions did.

The whole loop in a terminal, no browser and no backend:

```sh
export KB_SERVICE_STORE=~/kb
kb ingest paper.pdf                       # uploads it; conversion is a separate step
kb process                                # converts every unconverted source, in a batch
kb sources                                # source id, record id, engine used
kb schema add --file process-schema.json  # name, definition, system_prompt
kb project --schema SCHEMA_ID --source SOURCE_ID   # your model, your credentials
kb draft create --projection PROJECTION_ID
kb submit DRAFT_ID --revision 1
kb approve DRAFT_ID --revision 1          # the only command that writes the graph
kb graph
```

`kb ingest` also accepts several files at once and, in process, waits for their
batch conversion the same way it always waited for one — pass `--group GROUP_ID`
to file them into a named group instead of the default one, and `kb groups create
--name Alloys` to make one first.

`kb tools` prints the agent manifest; `kb --help` lists the rest (`jobs --watch`,
`markdown --out`, `prompt`, `save-projection`, `projections`, `settings`, `groups`,
`process`). Insights have their own family:

```sh
kb insights stats [--schema ID ...]               # the statistics, no model involved;
                                                  # no --schema: every schema, with cross-schema stats
kb insights extract [--schema ID ...] [--replace]  # your model proposes draft insights over that scope
kb insights list [--schema ID] [--kind anomaly] [--status draft] [--query moisture]
kb insights publish INSIGHT_ID [...]              # writes the graph's experiment layer
kb insights unpublish INSIGHT_ID                  # removes exactly what publish wrote
kb graph --layer experiment                       # or --layer literature
```

### As a service

```sh
export KB_SERVICE_TOKEN=$(openssl rand -hex 16)
kb serve --store ~/kb            # 127.0.0.1:8931
curl -H "Authorization: Bearer $KB_SERVICE_TOKEN" \
     -d '{}' http://127.0.0.1:8931/v1/collections/default/overview
```

`POST /v1/collections/{collection}/{operation}` takes the action's arguments as the
body, for every operation the card has, because both read one table. `GET /v1/tools`
returns the manifest and `GET /v1/health` is the only unauthenticated route.
Binding anything other than loopback without a token is refused.

**Writing the graph is not reachable over the network.** `review` with
`operation=approve` and `insight_review` with `operation=publish` return 403 unless
`KB_ADMIN_TOKEN` is set on the service *and* presented, and even then the first call
returns `confirmation_required` until the request repeats with `?confirm=true`. Out
of the box, publishing a fact or an insight happens on the canvas or at the host's
own terminal — nowhere else.

Point the CLI at a running service with `--service URL` (or `KB_SERVICE_URL`) and
it proxies instead of opening the store itself.

### For an agent harness (MCP)

```jsonc
{"command": "kb", "args": ["mcp", "--store", "/home/you/kb", "--collection", "default"]}
```

The server exposes the same twenty tools with the same names and schemas an OAW
Agent sees, on stdio. `ingest`, `process`, `groups`, `settings`, `review`, the
experiment upload/publish actions and `insight_review` are absent, exactly as they
are absent from every relationship in OAW.

### The two modes coexist — one writer per store

A card and a service are separate stores by default: the card's store is its node
storage directory inside the OAW profile, the service's is `--store`. Both may run
at once.

What must not happen is **two processes writing one store**: each opens MKB's job
thread, and two of them race the same job rows in one SQLite file. `kb serve`
writes `.kb-service.lock` (pid, url) into the store; an in-process `kb` command on
a locked store refuses and prints the `--service` URL to use instead. The lock is
removed on shutdown and ignored if its pid is gone.

## Tests

```sh
cd plugins/knowledge_base && pytest    # pipeline, service and the layering guard
```

That suite needs no OAW on the path — `test_layering.py` asserts as much by
importing the core in a clean interpreter and failing if `open_agent_world` or
`backend` appears in `sys.modules`. The host seam (registration, the error
conversion, create/delete) is covered by
`backend/tests/test_knowledge_base_plugin.py`.

## Agent connections

| Relationship | Tools and permissions |
| --- | --- |
| Knowledge read | `knowledge_overview`, `knowledge_sources`, `knowledge_markdown`, `knowledge_document_search`, `knowledge_schemas`, `knowledge_projections`, `knowledge_experiments`, `knowledge_experiment_bundles`, `knowledge_insights`, `knowledge_graph`, `knowledge_jobs` |
| Knowledge extract | Read tools plus `knowledge_projection_prompt`, `knowledge_save_projection`, `knowledge_draft` |
| Knowledge experiment | Read tools plus `knowledge_experiment_extract_prompt`, `knowledge_experiment_save`, `knowledge_experiment_update`, `knowledge_insight_prompt`, `knowledge_insight_save`, `knowledge_insight_update` |

Uploading raw data (`ingest`, `experiment_ingest`), converting it (`process`),
managing groups (`groups`) and experiment uploads (`experiment_bundle_manage`),
changing card settings (`settings`), reviewing a draft (`review`) and publishing,
unpublishing or deleting an experiment record (`experiment_review`) or an insight
(`insight_review`) have **no capability kind at all**, so no relationship can reach them and no Agent can call
them. Publishing a fact, and publishing a record, stay human acts performed on the
canvas. Agents
cannot pass desktop confirmation arguments. `knowledge_overview` still reports every
group by name and count, and every read tool accepts an optional `group_id` to
narrow its answer to one group, so the Lab assistant can talk about how the base is
organized without being able to reorganize it.

Every Agent connected by a Knowledge read, extract or experiment edge is listed under
**Connected agents** in the engineering Settings section, each with its own model
picker — the same catalog of connections configured on the canvas. This exists
because the Agent's own card is not part of this workspace's layout, so there would
otherwise be no way to change which model it answers with without leaving the
Lab Manager workspace to find that card on the canvas. Changing it here edits
the Agent's own `model` field directly (`updateCard`, exactly what its own card's
picker would do) — it changes what that Agent uses everywhere, not just in this one
conversation.

## Persistence and failure behavior

Each card owns `knowledge.db` under its node storage directory. Sources,
artifacts, records, schemas, projections, evidence, drafts, review decisions,
fact revisions, the integration outbox and the knowledge graph itself all live in
that one file — the graph in plugin-owned `oaw_kg_entity` / `oaw_kg_relation`
tables, so it survives a restart. The search index (`oaw_kb_chunks_fts`, an FTS5
virtual table populated as each document converts), experiment uploads
(`oaw_kb_experiment_bundle` / `oaw_kb_experiment_bundle_source`) and experiment
records (`oaw_kb_experiment_record` / `oaw_kb_experiment_record_source`) and
insights (`oaw_kb_insight` / `oaw_kb_insight_record`) are plugin-owned tables in the
same file too, alongside MKB's own schema, not a second database. An insight
table from before cross-schema insights existed is rebuilt on first open so
`schema_id` may be null and `schema_ids` lists every schema an insight spans. The two graph
tables carry a `layer` column — `literature` for what an approved draft published,
`experiment` for a published insight and the record and field nodes it cites — so
one graph holds both and a query narrows to either. A database from before uploads existed is migrated on first open: its
records gain the new columns, `confirmed` becomes `published`, and the old
projection-keyed evidence table is folded into the source-keyed one. The
file uses WAL and a 30-second busy timeout because the job thread and resource
actions write concurrently. Groups are plain MKB collections in the same file, so
they persist and restart exactly the same way; opening an older `knowledge.db` for
the first time after upgrading adds the `group_id` and `layer` columns those two
tables need automatically, leaving every existing fact in place (tagged to no group
until the next approval touches it, and to the `literature` layer), and backfills
the search index for any record converted before it existed.

Deleting a card permanently removes `knowledge.db` and its WAL/SHM companions
through OAW's journaled lifecycle finalizer. Canvas undo, copy/paste and Legion
templates do not snapshot the file. Back up the profile while OAW is closed.

Uploads are capped at 32 MiB and results at 1 MiB, so long documents page through
`offset`/`limit` rather than arriving whole. Uploading a file only stores it —
processing is a separate, explicit batch step, alone or with others, so a large
drop of files never floods the job thread on its own (an experiment upload's files
are the exception: they only exist to be projected together, so each converts as it
lands). A conversion that no engine
can handle fails the job with the engine named, leaving the source in place to
retry after installing the engine. Interrupted jobs are recovered when the card
reopens.
