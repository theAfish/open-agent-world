# Knowledge Base

A local knowledge workspace for reports, documents, datasets, supporting files and
experimental results. Each card owns one SQLite file and runs the MKB SDK in-process.

## Workflow

**Materials:** import files, folders or archives -> preview project boundaries ->
convert files to readable Markdown -> run a reusable Space -> inspect project results.
A project owns related original files. A Space defines a JSON Schema, instructions
and a table/custom-JSON or graph output. Projection reads the original Markdown
**directly**, with no Knowledge Frame intermediate layer.

Selecting projects only selects a batch. Unchanged results and running work are
reused; new source/configuration versions or an explicit rerun create result history.
Review, corrections and custom post-processing are optional result operations.

**Experiments:** original files -> custom-schema JSON records -> Insights grounded
in selected published records. Record and Insight publication remains a human action.

## Install

From the repository, run `scripts/install-plugins.py plugins/knowledge_base` with
the backend interpreter. Pack installation prepares the pinned MKB SDK and declared
converters. No PostgreSQL, Docker, MinIO or separate service is required by the card.
Use Settings to select PDF/image conversion engines and model connections. Model
credentials remain in the host's credential store, outside plugin data and prompts.

## First project

1. Drop files/archives or choose a folder in **Materials / Projects**.
2. Inspect the directory preview. Split projects by top folder, leaf folder, chosen
   depth, individual files, or one project. Rename projects before importing.
3. Conversion starts on import. Open a project to read files or add supporting data.
4. Create a **Space** using the desired schema and extraction instructions.
5. Select projects and run the Space. Open **Results** to view data, evidence,
   history, an optional review or custom post-processing.

A connected Knowledge assistant can carry out the same operations from conversation
attachments, including archive previews. Connected database cards retain their own
inspection/query tools and permissions.

## Guides

- [Card workflows and deployment](docs/workflows.md)
- [Agent tools, model bridge and persistence](docs/agent-contract.md)
- [Standalone HTTP, MCP, CLI and tests](docs/standalone.md)

This development redesign replaces the source-set Knowledge Draft model and its
routes. No legacy adapter or automatic conversion of old drafts is provided.
