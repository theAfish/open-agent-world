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

Experiment extraction and Insight bridges retain their record validation,
source-evidence checks and human publication boundary.

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

There is no Knowledge Frame. Project results and graph views are readable directly;
publication of experiment records and Insights is a separate human decision.

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
