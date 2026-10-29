# Run Knowledge Base without OAW

[Pack guide](../README.md)

The OAW card, standalone HTTP service, MCP and `kb` CLI share the same project,
conversion, Space and experiment operations. Install service dependencies with:

```sh
python -m pip install -e 'plugins/knowledge_base[service]'
```

A store contains `knowledge.db` and `settings.json`. Select it with `--store` or
`KB_SERVICE_STORE`; the default is `~/.local/share/oaw-knowledge`. One store is one
knowledge base containing stable projects. Projects can hold reports, original
data, experimental files, notes and supplementary material.

## Import and convert

Imports preview project boundaries before committing. Folder paths are preserved;
ZIP and TAR expansion enforces path, entry and size limits.

```sh
export KB_SERVICE_STORE=~/kb
kb import ./reports --grouping depth --depth 2
# Inspect the projects and token, then commit the same preview:
kb import --preview-token TOKEN --grouping depth --depth 2 --commit
kb projects
kb projects PROJECT_ID
```

Grouping choices are `top`, `leaf`, `single`, `depth` and `files`; `depth 0`
also makes one project per file. Committing starts conversion by default. Use
`--no-convert` to defer it, then `kb convert PROJECT_ID ...`. Local CLI commands
wait for jobs before closing their worker; remote commands return job IDs.
Follow them with `kb jobs --job JOB_ID --watch`.

Add supplementary files to an existing project with:

```sh
kb import supplement.csv --grouping single --project PROJECT_ID --commit
```

Repeating a committed preview token returns the same receipt. Identical content
at the same project path reuses its source. Replacing a path activates a new
immutable source revision while retaining earlier evidence.
`kb markdown --source SOURCE_ID` reads converted Markdown.

## Direct Markdown projection

A Space defines a name, instructions, JSON Schema and output kind (`table` or
`graph`). For example, `report-space.json`:

```json
{
  "name": "Reported measurements",
  "kind": "table",
  "system_prompt": "Extract measurements explicitly supported by the project files.",
  "definition": {
    "type": "object",
    "required": ["records"],
    "properties": {"records": {"type": "array", "items": {"type": "object"}}}
  }
}
```

```sh
kb spaces add --file report-space.json
kb spaces list
kb project PROJECT_ID ANOTHER_PROJECT_ID --space SPACE_ID
kb call projections
kb review PROJECTION_ID --note "Verify values against the attached report"
```

Model calls use `KB_MODEL`, `KB_MODEL_API_KEY` and `KB_MODEL_BASE_URL`
(an OpenAI-compatible endpoint). Credentials stay in the CLI process.
The service never receives them.

Projection reads the original converted Markdown of all current project files;
there is no intermediate knowledge frame. Subset selection reuses unchanged
completed results and running work. `--rerun` explicitly creates a new version.
Results carry project, Space version and source provenance. Review is optional
and preserves the earlier result in history. Results require no publication.

`kb call OPERATION --file request.json` accepts any operation's JSON request.
For example, `{"operation":"get","projection_id":"..."}` reads a projection;
`include_history` on a projection listing includes earlier versions.

## Experimental records and insights

The experiment chain remains:
`project files → custom-schema JSON records → insights over published records`.

```sh
kb schema add --file experiment-schema.json
kb call experiment_bundle_prepare --file bundle.json
kb call experiment_ingest --file upload.json
kb jobs --job JOB_ID --watch
kb records extract --bundle BUNDLE_ID
kb records list --schema SCHEMA_ID
kb records publish RECORD_ID
kb insights stats --schema SCHEMA_ID
kb insights extract --schema SCHEMA_ID
kb insights publish INSIGHT_ID
kb graph
```

`bundle.json` supplies `schema_id`, `name` and optionally `group_id` (the project
ID). `upload.json` supplies `bundle_id`, `filename`, `media_type` and
`content_base64`. Schema files contain `name`, `definition` and `system_prompt`.
Records and insights support inspection, explicit publication and unpublication.
Use `--project PROJECT_ID` to scope listings and analysis. Insights cite records;
records cite the original files.

## HTTP and MCP

```sh
export KB_SERVICE_TOKEN=your-service-token
kb serve --store ~/kb
curl -H "Authorization: Bearer $KB_SERVICE_TOKEN" \
     -H 'Content-Type: application/json' -d '{}' \
     http://127.0.0.1:8931/v1/overview
```

`POST /v1/{operation}` takes the operation's JSON arguments. `GET /v1/projects`
lists projects; `GET /v1/tools` supplies the agent manifest. Only
`GET /v1/health` is unauthenticated. Non-loopback serving requires a token.

Publishing experimental records or insights requires a separate `KB_ADMIN_TOKEN`
to be configured and presented. An admin must also send `?confirm=true` after
receiving `confirmation_required`. A service token cannot bypass this gate with
that query parameter. Space projection results are usable immediately.

The CLI uses a running service with `--service URL` or `KB_SERVICE_URL`.
Configure MCP with:

```json
{"command":"kb","args":["mcp","--store","/home/you/kb"]}
```

MCP exposes the same project, Space, conversion, projection, revision and
experiment tools as OAW. Raw upload bytes, settings and human publication are
not agent tools. OAW's attachment import additionally uses independently
authorized host artifact references.

One process owns a store's job worker. `kb serve` writes `.kb-service.lock`;
local commands refuse a live server's store and direct callers to `--service`.
Shutdown removes the lock. Cards and standalone services use separate stores
by default.

## Focused validation

From `plugins/knowledge_base`, run `pytest tests/test_projects.py tests/test_spaces.py
tests/test_service.py tests/test_cli.py`. `test_layering.py` checks that standalone
core imports do not load OAW. Host registration and authorization are tested in
`backend/tests/test_knowledge_base_plugin.py`.
