"""The operation table: one row per thing anyone can ask a knowledge base to do.

Every front door reads this table — the OAW plugin turns each row into a capability
and a resource action, the HTTP service turns it into a route, the MCP server turns it
into a tool, and the CLI turns it into a subcommand. A row with no ``tool_name`` is
deliberately unreachable by any agent on any transport. Raw-byte upload, destructive
management, settings and publication stay outside the agent surface; explicit write
capabilities prepare selected sources and unpublished drafts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from . import actions, insights, knowledge


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
        """The request model's JSON schema minus the fields marked ``agent_hidden``
        (see ``actions.AGENT_HIDDEN``): knobs the UI and CLI pass that an agent must
        not be offered, so the tool surface stays one obvious path."""
        schema = self.model.model_json_schema()
        hidden = {name for name, field in self.model.model_fields.items()
                  if isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get("agent_hidden")}
        if hidden:
            schema["properties"] = {key: value for key, value in schema.get("properties", {}).items()
                                    if key not in hidden and not (isinstance(value, dict) and value.get("agent_hidden"))}
            if "required" in schema:
                schema["required"] = [key for key in schema["required"] if key not in hidden]
        return schema


OPERATIONS: tuple[Operation, ...] = (
    Operation("overview", actions.overview, actions.Overview, tool_name="knowledge_overview",
        description="Summarize this knowledge base: its groups, and source, record, schema, knowledge draft, entity and relation counts (optionally narrowed to one group_id), the configured markdown engines, and how many conversion jobs are running. Call this first to orient before any other knowledge tool."),
    Operation("sources", actions.sources, actions.Sources, tool_name="knowledge_sources",
        description="List uploaded sources with their markdown conversion status, record id and how many structured records were read from each. Use the returned source_id with knowledge_markdown, knowledge_extract_prompt and knowledge_record_extract_prompt."),
    Operation("markdown", actions.markdown, actions.Markdown, tool_name="knowledge_markdown",
        description="Read the markdown extracted from one source, by source_id or record_id. Long documents are windowed: use offset and limit and follow has_more to page through."),
    Operation("search", actions.search, actions.Search, tool_name="knowledge_document_search",
        description="Full-text search across every converted document's markdown. Returns ranked excerpts, each with the source filename and the heading it falls under — raw document text, not a verified fact. Use this to answer document-level questions across many sources at once instead of reading one document from the top with knowledge_markdown."),
    Operation("schemas", actions.schemas, actions.SchemaRead, tool_name="knowledge_schemas",
        description="List or read extraction schemas. A schema describes the fields to extract: kind=\"literature\" reads records from converted sources, kind=\"experiment\" reads experiment uploads. Reuse an existing schema when one fits. Creating or changing one requires knowledge_schema_manage."),
    Operation("knowledge_drafts", knowledge.knowledge_drafts, knowledge.KnowledgeDrafts,
        tool_name="knowledge_drafts",
        description="List or read knowledge drafts: the concept graph proposed from a set of literature sources — concepts (name, aliases, description) and the relations between them (a free predicate read as \"source predicate target\", an evidence_level 1-4 and a quoted snippet), each citing the source_ids it was read from. A draft is a candidate until a person publishes it into the knowledge graph's literature layer; a published one is what the base asserts about its literature."),
    Operation("knowledge_source_outline", knowledge.knowledge_source_outline, knowledge.KnowledgeSourceOutline,
        tool_name="knowledge_source_outline",
        description="The sections of one converted source (heading path, character offset and length). Call it first when extracting knowledge from a long document, then read one section at a time with knowledge_extract_prompt(section=...) instead of paging through the whole text."),
    Operation("knowledge_lookup", knowledge.knowledge_lookup, knowledge.KnowledgeLookup,
        tool_name="knowledge_lookup",
        description="Find concepts already named in a knowledge draft and in the published literature graph whose name or aliases contain any of the given terms, with the predicates used around them and the predicate frequencies of the published graph. Call it with the key terms of a section before naming its concepts, and reuse the names and predicates it returns so the new source connects to the existing graph instead of duplicating it."),
    Operation("knowledge_extract_prompt", knowledge.knowledge_extract_prompt, knowledge.KnowledgeExtractPrompt,
        tool_name="knowledge_extract_prompt",
        description="Get everything needed to name one source's concepts and relations for a knowledge draft: the fixed instruction, suggested predicates, the names and predicates already in the draft and the published graph that this text mentions (reuse them), and the document markdown — one section when section or heading is given (see knowledge_source_outline), else windowed (follow has_more with offset). Produce the concepts and relations, then submit them with knowledge_save; one call per section is fine, the draft merges them."),
    Operation("knowledge_save", knowledge.knowledge_save, knowledge.KnowledgeSave,
        tool_name="knowledge_save",
        description="Fold the concepts and relations you read from one source (or one section of it) into its knowledge draft. The source must be one of the draft's; concepts merge by name and aliases union, so a concept another section or source already named gains this one as evidence instead of a duplicate node; predicates are normalized to snake_case. Pass replace=true only on the first save when asked to re-extract the whole draft from scratch. Never publishes: that is the person's click."),
    Operation("knowledge_audit_prompt", knowledge.knowledge_audit_prompt, knowledge.KnowledgeAuditPrompt,
        tool_name="knowledge_audit_prompt",
        description="Get everything needed to review one knowledge draft before a person publishes it: the review instruction, findings the card computed (predicate frequencies, look-alike concept names, isolated concepts, pairs related in both directions, contradicting predicates, draft names matching the published graph) and the draft's concepts and relations with their snippets. Decide on typed operations — merge_concepts, rename_predicate, add_alias, delete_relation, delete_concept, set_evidence, flag — each with a one-line reason, then submit them with knowledge_audit_apply. Read a source section with knowledge_extract_prompt when a merge or deletion needs checking against the text."),
    Operation("knowledge_audit_apply", knowledge.knowledge_audit_apply, knowledge.KnowledgeAuditApply,
        tool_name="knowledge_audit_apply",
        description="Apply review operations to a knowledge draft and record them in its review log. Merged concepts become aliases of the kept one and their relations follow; renamed predicates re-key their relations; operations naming nothing in the draft are skipped and logged. Never publishes."),
    Operation("knowledge_update", knowledge.knowledge_update, knowledge.KnowledgeUpdate,
        tool_name="knowledge_update",
        description="Edit one knowledge draft when a person asks: rename it, or replace its concept and relation lists to remove a wrong element, merge two concepts (give one the other's name as an alias and drop it), or correct a name, predicate or evidence level. Refuses published drafts."),
    Operation("record_extract_prompt", actions.record_extract_prompt, actions.RecordExtractPrompt,
        tool_name="knowledge_record_extract_prompt",
        description="Get everything needed to project one converted literature source onto a kind=\"literature\" schema: the schema's system prompt and JSON Schema definition, the instruction and the document markdown (windowed: follow has_more with offset). Produce one record per specimen, material or case the document reports, then submit them with knowledge_record_save."),
    Operation("record_save", actions.record_save, actions.RecordSave,
        tool_name="knowledge_record_save",
        description="Store the records projected from one literature source as drafts, each validated against the schema and linked to that source. An empty list is allowed when the document reports nothing for the schema. Pass replace_drafts=true when re-extracting so that source's earlier drafts for this schema are replaced; published records are never touched."),
    Operation("experiment_bundles", actions.experiment_bundles, actions.ExperimentBundles,
        tool_name="knowledge_experiment_bundles",
        description="List or read experiment uploads: one upload is a set of files (a photographed table, a spreadsheet, a note) describing specimens of one experiment, projected together onto one kind=\"experiment\" schema. Shows each file's conversion state and how many records the upload has produced."),
    Operation("experiment_extract_prompt", actions.experiment_extract_prompt, actions.ExperimentExtractPrompt,
        tool_name="knowledge_experiment_extract_prompt",
        description="Get everything needed to project one experiment upload onto its schema: the schema's system prompt and JSON Schema definition, the uploader's notes, and every file's markdown joined into one document (windowed: follow has_more with offset). Produce one record per specimen, then submit them with knowledge_experiment_save."),
    Operation("experiment_save", actions.experiment_save, actions.ExperimentSave,
        tool_name="knowledge_experiment_save",
        description="Store the records projected from one experiment upload as drafts, each validated against the schema and linked to the files it was read from. Pass replace_drafts=true when re-extracting so earlier drafts are replaced; published records are never touched. A draft is a candidate until a person publishes it."),
    Operation("experiments", actions.experiments, actions.Experiments,
        tool_name="knowledge_experiments",
        description="List or read structured records — specimen or material data extracted from an experiment upload or straight from a literature source, and reviewed by a person. Filter by schema, upload (bundle_id), source (source_id) or status: a published record is approved data; a draft is still being reviewed."),
    Operation("experiment_update", actions.experiment_update, actions.ExperimentUpdate,
        tool_name="knowledge_experiment_update",
        description="Edit one draft experiment record's name, data or note, when a person asks for the change. Refuses published records. Never publishes: that is the person's click."),
    Operation("insights", insights.insights, insights.Insights, tool_name="knowledge_insights",
        description="Read the insights of this knowledge base: meta-knowledge (anomaly, rule, trend, correlation, threshold, note) that was extracted from published experiment records — of one schema or across several — and reviewed by a person, each evidence-linked to the records it rests on. Use this whenever someone asks what the experiments show as a whole: any anomalies, what drives a property, which rules hold. Filter by schema_id, kind, status or a free-text query over titles and statements; a published insight is in the knowledge graph's experiment layer, a draft is still being reviewed. operation=\"stats\" returns deterministic statistics over published records — per schema the field distributions, outliers and correlations, and across schemas the correlations between fields of different schemas for specimens both describe — use it for numeric questions instead of estimating."),
    Operation("insight_prompt", insights.insight_prompt, insights.InsightPrompt,
        tool_name="knowledge_insight_prompt",
        description="Get everything needed to propose insights over published experiment records — one schema (schema_id), several (schema_ids) or, with neither, every schema that has published records: each schema's definition and statistics (distributions, outliers, correlations), the cross-schema correlations over specimens several schemas describe (records matched by name), the insights already published, and the records as one markdown table per schema with a record_id column (windowed: follow has_more with offset). Produce grounded insights citing record_ids as evidence, then submit them with knowledge_insight_save using the same scope."),
    Operation("insight_save", insights.insight_save, insights.InsightSave,
        tool_name="knowledge_insight_save",
        description="Store proposed insights as drafts for a scope of schemas (the same schema_id / schema_ids / none given to knowledge_insight_prompt), each evidence-linked to the published records it cites; an insight citing a record outside those schemas and that group is refused. Pass replace_drafts=true when re-extracting so that scope's earlier drafts are replaced; published insights are never touched. A draft is a candidate until a person publishes it."),
    Operation("insight_update", insights.insight_update, insights.InsightUpdate,
        tool_name="knowledge_insight_update",
        description="Edit one draft insight's kind, title, statement, fields, confidence, evidence or note, when a person asks for the change. Refuses published insights. Never publishes: that is the person's click."),
    Operation("graph", actions.graph, actions.GraphQuery, tool_name="knowledge_graph",
        description="Search the published knowledge graph — the only place published knowledge lives. Call it FIRST for any question about what this base knows, before knowledge_document_search. Pass query=<the question or key terms in plain words>; or names=[X] / names=[X, Y] for exact concept names (aliases and acronyms count; spacing and punctuation do not). Every form runs the same search: by words and, when the card has an embedding model, by meaning — the result's semantic field is true when meaning-based matching ran, false with semantic_error when it could not (then the answer is word-based only; say so). You get the best-fitting concepts (matched=true), the relations that fit the question ranked best first — each readable as source_name, predicate, target_name with the statement the source makes, evidence_level and snippet — and, when two or more concepts are named, connections: the shortest relation chains between them. The relations are the published claims; a concept's description is background only. operation=\"connect\" with names=[X, Y] returns only the chains between two concepts (max_depth hops, default 3) or connected=false; operation=\"traverse\" with an entity_id walks outward. An empty result or a concept with no relations means the base holds no published claim — report that rather than inferring one. Only human-published knowledge drafts and insights appear here; layer=\"literature\" narrows to what was published from documents, layer=\"experiment\" to published insights with the records and fields they cite."),
    Operation("graph_index", actions.graph_index, actions.GraphIndexStatus, tool_name="knowledge_graph_index",
        description="Read the semantic index status: which embedding model the card uses (enabled=false when none), how many graph elements are embedded, pending or stale, and the last error. Call this when asked whether meaning-based search is available or why a search said semantic=false. Building the index requires knowledge_graph_index_build."),
    Operation("jobs", actions.jobs, actions.Jobs, tool_name="knowledge_jobs",
        description="Check markdown conversion jobs and their progress events. Poll this after an upload until the job reports COMPLETED before reading its markdown."),
    Operation("process_selected", actions.process_selected, actions.ProcessSelected,
        tool_name="knowledge_process",
        description="Convert the selected source_ids to readable markdown using this base's configured converter. Obtain IDs from knowledge_sources or an attachment import. Already converted sources are skipped and running jobs are reused. Check knowledge_jobs until COMPLETED, then read knowledge_markdown; no schema or graph is needed to read a paper."),
    Operation("schema_manage", actions.schema_manage, actions.SchemaManage,
        tool_name="knowledge_schema_manage",
        description="Create or update an extraction schema when the user wants structured records. First check knowledge_schemas for a suitable existing schema. Supply a JSON Schema object describing the desired fields and a system_prompt telling the extractor how to fill them from evidence; choose kind=literature for papers or kind=experiment for experiment uploads. This does not extract or publish records."),
    Operation("knowledge_draft_open", knowledge.knowledge_draft_open, knowledge.KnowledgeDraftOpen,
        tool_name="knowledge_draft_open",
        description="Open a literature knowledge draft over selected converted source_ids, or reuse an unpublished draft over the same sources and title. Use this only when the user wants concepts and relations prepared for the graph; simply reading a paper needs no draft. Next use knowledge_extract_prompt and knowledge_save, then leave the draft for human review and publication."),
    Operation("experiment_bundle_prepare", actions.experiment_bundle_prepare, actions.ExperimentBundlePrepare,
        tool_name="knowledge_experiment_bundle_prepare",
        description="Create or update an experiment upload's name, notes and schema. For create, choose an experiment schema with knowledge_schemas or knowledge_schema_manage and give a name; an equivalent unpublished upload is reused. Import its files next, wait for conversion, then use knowledge_experiment_extract_prompt and knowledge_experiment_save. Updates refuse uploads with published records. This cannot delete, publish or change extraction run state."),
    Operation("graph_index_build", actions.graph_index_build, actions.GraphIndexBuild,
        tool_name="knowledge_graph_index_build",
        description="Build missing or stale embeddings for the published graph using the configured embedding model. Does not change the model or publish knowledge. Read knowledge_graph_index first to check availability and progress."),
    # No tool_name below this line: human-only, on every transport.
    Operation("ingest", actions.ingest, actions.Ingest,
        description="Upload one raw file into a group. It stays unconverted until the Process action runs its conversion job."),
    Operation("process", actions.process_sources, actions.ProcessSources,
        description="Convert one or more uploaded sources to markdown in a batch. With no source_ids, converts every unconverted source, optionally narrowed to one group."),
    Operation("groups", actions.groups, actions.Groups,
        description="List, create, rename or delete the groups sources are filed into. Every source belongs to exactly one group; deleting a non-empty group is refused."),
    Operation("settings", actions.update_settings, actions.Settings,
        description="Read or change this knowledge base's collection name, PDF engine and MinerU base URL."),
    Operation("experiment_bundle_manage", actions.experiment_bundle_manage, actions.ExperimentBundleManage,
        description="Create, rename, annotate, mark the extraction state of, or delete one experiment upload."),
    Operation("experiment_ingest", actions.experiment_ingest, actions.ExperimentIngest,
        description="Upload one raw file into an experiment upload and queue its markdown conversion at once."),
    Operation("experiment_review", actions.experiment_review, actions.ExperimentReview,
        description="Publish, unpublish or delete one experiment record. Publishing asks for confirmation and marks the record as approved data."),
    Operation("insight_review", insights.insight_review, insights.InsightReview,
        description="Publish, unpublish or delete insights. Publishing asks for confirmation and writes the insight, the records it cites and the fields it is about into the knowledge graph's experiment layer; unpublishing removes exactly that."),
    Operation("knowledge_draft_manage", knowledge.knowledge_draft_manage, knowledge.KnowledgeDraftManage,
        description="Open a knowledge draft over a set of converted sources, change its title or source set, or delete it."),
    Operation("knowledge_graph_audit_prompt", knowledge.knowledge_graph_audit_prompt, knowledge.KnowledgeGraphAuditPrompt,
        description="Findings and the union of every published knowledge draft, for a review of the published literature layer as a whole."),
    Operation("knowledge_graph_audit_apply", knowledge.knowledge_graph_audit_apply, knowledge.KnowledgeGraphAuditApply,
        description="Apply review operations to every published knowledge draft and rebuild the literature layer from them. Asks for confirmation: it rewrites published knowledge."),
    Operation("knowledge_review", knowledge.knowledge_review, knowledge.KnowledgeReview,
        description="Publish or unpublish one knowledge draft. Publishing asks for confirmation and writes the draft's entities and relations into the knowledge graph's literature layer; unpublishing removes what no other published draft also asserts."),
)

BY_NAME: dict[str, Operation] = {operation.name: operation for operation in OPERATIONS}
AGENT_OPERATIONS: tuple[Operation, ...] = tuple(item for item in OPERATIONS if item.agent)

READ_ACTIONS = ("overview", "sources", "markdown", "search", "schemas", "knowledge_drafts",
               "knowledge_source_outline", "knowledge_lookup",
               "experiments", "experiment_bundles", "insights", "graph", "graph_index", "jobs")
# Existing narrow extract and experiment relationships retain their write scopes.
EXTRACT_ACTIONS = READ_ACTIONS + ("knowledge_extract_prompt", "knowledge_save", "knowledge_update",
                                  "knowledge_audit_prompt", "knowledge_audit_apply",
                                  "record_extract_prompt", "record_save")
# The experiment pipeline: project an upload into records, propose insights over the
# published ones, and edit either kind of draft on request. Publishing is deliberately
# absent — neither review action has a tool name on any transport.
EXPERIMENT_ACTIONS = READ_ACTIONS + ("experiment_extract_prompt", "experiment_save",
                                     "experiment_update", "insight_prompt", "insight_save",
                                     "insight_update")
RESEARCH_ACTIONS = tuple(dict.fromkeys(EXTRACT_ACTIONS + EXPERIMENT_ACTIONS + (
    "process_selected", "schema_manage", "knowledge_draft_open", "experiment_bundle_prepare",
    "graph_index_build",
)))


def tool_manifest() -> list[dict]:
    """The agent-callable surface, in the shape MCP and tool-listing endpoints want."""
    return [{"name": item.tool_name, "operation": item.name, "description": item.description,
             "input_schema": item.input_schema()} for item in AGENT_OPERATIONS]
