# Knowledge Base Agent and persistence contract

[Pack guide](../README.md)

## Where the model call happens

Connected Agents and the card's model bridge use the same validated plugin operations.
The host resolves model credentials; plugins and browsers never receive provider keys.
`POST /api/knowledge/{card_id}/project-space` streams progress and projects each newly
claimed project through its Space. `POST /api/knowledge/{card_id}/review-projection`
performs optional review or an instructed post-processing revision. Both also exist
under the published workspace router, gated by the complete operation grant set.

The model response envelope is `{ "data": <schema value> }`, allowing root objects
or arrays under JSON object response mode. `projection_save` receives the inner
value. The host follows Markdown windows and refuses to save success if the project
exceeds its direct-call input limit. An Agent can read the remaining windows through
its tools. Failed or interrupted direct calls mark their owned runs failed.

Experiment record extraction keeps its record validation, source-evidence checks and
human publication boundary. Insights have no model bridge: they are an Agent's work.
`knowledge_insight_prompt` returns the published records as raw JSON, paged
(`has_more` / `next_offset`; each page is sized to stay under the roughly 8 KB an Agent
sees inline), with each schema's definition and the Insights already
published; it computes nothing. The Agent explores freely, does every calculation with
its own tools (the Lab Manager preset connects a Sandbox for this), and saves drafts with
`knowledge_insight_save`, recording how it got each number in `properties.method`. The
plugin only checks that every cited record is a published record of the scope.

## Agent connections

- **Knowledge read:** projects/files, Markdown/search, Spaces, projections, experiment
  schemas/records, Insights, graph and job status. Read grants cannot create projects,
  change schemas, prepare runs or save/revise results.
- **Knowledge extract:** read plus project projection prepare/save/fail/revise.
- **Knowledge experiment:** read plus experiment record and Insight draft work.
- **Knowledge research:** both write workflows plus project/Space preparation,
  selected conversions, graph indexing and authorized attachment import.

The current operation table is authoritative for HTTP, MCP, CLI and OAW capability
schemas. Removed Knowledge Draft operations are not aliases to new operations.

### Attachment and project import

`knowledge_import_artifact` accepts authorized source, version_id, path and optional
project_id/bundle_id. The host verifies session/source access, bounds and hashes bytes,
then rechecks destination access before the private transfer. No model-provided bytes,
host filesystem paths or forged source receipts are accepted.

The default import retains the original as evidence without starting conversion or
calling a model. An Agent can read the file with its Harness tools and save records
that cite this original immediately. Set `auto_process=true` when Knowledge Base
Markdown conversion is wanted; then follow the returned job before reading Markdown.

An ordinary attachment creates/reuses a stable project unless a project is supplied.
An archive returns a staged project preview; the Agent inspects grouping then calls
`knowledge_project_import_commit`. Preview tokens belong to the card and actor;
commit receipts survive process restart. Archive traversal, links and expansion-limit
violations are rejected before files reach the project inventory.

### Direct Markdown projection

1. Read `knowledge_projects` and `knowledge_spaces`; create a suitable Space only if
   needed with `knowledge_space_manage`.
2. `knowledge_projection_prepare(project_ids, space_id)` returns created, running or
   reused entries. Execute only created entries. An explicit rerun retains history.
3. Read every `knowledge_projection_prompt` Markdown window for the prepared ID.
4. `knowledge_projection_save` validates JSON, source citations and the recorded
   Space/source snapshot; `knowledge_projection_fail` records an actual failure.
5. Optional `knowledge_projection_review_prompt` / `knowledge_projection_revise`
   verifies or transforms an existing result with an expected revision and note.

There is no Knowledge Frame. Project results are readable directly with
`knowledge_projections`. Saving or revising a graph-kind result also rebuilds its
concepts and relations in the card's one knowledge graph (merged across projects by
name, evidence kept per file), so `knowledge_graph` search, traversal and the optional
semantic index cover them. Publication of experiment records and Insights is a separate
human decision.

`knowledge_graph` is queried coarse to fine (the procedure is in the tool description):
`query` returns the best-matching concepts, each with `links` (its best relations for the
question, with each neighbour's description); `traverse` with several `names` expands the
neighbours worth a look; `evidence` with the two concept names (and `predicate`) returns one
relation's full statement and the original text around its supporting sentences. Original
documents are read only for the relations that decide the answer.

## Persistence and failure behavior

MKB collections are projects; sources retain original bytes and relative paths.
An explicit active file inventory selects the current source revision for each path.
Conversion artifacts and their text records are representations of the original
files, not an extracted semantic frame. The card's SQL store contains import receipts,
project inventory and versioned project/Space results alongside MKB repositories.

Projection claims serialize under a SQLite write transaction. Result identity uses
project and Space; recorded versions include schema version and converted-source
fingerprints. Repeated batch selection reuses unchanged work. Saves reject stale
source/configuration snapshots, invalid JSON Schema and invalid graph references.
Revisions require the current result and expected revision and preserve prior data.

Experiment record edits refuse published records. Insight evidence must belong to
the selected published record scope; neither model path grants publication. Deleting
the Knowledge base card removes its storage, including original files and results.
No legacy Draft migration or adapter runs when the card opens.

### Evidence chain

The experiment workflow keeps one chain of links: an uploaded original (MKB source,
identified by id **and** sha256) → the records read from it
(`oaw_kb_experiment_record_source`, which stores the hash the link was made against)
→ the Insights citing those records (`oaw_kb_insight_record`). Originals are never
deleted or rewritten; replacing a file is a new source. `knowledge_lineage` walks the
chain from any end — one `source_id`, `record_id` or `insight_id` — and returns the
originals (hash, whether the bytes are still available), the records (each link marked
`intact` when the retained bytes still match the hash) and the Insights. Records and
Insights removed since are reported as `missing`, not dropped.

### Where original bytes live

By default MKB keeps original files and conversion artifacts inside `knowledge.db`.
`OAW_KB_OBJECT_STORE_URL` moves them to `file:///absolute/root` or any S3-compatible
service (`s3://bucket?endpoint=https://host`; credentials through the host credential
store or `OAW_KB_OBJECT_STORE_ACCESS_KEY` / `OAW_KB_OBJECT_STORE_SECRET_KEY`). The
choice is recorded in the database on first open and the card refuses to open against
a different store afterwards; evidence is not migrated between stores automatically.
