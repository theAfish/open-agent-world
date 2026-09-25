# Knowledge base

An automatically discovered OAW plugin (`knowledge.base`). One card owns one SQL
file and carries documents through the whole research-knowledge pipeline:
raw data → markdown → structured JSON projection → draft graph → human review →
published knowledge graph.

This is a proof of concept built on the `mat_know_base` (MKB) Python SDK. It runs
the pipeline in process, stores every byte in one SQLite file, and needs no Docker,
no MinIO, no PostgreSQL and no object store. The plugin imports MKB; it never
modifies it.

The same package runs two ways. **Embedded**, as the card described below.
**Standalone**, as a small HTTP service, an MCP server and a `kb` CLI over the same
operations — see [Running it without OAW](#running-it-without-oaw) — so the
pipeline can be driven from a terminal or reached by another agent system with no
backend running.

## Install the engines

The plugin loader does not install plugin dependencies, so MKB and the PDF reader
go into the backend environment once (the standalone install is
[below](#running-it-without-oaw)):

```sh
backend/.venv/bin/pip install "mat-know-base @ /path/to/mat_know_base" pymupdf4llm
```

Without MKB the card loads but every action reports that `mat-know-base` is
missing. Without `pymupdf4llm` everything else still works and PDFs fall back to
the remote engine or fail with a named missing engine — text, markdown, CSV and
JSON never need either package.

For the optional remote PDF engine, set the token in the backend environment:

```sh
export OAW_MINERU_TOKEN=...
```

The token is read **only** from that environment variable. It is never stored in
card configuration, never sent to the frontend, and never reaches an Agent. The
card's **MinerU base URL** setting selects the service; it carries no credential.
The standalone service reads the same variable from its own environment.

## The Knowledge research formation

The card is most useful with someone to ask about it. The plugin registers a Legion
preset, **Knowledge research**, in the Legion deck: deploying it places a knowledge
base, a **Librarian** Agent and a Conversation, already wired and already laid out —
the conversation as a narrow sidebar, then Sources over Jobs, then markdown and
schemas over projections, review and the graph.

**You drive the pipeline; the Librarian only reads.** Uploading a PDF, running a
projection, building a draft and approving it are buttons in the workspace, in that
order. The Librarian holds **Knowledge read** and nothing else: it can tell you what
the base contains, read a converted document back to you and query the published
graph, but it cannot ingest, project, draft or approve. Nothing enters the graph
except by your click.

Saving your own knowledge Legion works too, but note what a copy is: the node type is
templateable while the database is not captured, so **a deployed copy is an empty
knowledge base** with the same name and wiring — no sources, no facts, and settings
back at their defaults. This is the same promise the card's deletion warning makes.

## Using the card

Open the **Knowledge base** pack in the card library, place a card, then open its
workspace.

1. **Sources** — add a PDF, markdown, text, CSV or JSON file up to 32 MiB.
   Conversion runs in the background on the card's own job thread, so the canvas
   stays responsive; the **Jobs** section shows progress and any failure.
2. **Markdown** — select a source to read the extracted markdown, choose a schema
   and a model, and **Project to JSON**.
3. **Schemas** — a schema is a JSON Schema object plus the system prompt used to
   extract it. Reuse one before creating a near-duplicate.
4. **Projections** — structured extractions, each linked by evidence back to the
   markdown artifact and the original file. Select one or more and **Build draft**.
5. **Review** — read the proposed graph, then submit, reject, or approve.
   Approving asks for an explicit confirmation and is the **only** thing that
   writes the knowledge graph.
6. **Graph** — the published graph, drawn as a map: click an entity to read its
   type and properties beside it, double-click to traverse outward from it. The
   name filter and the entity list stay beside the map.

Model output is never presented as fact. A projection and a draft are candidates;
a published fact revision is what an approval produces, and every published entity
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

Outside OAW, `kb project` is the third caller of that same prompt builder, using a
model from your own shell (`KB_MODEL_BASE_URL`, `KB_MODEL_API_KEY`, `KB_MODEL`).
The standalone service never holds model credentials and never calls a provider.

## Running it without OAW

The pipeline does not import OAW at all — only `plugin.py` and `lifecycle.py` do,
and neither is loaded outside the backend. So the same code runs as a small
service with an HTTP API, an MCP server for an agent harness, and a `kb` CLI:

```sh
python -m venv .venv && .venv/bin/pip install -e plugins/knowledge_base[service] \
    "mat-know-base @ /path/to/mat_know_base" pymupdf4llm
```

A **store** is a directory holding one `knowledge.db` plus a small settings file;
it defaults to `$KB_SERVICE_STORE`, else `~/.local/share/oaw-knowledge`. One store
can hold many **collections**, named per call (`--collection`, or the URL path).

The whole loop in a terminal, no browser and no backend:

```sh
export KB_SERVICE_STORE=~/kb
kb ingest paper.pdf                       # uploads and waits for the conversion
kb sources                                # source id, record id, engine used
kb schema add --file process-schema.json  # name, definition, system_prompt
kb project --schema SCHEMA_ID --source SOURCE_ID   # your model, your credentials
kb draft create --projection PROJECTION_ID
kb submit DRAFT_ID --revision 1
kb approve DRAFT_ID --revision 1          # the only command that writes the graph
kb graph
```

`kb tools` prints the agent manifest; `kb --help` lists the rest (`jobs --watch`,
`markdown --out`, `prompt`, `save-projection`, `projections`, `settings`).

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
| Knowledge read | `knowledge_overview`, `knowledge_sources`, `knowledge_markdown`, `knowledge_schemas`, `knowledge_projections`, `knowledge_graph`, `knowledge_jobs` |
| Knowledge extract | Read tools plus `knowledge_projection_prompt`, `knowledge_save_projection`, `knowledge_draft` |

Uploading raw data (`ingest`), changing card settings (`settings`) and reviewing a
draft (`review`) have **no capability kind at all**, so no relationship can reach
them and no Agent can call them. Publishing a fact stays a human act performed on
the canvas. Agents cannot pass desktop confirmation arguments.

## Persistence and failure behavior

Each card owns `knowledge.db` under its node storage directory. Sources,
artifacts, records, schemas, projections, evidence, drafts, review decisions,
fact revisions, the integration outbox and the knowledge graph itself all live in
that one file — the graph in plugin-owned `oaw_kg_entity` / `oaw_kg_relation`
tables, so it survives a restart. The file uses WAL and a 30-second busy timeout
because the job thread and resource actions write concurrently.

Deleting a card permanently removes `knowledge.db` and its WAL/SHM companions
through OAW's journaled lifecycle finalizer. Canvas undo, copy/paste and Legion
templates do not snapshot the file. Back up the profile while OAW is closed.

Uploads are capped at 32 MiB and results at 1 MiB, so long documents page through
`offset`/`limit` rather than arriving whole. A conversion that no engine can
handle fails the job with the engine named, leaving the source in place to retry
after installing the engine. Interrupted jobs are recovered when the card reopens.
