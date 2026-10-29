"""OAW registration: the ``knowledge.base`` card over the shared operation table.

Everything the card can do lives in ``operations.py`` alongside the HTTP and MCP
front doors, so a tool behaves the same whichever one a caller came through. Rows
without a tool name get a resource action with no capability kind, which is what makes
them unreachable by any agent.
"""
import asyncio
from functools import wraps
from importlib.resources import files
from typing import Literal

from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, CapabilitySelector, DeploymentSurface, NodeDeploymentDefinition,
    NodeResourceAction, NodeTypeDefinition, PackDefinition, PluginAsset, PluginDescriptor,
    RelationshipDefinition, NodeDataSource, NodePresentation, WorkspaceLayoutMetadata,
)
from pydantic import BaseModel, ConfigDict, Field

from .errors import KnowledgeError, operator_message
from .lifecycle import KnowledgeLifecycle
from .operations import EXPERIMENT_ACTIONS, EXTRACT_ACTIONS, OPERATIONS, READ_ACTIONS, RESEARCH_ACTIONS
from . import actions, artifact_import
from .preset import definition as research_preset
from .data_source import DATA_SOURCE

PREFIX = "knowledge.base"

# Deployment cannot expose a live model picker — "it does not mount ... model
# settings" — so a deployed release projects the published fact through one model
# chosen ahead of time. Every top-level field a resource action's result may carry,
# named once so the whole-card surface and each workspace section can share it.
DEPLOYMENT_FIELDS = {
    "schema_manage": frozenset({"schema"}),
    'overview': frozenset(['active_jobs', 'collection', 'counts', 'engines', 'groups']),
    'sources': frozenset(['offset', 'sources']),
    'markdown': frozenset(['engine', 'filename', 'has_more', 'markdown', 'offset', 'record_id', 'total_characters']),
    'search': frozenset(['query', 'results']),
    'schemas': frozenset(['schema', 'schemas']),
    'experiment_bundles': frozenset(['bundle', 'bundles']),
    'experiment_bundle_manage': frozenset(['bundle', 'deleted', 'records_removed']),
    'experiment_ingest': frozenset(['bundle_id', 'job', 'source']),
    'experiment_extract_prompt': frozenset(['bundle_id', 'bundle_name', 'definition', 'field_descriptions', 'files', 'group_id', 'has_more', 'instruction', 'markdown', 'notes', 'offset', 'schema_id', 'schema_name', 'schema_version', 'system_prompt', 'total_characters']),
    'experiment_save': frozenset(['bundle_id', 'invalid', 'records']),
    'experiments': frozenset(['experiment', 'experiments']),
    'experiment_update': frozenset(['experiment']),
    'experiment_review': frozenset(['deleted', 'experiment', 'message', 'reasons', 'status']),
    'insights': frozenset(['cross_schema', 'group_id', 'insight', 'insights', 'schema_id', 'schema_ids', 'schema_name', 'statistics', 'statistics_by_schema']),
    'insight_prompt': frozenset(['cross_schema', 'definition', 'existing', 'field_descriptions', 'group_id', 'has_more', 'instruction', 'kinds', 'markdown', 'offset', 'record_count', 'roles', 'schema_id', 'schema_ids', 'schema_name', 'schema_version', 'schemas', 'statistics', 'statistics_by_schema', 'total_characters']),
    'insight_save': frozenset(['insights', 'schema_ids', 'warnings']),
    'insight_update': frozenset(['insight']),
    'insight_review': frozenset(['deleted', 'graph', 'insights', 'message', 'reasons', 'status']),
    'graph': frozenset(['connections', 'entities', 'matched', 'relations', 'search_text', 'semantic', 'semantic_error', 'truncated']),
    'jobs': frozenset(['events', 'job', 'jobs']),
    'projects': frozenset(['offset', 'project', 'projects', 'has_more', 'next_offset']),
    'project_manage': frozenset(['project']),
    'project_import_preview': frozenset(['depth', 'files', 'grouping', 'projects', 'token']),
    'project_import_commit': frozenset(['jobs', 'projects', 'reused', 'source_ids']),
    'project_process': frozenset(['jobs']),
    'spaces': frozenset(['space', 'spaces', 'templates']),
    'space_manage': frozenset(['space']),
    'projections': frozenset(['projection', 'projections', 'offset', 'has_more', 'next_offset']),
    'projection_prepare': frozenset(['projections']),
    'projection_prompt': frozenset(['project_id', 'project_name', 'expected_revision', 'definition', 'has_more', 'instruction', 'markdown', 'offset', 'project', 'projection_id', 'revision', 'source_fingerprint', 'sources', 'space', 'space_version', 'system_prompt', 'total_characters']),
    'projection_save': frozenset(['projection']),
    'projection_fail': frozenset(['projection']),
    'projection_review_prompt': frozenset(['project_id', 'project_name', 'expected_revision', 'data', 'definition', 'has_more', 'instruction', 'markdown', 'offset', 'project', 'projection_id', 'revision', 'sources', 'space', 'system_prompt', 'total_characters']),
    'projection_revise': frozenset(['projection']),
}


def _surface(*names, config_fields=frozenset()):
    return DeploymentSurface(
        resource_actions={name: DEPLOYMENT_FIELDS[name] for name in names}, config_fields=config_fields)


def _deployment():
    project_actions = ("overview", "projects", "project_manage", "project_import_preview",
        "project_import_commit", "project_process", "sources", "markdown", "search", "jobs")
    result_actions = ("overview", "projects", "spaces", "projections", "projection_prepare",
        "projection_prompt", "projection_save", "projection_fail", "projection_review_prompt", "projection_revise", "markdown")
    return NodeDeploymentDefinition(
        surface=_surface(*DEPLOYMENT_FIELDS, config_fields={"default_model"}),
        sections={
            "settings": DeploymentSurface(),
            "projects": _surface(*project_actions, *result_actions, config_fields={"default_model"}),
            "spaces": _surface("spaces", "space_manage", "projects", "projections", "markdown"),
            "results": _surface(*result_actions, config_fields={"default_model"}),
            "schemas": _surface("schemas", "schema_manage"),
            "experiments": _surface("overview", "jobs", "schemas", "schema_manage", "experiment_bundles",
                "experiment_bundle_manage", "experiment_ingest", "experiment_extract_prompt", "experiment_save",
                "experiments", "experiment_update", "experiment_review", config_fields={"default_model"}),
            "insights": _surface("overview", "schemas", "experiments", "insights", "insight_prompt",
                "insight_save", "insight_update", "insight_review", "graph", config_fields={"default_model"}),
        },
    )


def _guarded(handler):
    """Report MKB's own refusals the way the card reports ours: a 422, not a crash.

    OAW turns a ``ValueError`` out of a resource action into a validation error and
    everything else into a 500. MKB raises its own ``NotFoundError`` and friends for
    ordinary caller mistakes — a schema id that does not exist — so those are folded
    into :class:`KnowledgeError` here. The HTTP service keeps the untouched handlers
    because it can say 404 and 409; the card has one shape for "you asked for
    something that isn't there", so collapsing to it loses nothing.
    """
    @wraps(handler)
    def invoke(context, arguments):
        try:
            return handler(context, arguments)
        except ValueError:
            raise  # already the shape the host understands
        except Exception as error:
            message = operator_message(error)
            if message is None:
                raise  # a real bug: let it reach the log with its traceback
            raise KnowledgeError(message) from error

    return invoke


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "error"] = "available"
    description: str = Field(default="", max_length=2000,
        json_schema_extra={"agentReadable": True, "agentWritable": True})
    # A deployment has no live model picker, so extraction there uses whichever
    # model reference (an "oaw:model:..." id, set from the engineering Settings
    # section) was chosen before publishing. Blank until someone picks one.
    default_model: str = Field(default="", max_length=200)


class _JobWait(actions.Jobs):
    wait_seconds: float = Field(default=0, ge=0, le=30,
        description="With job_id, wait up to this many seconds for conversion to finish before returning its actual status.")


async def _wait_jobs(context, capability, arguments):
    request = _JobWait.model_validate(arguments)
    if request.wait_seconds and not request.job_id:
        raise KnowledgeError("job_id is required when waiting for a conversion")
    deadline = asyncio.get_running_loop().time() + request.wait_seconds
    arguments = request.model_dump(exclude={"wait_seconds"})
    while True:
        result = await context.node_resource_action(capability, "jobs", arguments)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0 or result.get("job", {}).get("status") not in actions.ACTIVE_JOBS:
            return result
        # Wait outside the resource mutation lock; the next poll rechecks access.
        await asyncio.sleep(min(1, remaining))


class KnowledgeBasePlugin:
    descriptor = PluginDescriptor(id=PREFIX, version="0.2.0", plugin_api_version="1.40",
        name="Knowledge base",
        description="Project materials to readable Markdown, reusable Space projections, and experiment records with evidence-linked insights.")

    def register(self, registration):
        registration.register_asset(PluginAsset(id="knowledge", media_type="image/svg+xml",
            content=files(__package__).joinpath("assets/knowledge.svg").read_bytes()))
        resource_actions = {}
        for operation in OPERATIONS:
            if not operation.agent:
                resource_actions[operation.name] = NodeResourceAction(_guarded(operation.handler))
                continue
            kind = f"{PREFIX}.{operation.name}"
            async def invoke(context, capability, arguments, action=operation.name):
                if action == "jobs":
                    return await _wait_jobs(context, capability, arguments)
                return await context.node_resource_action(capability, action, arguments)
            registration.register_capability(CapabilityDefinition(kind=kind,
                tool_name=operation.tool_name, description=operation.description + (
                    " Supply job_id and wait_seconds=20 to wait for completion without repeated immediate polls."
                    if operation.name == "jobs" else ""),
                target_parameter="knowledge", input_schema=(
                    _JobWait.model_json_schema() if operation.name == "jobs" else operation.input_schema()),
                read_only=operation.name in READ_ACTIONS), invoke)
            resource_actions[operation.name] = NodeResourceAction(_guarded(operation.handler),
                capability_kind=kind)

        schema = artifact_import.ArtifactImport.model_json_schema()
        schema["properties"].pop("source_collection_id")
        schema["required"].remove("source_collection_id")
        registration.register_capability(CapabilityDefinition(kind=artifact_import.KIND,
            tool_name=artifact_import.TOOL_NAME, description=artifact_import.DESCRIPTION,
            target_parameter="knowledge", input_schema=schema,
            selectors=(CapabilitySelector(parameter="source", argument="source_collection_id",
                capability_kinds=frozenset({"artifact.read"})),)), artifact_import.invoke)
        resource_actions[artifact_import.ACTION] = NodeResourceAction(
            _guarded(artifact_import.store_import), capability_kind=artifact_import.KIND)

        registration.register_node_type(NodeTypeDefinition(
            id=PREFIX, label="Knowledge base",
            description="Projects, Spaces, structured results and experiment insights",
            icon="library", icon_asset="knowledge", color="#8a6fd1",
            deck_id="knowledge", deck_label="Knowledge", deck_icon="library",
            default_name="Knowledge base", default_size=(360, 280), default_status="available",
            statuses=frozenset({"available", "error"}), config_model=KnowledgeConfig,
            traits=frozenset({"knowledge.base", "data.source"}), lifecycle=KnowledgeLifecycle(),
            data_source=NodeDataSource(DATA_SOURCE.schemas, _guarded(DATA_SOURCE.read)),
            resource_actions=resource_actions,
            deletion_warning="Deleting this card permanently removes its documents, records, drafts, insights and knowledge graph. Canvas undo and copy do not preserve knowledge base files. Back up your profile before deleting knowledge you need.",
            # Templateable with no template handler: a Legion copy deploys a card with
            # the same name and settings over an empty database, which is what the
            # deletion warning already promises. Files are never captured.
            templateable=True, template_status="available",
            frontend={"preview": "preview", "body": "workspace", "workspace": "workspace"},
            surfaces={"preview": True, "inspector": True, "workspace": True},
            presentation=NodePresentation(states=("node", "preview", "inspector", "workspace"),
                initial="preview", open="inspector",
                workspace_layout=WorkspaceLayoutMetadata(min_width=320, min_height=280, ideal_width=800, ideal_height=600)),
            deployment=_deployment()))

        for access, label, short, granted, description in (
            ("read", "Knowledge read", "read", READ_ACTIONS,
             "Can read this knowledge base: projects, original Markdown, Spaces, projections, records, insights and the published graph."),
            ("extract", "Knowledge extract", "extract", EXTRACT_ACTIONS,
             "Can read this knowledge base, run project Space projections directly over Markdown and revise their results when asked. Review is optional."),
            ("experiment", "Knowledge experiment", "experiment", EXPERIMENT_ACTIONS,
             "Can read this knowledge base, project an experiment upload into draft records and edit drafts when asked. Records stay unpublished until a person publishes them on the canvas."),
            ("research", "Knowledge research", "research", RESEARCH_ACTIONS + (artifact_import.ACTION,),
             "Can import authorized attachments, convert documents, prepare projects, Spaces, schemas and uploads, and extract project results, experiment records and insight drafts. Publication and settings remain under human control."),
        ):
            registration.register_relationship(RelationshipDefinition(
                id=f"{PREFIX}.{access}", label=label, short_label=short, description=description,
                source_traits=frozenset({"core.agent"}), target_types=frozenset({PREFIX}),
                templateable=True,
                capabilities=tuple(CapabilityGrantDefinition(f"{PREFIX}.{item}")
                                   for item in granted)))

        registration.register_pack(PackDefinition(id=f"{PREFIX}.default", name="Knowledge base",
            description="Turn documents into reviewed, traceable structured knowledge.",
            cards=(PREFIX,), accent_color="#8a6fd1"))

        registration.register_legion_preset(research_preset())


def create_plugin():
    return KnowledgeBasePlugin()
