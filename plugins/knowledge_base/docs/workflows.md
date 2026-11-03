# Knowledge Base workflows

[Pack guide](../README.md)

## The Lab Manager formation

The Lab Manager preset places a Knowledge base, Knowledge assistant and Conversation
in one workspace. The assistant's research connection can import authorized
attachments, organize projects, convert files, configure Spaces, project results,
and prepare experiment record/Insight drafts. It can also inspect/query independently
connected database cards through their own grants.

A saved Legion copy starts with empty knowledge storage; copying layout and wiring
does not copy private project materials or results.

## Using the card

### Projects

A project is a stable bundle of related materials, independent of domain. It can be
a report and its appendices, an experiment with measurements and notes, or any other
set of related original files. Selecting a checkbox never creates another project
or extraction result.

**New project** is the one way in. Name a new project or pick an existing one, then add
files, a folder or ZIP/TAR archives; they all join that project and relative paths are
retained. Files can be removed from a project individually, and a project can be
deleted with its results (the knowledge graph is rebuilt). Removal is soft: the
original bytes stay as evidence, and results that used a removed file are marked out of
date rather than deleted.

Import starts conversion automatically. Each file reports pending, converting,
converted or failed; project readiness summarizes those states. Unsupported formats
remain available as original files and report conversion failure. Read the original
converted Markdown without configuring any Space. Search spans converted contents.

Same-path file updates produce new immutable sources and update the project's
active inventory. Earlier sources remain available as evidence for historical
results. Conversion outputs are reused across Spaces.

### Spaces

A Space holds extraction instructions, JSON Schema, output kind and configuration
version. The built-in Knowledge graph Space is maintained by the developers and is
read-only. For other needs choose a table/custom-JSON or graph starting point and edit
the definition for the desired domain. A Space consumes all converted original files in each
selected project directly. No Knowledge Frame or fixed research-literature schema
is inserted before projection.

Running A+B+C and later selecting A opens/reuses A's result. A currently running
result is reused. Changed material or Space configuration causes a new result
version; explicit Re-extract deliberately starts a new run. Each project retains
its own evidence and history under the Space.

### Results

Results show the project, Space/version, data and source evidence. Tables and graphs
are views over the returned structured JSON. Browse source files to verify claims.
Show history to inspect earlier runs or corrections.

Review is optional and acts on an existing result. Post-process accepts a requested
transformation, such as unit normalization, using the original evidence and current
schema. Manual edits and model corrections save a new revision with a note. None of
these actions is a mandatory stage between extraction and viewing its results.

### Experiments

Experiment files are processed together against a user-defined experiment schema.
The output consists of structured JSON records, one per specimen, sample, run or
case as appropriate. Records can be inspected in a table or edited as JSON/forms.
They remain drafts until a person confirms publication.

Insights are a subsequent operation over published JSON records, not a second
reading of the original files, and they are an Agent's work: connect a Lab assistant
Agent (with tools for calculation, such as a Sandbox) and use **Extract insights**.
The card computes no statistics and a deployment without a connected Agent cannot
extract them, though it can still review, edit and publish drafts. The Agent reads all
published records as JSON, looks for connections of any kind, does its own arithmetic
and proposes evidence-linked insights. Each Insight cites record IDs, which in turn
cite original source files, and notes how its numbers were obtained so a reviewer can
reproduce them. Publishing an Insight writes it and its evidence into the experiment
graph. The model cannot publish records or Insights itself.

## Deploying it as a locked application

Projects, Spaces, Results, Experiments, Schemas and Insights can be placed as whole
card or detached workspace sections. Published surfaces expose only their declared
resource operations and response fields. Model credentials and engineering Settings
remain private. Set a default model before publishing if the deployment will extract
without a connected Agent. Runtime projection/review bridges require all constituent
read and write grants; publishing a read action alone cannot grant result writes.
