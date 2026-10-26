# Knowledge base

An automatically discovered OAW plugin (`knowledge.base`). One card owns one SQL
file and carries three related workflows over the same collection:

- **Literature** — raw data → markdown → full-text search; then either
  **Knowledge**: pick some converted sources, open a **knowledge draft** over them,
  and a model — a connected Agent or a few calls per source — reads each section by
  section, consults what the draft and the published graph already name, and names
  the concepts the source is about and the relations it states between them (free
  predicates, an evidence level and a quote each; no graph schema to author) → a
  model **reviews** the draft against findings the card computes → a person edits
  it → **publishes** it into the graph's **literature layer**; or **Records**: read structured data out
  of sources with a schema you write (say, the ionic conductivity of every solid
  electrolyte a paper reports) → publish the records → extract **insights** over
  them, exactly as the Experiment workflow does over its own.
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
  **experiment layer**, beside the literature knowledge. Asking the Lab assistant
  "any anomalies in July?" or "what drives yield?" reads those published insights.
- The knowledge graph is opt-in, not required by either workflow: search answers
  document-level questions and published records answer structured ones without
  ever touching a draft. What reaches the graph is a published knowledge draft from
  Literature and a published insight from either side — never a record on its own.

This is a proof of concept built on the `mat_know_base` (MKB) Python SDK. It runs
the pipeline in process, stores every byte in one SQLite file, and needs no Docker,
no MinIO, no PostgreSQL and no object store. The plugin imports MKB as a fixed,
installed dependency; it never modifies it — full-text search, knowledge drafts,
records, insights and the multi-format converters below are all implemented in this
plugin, in `search_store.py`, `knowledge_store.py`, `experiment_store.py`,
`insight_store.py`, `insight_stats.py` and `markdown.py`, alongside MKB's own tables
in the same SQLite file, the same way the knowledge graph already is.

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
only when you ask.** Uploading a file, converting it, opening a knowledge draft,
extracting it, publishing it and publishing a record are buttons in the workspace.
The Lab assistant holds **Knowledge experiment**: every read tool — it can tell you
what the base contains, search or read a converted document back to you, read
knowledge drafts, records and insights (and the statistics behind them) and query
the published graph — plus the experiment half: it is what the Experiment workflow's
**Extract records** and **Extract insights** buttons start when it is connected, and
the one you ask in the conversation to fix a draft record or a draft insight. It
cannot ingest, convert, write a knowledge draft or publish anything. Nothing enters
the graph, and no record or insight is published, except by your click. Connect a
second Agent with **Knowledge extract** to hand it the literature half — naming a
source's concepts into a knowledge draft you opened, reading records out of a source
with a schema — or with **Knowledge read** for a strictly read-only one beside it.

Saving your own knowledge Legion works too, but note what a copy is: the node type is
templateable while the database is not captured, so **a deployed copy is an empty
knowledge base** with the same name and wiring — no sources, no groups, no graph,
and settings back at their defaults. This is the same promise the card's deletion warning
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
   switches to the raw text.
2. **Knowledge** — the concept graph of your literature, with no schema to write.
   **New draft** picks one or more converted sources and opens a **knowledge
   draft** over them; **Extract** reads each source a few sections at a time —
   through a connected Agent holding **Knowledge extract** when there is one, else
   one model call per chunk of sections (a short document is one call) — and names
   the concepts it is about and the relations it states between them. Nodes are
   **concepts only** (a material, a phenomenon, a method, a property — never a bare
   value or a figure number), each with **aliases** for its other spellings; a
   relation is a statement as first-class as a concept: a **free predicate** in
   the active voice (`improves`, `caused_by`, `measured_by` …, normalised to
   snake_case), a one-sentence **statement** of what the document claims about the
   pair, with the values and conditions that matter, MKB's **evidence level** (1
   causal … 4 inferred) and the **snippet** that states it. Predicates that only
   say a link exists (`related_to`, `associated_with` …) are flagged by the review,
   which gives each such relation the specific verb its statement supports. Concepts are keyed by
   name, so a concept two papers both name becomes one node with both as evidence;
   before each chunk the model is handed the names and predicates already in the
   draft and the published concepts the text mentions, so it reuses them. **Review
   draft** then has a model — the Agent, or one call — go over the draft against
   findings the card computes (predicate frequencies, look-alike names, isolated
   concepts, pairs related both ways, contradicting predicates, names the published
   graph already uses) and propose typed operations: merge concepts (the dropped
   names become aliases), rename a predicate, add an alias, delete a relation or a
   metadata-only concept, or flag something for you. They are applied to the draft
   at once and listed in its **review log**; `set_predicate` retypes one relation
   from its statement. **Edit** the concept and relation
   tables (rename, alias, change a predicate or evidence level, remove, add), then
   **Publish** — after an explicit confirmation, the **only** thing that writes
   the graph's literature layer. Below the drafts, **Review graph** runs the same
   review over the whole published layer: the model's proposed operations are
   shown first and, once you confirm, applied to every published draft that
   asserts the affected elements, after which the layer is rebuilt from the
   drafts — so what the graph holds is always exactly what its drafts assert.
   **Unpublish** removes exactly what the draft wrote, except elements another
   published draft also asserts. The published layer is drawn as a map below:
   click a concept to read its description, aliases, sources and relations;
   switch the side list to **Relations** (or click a relation line in a concept's
   panel) to read a relation on the same footing — its statement, predicate,
   evidence level, snippet, sources and both endpoints; double-click a concept to
   traverse outward. **Replace** empties a draft and reads every source again.
3. **Records** — structured data read out of sources with a schema of your own.
   **Schemas** here are **literature**-kind (the Experiment workflow has its own);
   a schema is a JSON Schema object plus the system prompt that explains it — e.g.
   one record per solid electrolyte, with its ionic conductivity and the
   temperature it was measured at. Pick a schema, select converted sources and
   **Extract**: one model call (or Agent run) per source produces zero or more
   draft records, each validated against the schema and linked to the source it
   came from. Rows unfold to a table; a draft edits in place as a form or JSON;
   **Publish** marks it approved data. **Insights** at the bottom of the tab are
   the same meta-knowledge extraction the Experiment workflow has, scoped to the
   literature schemas: statistics the card computes over the published records,
   draft insights a model proposes, and publication into the graph's experiment
   layer.

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
belongs to exactly one group for its whole life; markdown, knowledge drafts,
records, experiment uploads and the entities and relations a draft publishes all
trace back to the group their source came from, so a knowledge base with several
document sets stays as easy to browse group by group as it is to see as a whole. A
file uploaded into an experiment upload stays in the Experiment workflow: Literature's
source list leaves it out (`sources` with `workflow="experiment"` or `"all"` lists it).

Model output is never presented as fact. A draft of any kind is a candidate; a
published record is a person's checked judgment, not a graph element; a published
insight is a person's checked statement *about* records, in the graph's experiment
layer and traceable to every record it cites; a published knowledge draft is what
the literature layer holds, and every entity and relation in it carries the
`source_ids` it was read from.

## Where the model call happens

Every extraction needs a configured model, and plugins never see API credentials.
Each Extract button therefore either starts a connected Agent that does the work
through the plugin's own tools, or posts to a small bridge route in the OAW backend
that reads the prompt from the card, resolves the model connection, calls the
provider and writes the result back through the card's own save action. Provider
response bodies are never returned to the browser — they can carry request
credentials — and the card never writes an extraction result itself.

**Extract knowledge** is `knowledge_source_outline` → (`knowledge_lookup` →
`knowledge_extract_prompt` → `knowledge_save`) per section or group of sections,
for each source of the draft — a port of MKB's agent workflow (list headings, read a
section, search the existing graph, save incrementally) as plugin tools. The outline
returns each section as a character window; the prompt takes `section` (or
`heading`) and carries the instruction and suggested predicates
(`knowledge_prompt.py`), the names and predicates already in the draft, the published
concepts the text mentions, and that section's markdown; the save normalises the
reply — predicates to snake_case, endpoints that name nothing dropped, aliases
unioned — and merges it into the draft by content-keyed id (`concept:<slug>`,
`<source>|<predicate>|<target>`). With an Agent holding **Knowledge extract**
connected, the card starts it with a `KNOWLEDGE EXTRACTION REQUEST`; without one,
`POST /api/knowledge/{card_id}/knowledge` reads the outline, groups consecutive
sections into ~40k-character chunks and makes one call per chunk in the backend.
**Review draft** is `knowledge_audit_prompt` → `knowledge_audit_apply`
(`knowledge_audit.py`): the prompt carries the findings the card computes and the
draft; the apply takes typed operations (`merge_concepts`, `rename_predicate`,
`add_alias`, `delete_relation`, `delete_concept`, `set_evidence`, `flag`), applies
them through the same normalisation and appends them to the draft's `review_log`
(`set_predicate` retypes one relation and may correct its statement).
The bridge route is `POST /api/knowledge/{card_id}/audit`; an Agent can read a
source section before deciding on a merge. On the engineering canvas the three
knowledge bridge routes stream NDJSON — one `progress` line per section chunk or
review stage, then a `done` or `error` line — which the card shows as a progress
bar; the deployment routes return plain JSON. **Review graph** is
`knowledge_graph_audit_prompt` → `POST /api/knowledge/{card_id}/graph-audit`
(proposes only) → `knowledge_graph_audit_apply`, which asks for confirmation like
publishing does, applies the operations to every published draft in scope, and
rebuilds the literature layer from all published drafts; neither action has a tool
name, as rewriting published knowledge is a person's decision. `knowledge_update` is how a connected
agent edits a draft when asked to; `knowledge_draft_manage` (open, retitle, delete)
and `knowledge_review` (publish, unpublish) have no tool name on any transport.
Publishing a concept another published draft already wrote unions its aliases and
sources with the node's. Databases from before this model are re-keyed once on open
(`knowledge_migration.py`): typed nodes with one name collapse onto one concept, and
old relation types become predicates.

**Extract records** from literature is `record_extract_prompt` → `record_save`, once
per selected source: the schema, the instruction and the source's markdown in; zero
or more records out, each validated against the schema and linked to that source.
The bridge route is `POST /api/knowledge/{card_id}/records`; the Agent path uses the
same two tools (`knowledge_record_extract_prompt`, `knowledge_record_save`).

**Extract records** from an experiment upload has the same two paths over its own two
actions.**Extract insights** is the same shape a third time. `insight_prompt` takes a
scope — one `schema_id`, several `schema_ids`, or neither for every experiment
schema with published records — and returns each schema with its statistics, the
cross-schema statistics, the insights already published, and the records as one
markdown table per schema with a `record_id` column (windowed); `insight_save`
takes the same scope and stores the insights produced, refusing any whose evidence
is not a published record of those schemas in that group. With a Lab assistant
connected the card starts it with an `INSIGHT EXTRACTION REQUEST`; without one,
`POST /api/knowledge/{card_id}/insights` makes one JSON-mode call and writes back
through `insight_save`. Publishing — `insight_review` — is the one write to the graph
from insights, and it has no tool name on any transport. The scope's open form
takes every schema of one `schema_kind` (experiment by default; the Records tab
passes `literature`), so each section's insights stay over its own records.

Outside OAW, `kb knowledge extract`, `kb records extract` and `kb insights extract`
are the other callers of those same prompt builders, using a model from your own
shell (`KB_MODEL_BASE_URL`, `KB_MODEL_API_KEY`, `KB_MODEL`). The standalone service
never holds model credentials and never calls a provider.

## Deploying it as a locked application

The card declares a `NodeDeploymentDefinition` (Plugin API 1.21+, see
[plugin deployment](../../docs/plugin-deployment.md)), so a saved Lab Manager
Legion can be [published](../../docs/deployment.md) as a locked, password-protected
application the same way a Conversation or a Text card can. Every business action a
person uses to drive the pipeline publishes: upload, convert (alone or batched),
organize sources into groups, search, open a knowledge draft and extract, edit,
publish or unpublish it, read records out of sources with a schema, create an
experiment upload and add its files, review, publish or unpublish records, and
extract and publish insights over them — publishing a knowledge draft and
publishing an insight are still the only things that write the graph. The **Settings**
section never publishes: the collection name, PDF engine and MinerU URL stay
engineering-only and the section does not even mount in a deployed release,
regardless of whether its pane stays visible in the published layout.

A deployment mounts no live model picker (`docs/deployment.md`: "it does not mount
... model settings"), so every **Extract** button there uses one model chosen ahead
of time: set **Default model for deployment** in the engineering Settings section
before publishing. `POST /api/knowledge/{card_id}/knowledge`, `.../records`,
`.../extract` and `.../insights` are engineering-only routes; a deployment reaches
the same underlying calls through `POST
/api/runtime-app/workspace/knowledge/{card_id}/knowledge`, `.../records`,
`.../extract` and `.../insights` instead, each gated on the release granting both
halves of the pipeline it drives. A release carries no agent edges, so extraction
there is always the one-call path.

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
kb knowledge open --source SOURCE_ID      # a draft over one or more converted sources
kb knowledge extract DRAFT_ID             # your model, your credentials: one call per source (no chunking or review offline)
kb knowledge show DRAFT_ID                # the concepts and relations, each with its sources
kb knowledge publish DRAFT_ID             # the only command that writes the literature layer
kb graph --layer literature
```

Records read out of sources with a schema of your own are the other family:

```sh
kb schema add --file conductivity-schema.json   # name, definition, system_prompt
kb records extract --schema SCHEMA_ID --source SOURCE_ID [--replace]
kb records list --schema SCHEMA_ID
kb records publish RECORD_ID
```

`kb ingest` also accepts several files at once and, in process, waits for their
batch conversion the same way it always waited for one — pass `--group GROUP_ID`
to file them into a named group instead of the default one, and `kb groups create
--name Alloys` to make one first.

`kb tools` prints the agent manifest; `kb --help` lists the rest (`jobs --watch`,
`markdown --out`, `knowledge unpublish|delete`, `records show|unpublish|delete`,
`settings`, `groups`, `process`). Insights have their own family:

```sh
kb insights stats [--schema ID ...]               # the statistics, no model involved; no --schema:
                                                  # every experiment schema (--schema-kind literature for
                                                  # the Records tab's), with cross-schema stats
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

**Writing the graph is not reachable over the network.** `knowledge_review`, `knowledge_graph_audit_apply` and
`insight_review` with `operation=publish` return 403 unless `KB_ADMIN_TOKEN` is set
on the service *and* presented, and even then the first call returns
`confirmation_required` until the request repeats with `?confirm=true`. Out of the
box, publishing a knowledge draft or an insight happens on the canvas or at the
host's own terminal — nowhere else.

Point the CLI at a running service with `--service URL` (or `KB_SERVICE_URL`) and
it proxies instead of opening the store itself.

### For an agent harness (MCP)

```jsonc
{"command": "kb", "args": ["mcp", "--store", "/home/you/kb", "--collection", "default"]}
```

The server exposes the same tools with the same names and schemas an OAW Agent
sees, on stdio. `ingest`, `process`, `groups`, `settings`, `knowledge_draft_manage`,
`knowledge_review`, the experiment upload/publish actions and `insight_review` are
absent, exactly as they are absent from every relationship in OAW.

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
| Knowledge read | `knowledge_overview`, `knowledge_sources`, `knowledge_markdown`, `knowledge_document_search`, `knowledge_schemas`, `knowledge_drafts`, `knowledge_source_outline`, `knowledge_lookup`, `knowledge_experiments`, `knowledge_experiment_bundles`, `knowledge_insights`, `knowledge_graph`, `knowledge_jobs` |
| Knowledge extract | Read tools plus `knowledge_extract_prompt`, `knowledge_save`, `knowledge_update`, `knowledge_audit_prompt`, `knowledge_audit_apply`, `knowledge_record_extract_prompt`, `knowledge_record_save` |
| Knowledge experiment | Read tools plus `knowledge_experiment_extract_prompt`, `knowledge_experiment_save`, `knowledge_experiment_update`, `knowledge_insight_prompt`, `knowledge_insight_save`, `knowledge_insight_update` |

`knowledge_graph` answers "what does the base say about X (and Y)" and is the first
tool the Lab assistant calls for any question about the base. It has **one search
path**: whatever is passed — `query=<question>`, `names=[...]` (name, alias or acronym;
spacing and punctuation aside) or, from the UI and CLI, `name_contains` /
`relation_contains` — is searched the same way: by words (name and acronym match,
statement text) and, when the card has an embedding model, by meaning, fused with
reciprocal-rank fusion. The result holds the best-fitting concepts (`matched: true`),
the relations ranked best first — each readable as `source_name → predicate →
target_name` with its statement, evidence level and snippet (the relations are the
published claims; a concept's description is background) — and `connections`: the
shortest relation chains between any two concepts the question names.
`operation="connect"` with `names=[X, Y]` returns only those chains (`max_depth`,
default 3) or `connected: false`; `traverse` walks outward from an entity id. The
agent's tool schema exposes only `query` and `names`, so no lexical-only knob can be
picked by mistake; the graph map's name filter passes `semantic: false` and stays
lexical.

Meaning-based matching is on when the card's **Settings → Embedding model for graph
search** names an OpenAI-compatible embedding model (a connection from OAW's Models &
connections that serves `/embeddings`). Every search then reports `semantic: true`, or
`semantic: false` plus `semantic_error` when the model could not be used — the answer
is still the word-based one. Concept and relation vectors live in the card's SQLite
file (`oaw_kb_vectors`) and are brought up to date on each search (only changed
elements are embedded); **Settings → Semantic index** shows coverage and the last error
and can build the index up front, and `knowledge_graph_index` reports the same to the
assistant. A hit must stand out from the rest of the graph (two standard deviations
above the mean cosine) *and* clear an absolute floor (0.45, tuned on qwen3
text-embedding), so an unrelated question matches nothing. The graph text is sent to
the chosen endpoint. Empty means off, and a chat default model is never used for this.

The Lab assistant's instruction is copied into the agent when the Lab Manager
formation is created, so an existing formation keeps its old guidance: recreate it, or
paste the current instruction from `preset.py` into the agent's system instruction.

Uploading raw data (`ingest`, `experiment_ingest`), converting it (`process`),
managing groups (`groups`), knowledge drafts (`knowledge_draft_manage`) and
experiment uploads (`experiment_bundle_manage`), changing card settings
(`settings`) and publishing, unpublishing or deleting a knowledge draft
(`knowledge_review`), reviewing the published graph (`knowledge_graph_audit_prompt`,
`knowledge_graph_audit_apply`), a record (`experiment_review`) or an insight
(`insight_review`) have **no capability kind at all**, so no relationship can reach
them and no Agent can call them. Publishing anything stays a human act performed on
the canvas. Agents
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
artifacts, markdown records, schemas, evidence, the integration outbox and the
knowledge graph itself all live in that one file — the graph in plugin-owned
`oaw_kg_entity` / `oaw_kg_relation` tables, so it survives a restart. The search
index (`oaw_kb_chunks_fts`, an FTS5 virtual table populated as each document
converts), knowledge drafts (`oaw_kb_knowledge_draft` /
`oaw_kb_knowledge_draft_source`), experiment uploads (`oaw_kb_experiment_bundle` /
`oaw_kb_experiment_bundle_source`), records from either side
(`oaw_kb_experiment_record` / `oaw_kb_experiment_record_source`; a literature
record has no `bundle_id`) and insights (`oaw_kb_insight` / `oaw_kb_insight_record`)
are plugin-owned tables in the same file too, alongside MKB's own schema, not a
second database. An insight table from before cross-schema insights existed is
rebuilt on first open so `schema_id` may be null and `schema_ids` lists every schema
an insight spans. The two graph tables carry a `layer` column — `literature` for
what a published knowledge draft wrote, `experiment` for a published insight and
the record and field nodes it cites — so one graph holds both and a query narrows
to either. Projections, MKB drafts and facts written by earlier versions stay in
the file untouched and unreachable; entities they published remain in the
`literature` layer and on the Knowledge map. A database from before uploads existed is migrated on first open: its
records gain the new columns, `confirmed` becomes `published`, and the old
projection-keyed evidence table is folded into the source-keyed one. The
file uses WAL and a 30-second busy timeout because the job thread and resource
actions write concurrently. Groups are plain MKB collections in the same file, so
they persist and restart exactly the same way; opening an older `knowledge.db` for
the first time after upgrading adds the `group_id` and `layer` columns those two
tables need automatically, leaving every existing element in place (tagged to no
group until the next publication touches it, and to the `literature` layer), and
backfills the search index for any record converted before it existed.

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
