# Knowledge research, deployed

Publishes the **Knowledge research** Legion preset — a knowledge base card and a
read-only Librarian agent — as a locked, password-protected application, and serves
it. Unlike [`examples/deployed-workspace`](../deployed-workspace/README.md) this is
not a canned, key-free demo: it is the real `knowledge.base.research` preset, and
projecting a document into structured JSON genuinely calls a model once you
configure one.

## What it automates

The knowledge base card depends on two Python packages (`mat-know-base`,
`pymupdf4llm`) that the rest of the project does not install by default — see
[the plugin's own install note](../../plugins/knowledge_base/README.md#install-the-engines).
Running this script installs them into `backend/.venv` automatically (via
`uv sync --project backend --extra knowledge`, alongside whatever other extras are
already synced there) before it does anything else. If they are already
importable, that step is skipped and the run is fast.

## Run it

First-time setup is otherwise the same as any other part of the project: run
`bash scripts/setup.sh` (or `scripts/setup.ps1` on Windows) once, which builds the
frontend too. Then, from the repository root:

```sh
python examples/knowledge-legion-deploy/run.py
```

This installs the knowledge engines if needed, deploys the Knowledge research
preset into a fresh engineering profile, uploads and converts one sample document,
seeds one hand-written projection so Review and the Graph tab have something to
look at without a model call, builds and approves a draft from it, publishes a
release, and serves it at <http://127.0.0.1:38476>. The access password (unless
you rotate it) is **`oaw-knowledge-2026`**.

```sh
# A custom port, without opening a browser
python examples/knowledge-legion-deploy/run.py --port 38477 --no-open

# A second, independent copy — the first is never overwritten
python examples/knowledge-legion-deploy/run.py --data-root .open-agent-world/examples/knowledge-demo-2

# Only build the deployment data; do not start serving it
python examples/knowledge-legion-deploy/run.py --prepare-only
```

Stop the server with **Ctrl+C**; running the same command again reuses the same
deployment data (uploaded documents, conversion jobs, drafts and the published
graph all persist, exactly as [documented for the card](../../plugins/knowledge_base/README.md#persistence-and-failure-behavior)).

## Troubleshooting uploads and conversion

The deployment server's request and error log is at
`.open-agent-world/examples/knowledge-legion-deploy/runtime/logs/launcher.log`
(or `<data-root>/runtime/logs/launcher.log` with a custom data root). Follow it
with `tail -f .open-agent-world/examples/knowledge-legion-deploy/runtime/logs/launcher.log`.
An upload should produce a `POST .../resource/ingest` entry. Upload errors also
appear above the Knowledge base workspace; uploads do not create conversion jobs.
After uploading, select a source and press **Process**. The **Sources → Conversion
jobs** list shows each job's status; select a job to see its detailed progress and
error events. PDF files must be no larger than 32 MiB.

## Turning it into a working knowledge base

The sample document, schema and projection are there so the workspace is not empty
on first login, not to demonstrate model calls. To actually project new documents:

1. Sign in, open the card in the engineering profile under
   `.open-agent-world/examples/knowledge-legion-deploy/engineering` (or your
   `--data-root`), configure a model connection, and set **Default model for
   deployment** in the card's Settings section — a deployment has no live model
   picker, so "Project to JSON" there always uses this one, chosen ahead of time.
2. Publish a new release (`POST /api/deployments`, or the Publish application panel)
   and deploy it with `scripts/deploy.py`, the same as any other saved Legion — see
   the [deployment guide](../../docs/deployment.md).

## What is public and what stays locked

The knowledge base card declares a `NodeDeploymentDefinition` (see
[plugin.py](../../plugins/knowledge_base/src/oaw_knowledge_base/plugin.py) and the
[plugin deployment guide](../../docs/plugin-deployment.md)) that publishes every
business action a person needs to drive the pipeline: upload, convert (alone or in
a batch), organize sources into groups, project, build a draft, and submit/reject/
approve it — approving is still the only thing that writes the graph, exactly as
on the canvas. The collection name, PDF engine and MinerU URL never publish: the
card's Settings section is engineering-only and does not even mount in a deployed
release.

## Files

- `run.py` — installs the knowledge engines, deploys the preset, seeds sample data,
  publishes and serves it.
