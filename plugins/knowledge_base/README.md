# Knowledge base

An automatically discovered OAW plugin (`knowledge.base`). One card owns one SQL
file and carries three related workflows over the same collection:

- **Literature** — raw data → markdown → full-text search, or → structured JSON
  projection → draft graph → human review → published knowledge graph.
- **Experiments** — several files describing one experiment (a spreadsheet, a
  photographed notebook page, a paper) → per-file structured projections → one
  merged, queryable experiment record, confirmed by a person.
- The knowledge graph is opt-in, not required by either workflow: search answers
  document-level questions and experiment records answer structured ones without
  ever touching a draft or a review.

This is a proof of concept built on the `mat_know_base` (MKB) Python SDK. It runs
the pipeline in process, stores every byte in one SQLite file, and needs no Docker,
no MinIO, no PostgreSQL and no object store. The plugin imports MKB as a fixed,
installed dependency; it never modifies it — full-text search, experiment records
and the multi-format converters below are all implemented in this plugin, in
`search_store.py`, `experiment_store.py` and `markdown.py`, alongside MKB's own
tables in the same SQLite file, the same way the knowledge graph already is.

The same package runs two ways. **Embedded**, as the card described below.
**Standalone**, as a small HTTP service, an MCP server and a `kb` CLI over the same
operations — see [Running it without OAW](#running-it-without-oaw) — so the
pipeline can be driven from a terminal or reached by another agent system with no
backend running.

## Install the engines

MKB, the PDF reader and the spreadsheet reader are declared in this plugin's
`pyproject.toml`, independently of the host dependencies. `scripts/setup.ps1` /
`scripts/setup.sh` install the bundled plugins and their dependencies automatically;
the desktop builder also includes them. To install only this plugin into an existing
backend environment:

```sh
uv pip install --python backend/.venv/bin/python -e plugins/knowledge_base
```

Without MKB the card loads but every action reports that `mat-know-base` is
missing. Without `pymupdf4llm` everything else still works and PDFs fall back to
the remote engine or fail with a named missing engine. Without `openpyxl`, `.xlsx`
uploads fail the same way — text, markdown, CSV, TSV and JSON never need either
package.

On Windows use `backend/.venv/Scripts/python.exe`. After manually running
`uv sync`, use `--inexact` to retain installed plugins, or rerun
`scripts/install-plugins.py` with the backend Python. Source discovery itself
never installs packages. Distributed `.oawpack` v1 files automatically provision
**Sandbox** requirements; their backend dependencies must already be satisfied
by the host. That is a different installation path from this bundled plugin.

For the optional remote PDF engine, open the card's **Settings**, set the
**MinerU base URL**, enter **MinerU token** and choose **Save token**. The token is
encrypted in the host credential store, scoped to this card. Leaving it blank
preserves the saved value; **Remove token** restores external fallback. Resolution
order is card Secret, global `OAW_MINERU_TOKEN` setting, then server environment:

```sh
export OAW_MINERU_TOKEN=...
```

For images (a photographed lab notebook, a chart, a gel image), configure a vision
model the same way — OCR cannot read handwriting, so this is the only image engine:

```sh
export OAW_VISION_API_KEY=...
export OAW_VISION_BASE_URL=https://api.openai.com/v1
export OAW_VISION_MODEL=gpt-4o-mini   # optional, this is the default
```

Vision credentials and the standalone service use the environment variables above.
Saved MinerU tokens are never returned to the frontend or stored in card state,
job parameters or Legion templates. Global Value settings remain ordinary values;
use the card's Secret field for encrypted storage. `OAW_MINERU_TOKEN` in global
settings is host-only and is not injected into Sandboxes.

## The Knowledge research formation

The card is most useful with someone to ask about it. The plugin registers a Legion
preset, **Knowledge research**, in the Legion deck: deploying it places a knowledge
base, a **Librarian** Agent and a Conversation, already wired and already laid out —
the card on one side with a rail for its two workflows, **Literature** and
**Experiment**, and the conversation beside it.

**You drive the pipeline; the Librarian only reads.** Uploading a file, converting
it, running a projection, building a draft, assembling an experiment record and
approving a draft are buttons in the workspace. The Librarian holds **Knowledge
read** and nothing else: it can tell you what the base contains, search or read a
converted document back to you, read experiment records and query the published
graph, but it cannot ingest, convert, project, draft, assemble or approve. Nothing
enters the graph, and no experiment record exists, except by your click.

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

1. **Experiments** — select one or more **experiment** projections (one per
   attached file, extracted in Sources against an experiment-kind schema) and
   **Assemble experiment record**: a model merges them into one structured record,
   flagging any field the files disagree on. Edit the merged JSON or **Confirm** it
   directly — an experiment record never touches a draft, a review or the
   published graph.
2. **Schemas** — the same schema list as Literature's, so an experiment-kind
   schema created here shows up there too, and back.

The group selector — in Sources, or the toolbar for other sections — narrows every
section to one group at a time, or shows everything across every group. A source
belongs to exactly one group for its whole life; markdown, projections, drafts,
experiment records and the entities and relations a draft publishes all trace back
to the group their source came from, so a knowledge base with several document sets
stays as easy to browse group by group as it is to see as a whole.

Model output is never presented as fact. A projection and a draft are candidates; a
confirmed experiment record is a person's checked judgment, not a graph fact; a
published fact revision is what an approval produces, and every published entity
traces back through its draft and projections to the page it came from.

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

**Assemble experiment record** works the same way, one route over: `POST
/api/knowledge/{card_id}/assemble` reads every selected projection's data through
`experiment_assemble_prompt`, resolves the model connection, calls the provider once
to merge them, and writes the result back through `experiment_save`. An Agent can
take the same two-step path directly with `knowledge_experiment_assemble_prompt` and
`knowledge_experiment_save`.

Outside OAW, `kb project` is the third caller of that same prompt builder, using a
model from your own shell (`KB_MODEL_BASE_URL`, `KB_MODEL_API_KEY`, `KB_MODEL`).
The standalone service never holds model credentials and never calls a provider.

## Deploying it as a locked application

The card declares a `NodeDeploymentDefinition` (Plugin API 1.21+, see
[plugin deployment](../../docs/plugin-deployment.md)), so a saved Knowledge research
Legion can be [published](../../docs/deployment.md) as a locked, password-protected
application the same way a Conversation or a Text card can. Every business action a
person uses to drive the pipeline publishes: upload, convert (alone or batched),
organize sources into groups, search, project, choose the graph schema, build a
draft, assemble or confirm an experiment record, and submit, reject or approve a
draft — approving is still the only thing that writes the graph. The **Settings**
section never publishes: the collection name, PDF engine and MinerU URL stay
engineering-only and the section does not even mount in a deployed release,
regardless of whether its pane stays visible in the published layout.

A deployment mounts no live model picker (`docs/deployment.md`: "it does not mount
... model settings"), so **Project to JSON** and **Assemble experiment record**
there always use one model chosen ahead of time: set **Default model for
deployment** in the engineering Settings section before publishing.
`POST /api/knowledge/{card_id}/project` and `.../assemble` are engineering-only
routes; a deployment reaches the same underlying calls through
`POST /api/runtime-app/workspace/knowledge/{card_id}/project` and `.../assemble`
instead, each gated on the release granting both halves of the pipeline it drives.

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
`process`).

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

**Approving a draft is not reachable over the network.** `review` with
`operation=approve` returns 403 unless `KB_ADMIN_TOKEN` is set on the service *and*
presented, and even then the first call returns `confirmation_required` until the
request repeats with `?confirm=true`. Out of the box, publishing a fact happens on
the canvas or at the host's own terminal — nowhere else.

Point the CLI at a running service with `--service URL` (or `KB_SERVICE_URL`) and
it proxies instead of opening the store itself.

### For an agent harness (MCP)

```jsonc
{"command": "kb", "args": ["mcp", "--store", "/home/you/kb", "--collection", "default"]}
```

The server exposes the same ten tools with the same names and schemas an OAW Agent
sees, on stdio. `ingest`, `settings` and `review` are absent, exactly as they are
absent from every relationship in OAW.

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
| Knowledge read | `knowledge_overview`, `knowledge_sources`, `knowledge_markdown`, `knowledge_document_search`, `knowledge_schemas`, `knowledge_projections`, `knowledge_experiments`, `knowledge_graph`, `knowledge_jobs` |
| Knowledge extract | Read tools plus `knowledge_projection_prompt`, `knowledge_save_projection`, `knowledge_draft`, `knowledge_experiment_assemble_prompt`, `knowledge_experiment_save`, `knowledge_experiment_update` |

Uploading raw data (`ingest`), converting it (`process`), managing groups
(`groups`), changing card settings (`settings`) and reviewing a draft (`review`)
have **no capability kind at all**, so no relationship can reach them and no Agent
can call them. Publishing a fact stays a human act performed on the canvas. Agents
cannot pass desktop confirmation arguments. `knowledge_overview` still reports every
group by name and count, and every read tool accepts an optional `group_id` to
narrow its answer to one group, so the Librarian can talk about how the base is
organized without being able to reorganize it.

Every Agent connected by a Knowledge read or Knowledge extract edge is listed under
**Connected agents** in the engineering Settings section, each with its own model
picker — the same catalog of connections configured on the canvas. This exists
because the Agent's own card is not part of this workspace's layout, so there would
otherwise be no way to change which model it answers with without leaving the
Knowledge research workspace to find that card on the canvas. Changing it here edits
the Agent's own `model` field directly (`updateCard`, exactly what its own card's
picker would do) — it changes what that Agent uses everywhere, not just in this one
conversation.

## Persistence and failure behavior

Each card owns `knowledge.db` under its node storage directory. Sources,
artifacts, records, schemas, projections, evidence, drafts, review decisions,
fact revisions, the integration outbox and the knowledge graph itself all live in
that one file — the graph in plugin-owned `oaw_kg_entity` / `oaw_kg_relation`
tables, so it survives a restart. The search index (`oaw_kb_chunks_fts`, an FTS5
virtual table populated as each document converts) and experiment records
(`oaw_kb_experiment_record` / `oaw_kb_experiment_record_evidence`) are plugin-owned
tables in the same file too, alongside MKB's own schema, not a second database. The
file uses WAL and a 30-second busy timeout because the job thread and resource
actions write concurrently. Groups are plain MKB collections in the same file, so
they persist and restart exactly the same way; opening an older `knowledge.db` for
the first time after upgrading adds the `group_id` column those two tables need
automatically, leaving every existing fact in place (tagged to no group until the
next approval touches it), and backfills the search index for any record converted
before it existed.

Deleting a card permanently removes `knowledge.db` and its WAL/SHM companions
through OAW's journaled lifecycle finalizer. Canvas undo, copy/paste and Legion
templates do not snapshot the file. Back up the profile while OAW is closed.

Uploads are capped at 32 MiB and results at 1 MiB, so long documents page through
`offset`/`limit` rather than arriving whole. Uploading a file only stores it —
processing is a separate, explicit batch step, alone or with others, so a large
drop of files never floods the job thread on its own. A conversion that no engine
can handle fails the job with the engine named, leaving the source in place to
retry after installing the engine. Interrupted jobs are recovered when the card
reopens.
