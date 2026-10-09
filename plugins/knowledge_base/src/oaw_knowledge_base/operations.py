"""The operation table: one row per thing anyone can ask a knowledge base to do.

Every front door reads this table — the OAW plugin turns each row into a capability
and a resource action, the HTTP service turns it into a route, the MCP server turns it
into a tool, and the CLI turns it into a subcommand. A row with no ``tool_name`` is
deliberately unreachable by any agent on any transport: uploading raw data, converting
it in batch, managing groups, changing settings and reviewing a draft stay human acts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from . import actions


@dataclass(frozen=True)
class Operation:
    name: str
    handler: Callable[[Any, dict], dict]
    model: type[BaseModel]
    description: str
    tool_name: str | None = None

    @property
    def agent(self) -> bool:
        """Whether an agent may call this at all, on any transport."""
        return self.tool_name is not None

    def input_schema(self) -> dict:
        return self.model.model_json_schema()


OPERATIONS: tuple[Operation, ...] = (
    Operation("overview", actions.overview, actions.Overview, tool_name="knowledge_overview",
        description="Summarize this knowledge base: its groups, and source, record, schema, projection, draft, fact, entity and relation counts (optionally narrowed to one group_id), the configured markdown engines, and how many conversion jobs are running. Call this first to orient before any other knowledge tool."),
    Operation("sources", actions.sources, actions.Sources, tool_name="knowledge_sources",
        description="List uploaded sources with their markdown conversion status, record id and projection count. Use the returned source_id or record_id with knowledge_markdown and knowledge_projection_prompt."),
    Operation("markdown", actions.markdown, actions.Markdown, tool_name="knowledge_markdown",
        description="Read the markdown extracted from one source, by source_id or record_id. Long documents are windowed: use offset and limit and follow has_more to page through."),
    Operation("search", actions.search, actions.Search, tool_name="knowledge_document_search",
        description="Full-text search across every converted document's markdown. Returns ranked excerpts, each with the source filename and the heading it falls under — raw document text, not a verified fact. Use this to answer document-level questions across many sources at once instead of reading one document from the top with knowledge_markdown."),
    Operation("schemas", actions.schemas, actions.Schemas, tool_name="knowledge_schemas",
        description="List, read, create or update extraction schemas. A schema is a JSON Schema object plus the system prompt used to project markdown into structured JSON. Reuse an existing schema when one fits instead of creating a near-duplicate."),
    Operation("projection_prompt", actions.projection_prompt, actions.ProjectionPrompt,
        tool_name="knowledge_projection_prompt",
        description="Get everything needed to project one document into structured JSON: the schema's system prompt, JSON Schema definition, field descriptions and the document markdown. Produce JSON matching the definition, then submit it with knowledge_save_projection."),
    Operation("save_projection", actions.save_projection, actions.SaveProjection,
        tool_name="knowledge_save_projection",
        description="Store a structured JSON projection of one record against one schema. The result is validated and linked to the originating artifact as evidence. A projection is a candidate, not a fact: it does not enter the knowledge graph until a human approves a draft built from it."),
    Operation("projections", actions.projections, actions.Projections,
        tool_name="knowledge_projections",
        description="List stored projections, or read one in full with its evidence links. Use this to review extracted data before building a draft."),
    Operation("draft", actions.draft, actions.Draft, tool_name="knowledge_draft",
        description="List or read draft graphs, or create a new draft from selected projections. A draft is proposed knowledge awaiting human review; creating one never changes the published graph."),
    Operation("experiment_assemble_prompt", actions.experiment_assemble_prompt, actions.ExperimentAssemblePrompt,
        tool_name="knowledge_experiment_assemble_prompt",
        description="Get everything needed to merge several per-file extractions (projections against one kind=\"experiment\" schema) into one experiment record: the schema definition and each contributing projection's data and source. Produce the merged JSON, then submit it with knowledge_experiment_save."),
    Operation("experiment_save", actions.experiment_save, actions.ExperimentSave,
        tool_name="knowledge_experiment_save",
        description="Store a merged experiment record built from several projections, linking every contributing file as evidence. An experiment record never touches a draft, review or the published graph — it is confirmed or edited directly with knowledge_experiments."),
    Operation("experiments", actions.experiments, actions.Experiments,
        tool_name="knowledge_experiments",
        description="List or read experiment records — structured entities assembled from several uploaded files (e.g. a spreadsheet, a photographed notebook page and a paper) describing one experiment. Use this to compare experiments directly instead of the knowledge graph."),
    Operation("experiment_update", actions.experiment_update, actions.ExperimentUpdate,
        tool_name="knowledge_experiment_update",
        description="Confirm or hand-edit one experiment record. Never touches a draft, review or the published graph."),
    Operation("graph", actions.graph, actions.GraphQuery, tool_name="knowledge_graph",
        description="Query the published knowledge graph by entity type, relation type or name, or traverse outward from one entity. Only human-approved facts appear here."),
    Operation("jobs", actions.jobs, actions.Jobs, tool_name="knowledge_jobs",
        description="Check markdown conversion jobs and their progress events. Poll this after an upload until the job reports COMPLETED before reading its markdown."),
    # No tool_name below this line: human-only, on every transport.
    Operation("ingest", actions.ingest, actions.Ingest,
        description="Upload one raw file into a group. It stays unconverted until the Process action runs its conversion job."),
    Operation("process", actions.process_sources, actions.ProcessSources,
        description="Convert one or more uploaded sources to markdown in a batch. With no source_ids, converts every unconverted source, optionally narrowed to one group."),
    Operation("groups", actions.groups, actions.Groups,
        description="List, create, rename or delete the groups sources are filed into. Every source belongs to exactly one group; deleting a non-empty group is refused."),
    Operation("settings", actions.update_settings, actions.Settings,
        description="Read or change this knowledge base's collection name, PDF engine and MinerU base URL."),
    Operation("review", actions.review, actions.Review,
        description="Submit, reject or approve a draft. Approving publishes it as a fact revision and is the only thing that writes the knowledge graph."),
    Operation("graph_schema", actions.graph_schema, actions.GraphSchema,
        description="Get or set which literature-kind schema's projections build the published knowledge graph. A person's config choice, not something an agent should redirect."),
)

BY_NAME: dict[str, Operation] = {operation.name: operation for operation in OPERATIONS}
AGENT_OPERATIONS: tuple[Operation, ...] = tuple(item for item in OPERATIONS if item.agent)

READ_ACTIONS = ("overview", "sources", "markdown", "search", "schemas", "projections",
               "experiments", "graph", "jobs")
EXTRACT_ACTIONS = READ_ACTIONS + ("projection_prompt", "save_projection", "draft",
                                 "experiment_assemble_prompt", "experiment_save", "experiment_update")


def tool_manifest() -> list[dict]:
    """The agent-callable surface, in the shape MCP and tool-listing endpoints want."""
    return [{"name": item.tool_name, "operation": item.name, "description": item.description,
             "input_schema": item.input_schema()} for item in AGENT_OPERATIONS]
