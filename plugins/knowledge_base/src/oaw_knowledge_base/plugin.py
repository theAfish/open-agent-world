"""OAW registration: the ``knowledge.base`` card over the shared operation table.

Everything the card can do lives in ``operations.py`` alongside the HTTP and MCP
front doors, so a tool behaves the same whichever one a caller came through. Rows
without a tool name get a resource action with no capability kind, which is what makes
them unreachable by any agent.
"""
from functools import wraps
from importlib.resources import files
from typing import Literal

from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, DeploymentSurface, NodeDeploymentDefinition,
    NodeResourceAction, NodeTypeDefinition, PackDefinition, PluginAsset, PluginDescriptor,
    RelationshipDefinition,
)
from pydantic import BaseModel, ConfigDict, Field

from .errors import KnowledgeError, operator_message
from .lifecycle import KnowledgeLifecycle
from .operations import EXTRACT_ACTIONS, OPERATIONS, READ_ACTIONS
from .preset import definition as research_preset

PREFIX = "knowledge.base"

# Deployment cannot expose a live model picker — "it does not mount ... model
# settings" — so a deployed release projects the published fact through one model
# chosen ahead of time. Every top-level field a resource action's result may carry,
# named once so the whole-card surface and each workspace section can share it.
DEPLOYMENT_FIELDS = {
    "overview": frozenset({"collection", "groups", "engines", "counts", "active_jobs",
                          "graph_schema_id"}),
    "sources": frozenset({"sources", "offset"}),
    "ingest": frozenset({"source", "group_id"}),
    "process": frozenset({"jobs"}),
    "groups": frozenset({"groups", "group", "deleted"}),
    "markdown": frozenset({"record_id", "filename", "engine", "total_characters", "offset",
                           "markdown", "has_more"}),
    "search": frozenset({"query", "results"}),
    "schemas": frozenset({"schemas", "schema"}),
    "projection_prompt": frozenset({"schema_id", "schema_name", "schema_version", "system_prompt",
                                    "definition", "field_descriptions", "record_id", "artifact_id",
                                    "source_id", "filename", "markdown", "truncated"}),
    "save_projection": frozenset({"projection"}),
    "projections": frozenset({"projection", "projections"}),
    "draft": frozenset({"drafts", "draft"}),
    "experiment_assemble_prompt": frozenset({"group_id", "schema_id", "schema_name", "definition",
                                             "system_prompt", "contributions", "suggested_name"}),
    "experiment_save": frozenset({"record"}),
    "experiments": frozenset({"experiments", "experiment"}),
    "experiment_update": frozenset({"experiment"}),
    "review": frozenset({"decision", "draft_id", "status", "reasons", "message", "fact", "event",
                         "graph"}),
    "graph_schema": frozenset({"schema_id"}),
    "graph": frozenset({"entities", "relations", "truncated"}),
    "jobs": frozenset({"jobs", "job", "events"}),
}


def _surface(*names, config_fields=frozenset()):
    return DeploymentSurface(
        resource_actions={name: DEPLOYMENT_FIELDS[name] for name in names}, config_fields=config_fields)


def _deployment():
    """What a published release of the Knowledge research Legion can do.

    ``settings`` never appears here: the collection name, PDF engine and MinerU URL
    stay engineering-only, matching "it does not mount ... model settings". Every
    other business action a person uses to drive the pipeline — upload, convert,
    search, project, choose the graph schema, build a draft, review, publish — is
    exactly what a deployed release is for, so it is granted. The layout can place
    this card as one whole pane (``surface``) or extract each section into its own
    tab (``sections``); both are covered so either shape of a saved workspace can
    publish. Section names match the card's own frontend's WorkspaceSection ids —
    Sources holds search and the single-document projection convenience, Graph holds
    the graph-schema pipeline end to end (project, draft, review, publish) that used
    to be split across three separate tabs.
    """
    all_actions = tuple(DEPLOYMENT_FIELDS)
    return NodeDeploymentDefinition(
        surface=_surface(*all_actions, config_fields={"default_model"}),
        sections={
            # Declared with no grants of its own so the pane can still be placed
            # without failing publication; hide it in the layout editor to keep a
            # release from showing engineering configuration at all.
            "settings": DeploymentSurface(),
            "sources": _surface("overview", "sources", "ingest", "process", "groups", "jobs",
                                "search", "markdown", "projection_prompt", "save_projection",
                                config_fields={"default_model"}),
            "schemas": _surface("schemas"),
            "projections": _surface("projections", "save_projection", "projection_prompt",
                                    config_fields={"default_model"}),
            "graph": _surface("graph", "graph_schema", "projections", "save_projection",
                              "projection_prompt", "draft", "review",
                              config_fields={"default_model"}),
            "experiments": _surface("projections", "experiments", "experiment_assemble_prompt",
                                    "experiment_save", "experiment_update",
                                    config_fields={"default_model"}),
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
    # A deployment has no live model picker, so projection there uses whichever
    # model reference (an "oaw:model:..." id, set from the engineering Settings
    # section) was chosen before publishing. Blank until someone picks one.
    default_model: str = Field(default="", max_length=200)


class KnowledgeBasePlugin:
    descriptor = PluginDescriptor(id=PREFIX, version="0.2.0", plugin_api_version="1.21",
        name="Knowledge base",
        description="Embedded research knowledge base: documents to markdown, markdown to structured JSON, and a review-gated knowledge graph.")

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
                return await context.node_resource_action(capability, action, arguments)
            registration.register_capability(CapabilityDefinition(kind=kind,
                tool_name=operation.tool_name, description=operation.description,
                target_parameter="knowledge", input_schema=operation.input_schema()), invoke)
            resource_actions[operation.name] = NodeResourceAction(_guarded(operation.handler),
                capability_kind=kind)

        registration.register_node_type(NodeTypeDefinition(
            id=PREFIX, label="Knowledge base",
            description="Documents, structured projections and a reviewed knowledge graph",
            icon="library", icon_asset="knowledge", color="#8a6fd1",
            deck_id="knowledge", deck_label="Knowledge", deck_icon="library",
            default_name="Knowledge base", default_size=(360, 280), default_status="available",
            statuses=frozenset({"available", "error"}), config_model=KnowledgeConfig,
            traits=frozenset({"knowledge.base"}), lifecycle=KnowledgeLifecycle(),
            resource_actions=resource_actions,
            deletion_warning="Deleting this card permanently removes its documents, projections, drafts, published facts and knowledge graph. Canvas undo and copy do not preserve knowledge base files. Back up your profile before deleting knowledge you need.",
            # Templateable with no template handler: a Legion copy deploys a card with
            # the same name and settings over an empty database, which is what the
            # deletion warning already promises. Files are never captured.
            templateable=True, template_status="available",
            frontend={"preview": "preview", "body": "workspace", "workspace": "workspace"},
            surfaces={"preview": True, "inspector": True, "workspace": True},
            deployment=_deployment()))

        for access, label, short, granted, description in (
            ("read", "Knowledge read", "read", READ_ACTIONS,
             "Can read this knowledge base: sources, extracted markdown, schemas, projections, drafts and the published graph."),
            ("extract", "Knowledge extract", "extract", EXTRACT_ACTIONS,
             "Can read this knowledge base and add projections and draft graphs. Drafts stay unpublished until a person approves them on the canvas."),
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
