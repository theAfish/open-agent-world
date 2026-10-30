from __future__ import annotations

import re
import keyword
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator, TypeAdapter
from backend.plugins.state import PluginStateSpec, LEGACY_STATE

from backend.errors import GraphValidationError, PluginCompatibilityError, PluginUnavailableError
from backend.plugins.lifecycle import NodeLifecycleHandler
from backend.plugins.resources import NodeResourceAction
from backend.plugins.data_sources import NodeDataConsumer, NodeDataSource
from backend.plugins.execution_graph import NodeExecutionSource
from backend.plugins.template import NodeTemplateHandler
from backend.plugins.presets import LegionPresetDefinition
from backend.plugins.tutorials import Tutorials, validate_tutorials
from backend.plugins.equipment import DefaultEquipment

if TYPE_CHECKING:
    from backend.legions.models import LegionBlueprintPreset
    from backend.plugins.summoning import NodeSummoningDefinition
    from backend.agents import AgentCapabilityProvider, ModelConnectionResolver, RuntimeProvider
    from backend.capabilities import Capability
    from backend.plugins.capability import CapabilityContext
    from backend.state.schema import StateSchema


from backend.plugins.documents import NodeDocumentDefinition
from backend.plugins.deployment import NodeDeploymentDefinition
from backend.plugins.containers import NodeContainerDefinition
from backend.plugins.execution import NodeExecutionDefinition

PLUGIN_API_VERSION = "1.41"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$")
_API_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _supports_plugin_api(required: str) -> bool:
    host_match = _API_VERSION.fullmatch(PLUGIN_API_VERSION)
    required_match = _API_VERSION.fullmatch(required)
    if host_match is None or required_match is None:
        return False
    host_major, host_minor = (int(part) for part in host_match.groups())
    required_major, required_minor = (int(part) for part in required_match.groups())
    return required_major == host_major and required_minor <= host_minor


class PluginDescriptor(BaseModel):
    """Stable identity and compatibility metadata for one installed plugin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str = Field(min_length=1, max_length=64)
    plugin_api_version: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    python_requirements: tuple[str, ...] = ()
    sandbox_environments: tuple[str, ...] = Field(default=(), max_length=20,
        description="Own declared Sandbox environments used by this Pack's runtime providers.")
    requires_plugins: tuple[str, ...] = ()
    state: PluginStateSpec | None = None

    @model_validator(mode="after")
    def validate_sandbox_environments(self) -> Self:
        if len(set(self.sandbox_environments)) != len(self.sandbox_environments) or any(
            not _IDENTIFIER.fullmatch(value) for value in self.sandbox_environments
        ):
            raise ValueError("Sandbox environment references must be unique identifiers")
        return self


class PackDefinition(BaseModel):
    """A stable distribution manifest referencing canonical node type IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    cards: tuple[str, ...] = ()
    artwork_asset: str | None = Field(default=None, min_length=1, max_length=128)
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    packaging: Literal["standard", "premium", "paper", "collector"] = "standard"
    tutorials: Tutorials = ()

    @model_validator(mode="after")
    def validate_tutorial_content(self) -> Self:
        validate_tutorials(self.tutorials)
        return self


class PackCatalogItem(PackDefinition):
    plugin_id: str
    artwork_url: str | None = None
    source: Literal["bundled", "installed"] = "bundled"


class Plugin(Protocol):
    descriptor: PluginDescriptor

    def register(self, registration: PluginRegistration) -> None: ...


@dataclass(frozen=True, slots=True)
class PluginDefinition:
    """Small declarative Plugin implementation for simple packages and tests."""

    descriptor: PluginDescriptor
    configure: Callable[[PluginRegistration], None]

    def register(self, registration: PluginRegistration) -> None:
        self.configure(registration)


SurfaceLevel = Literal["node", "preview", "inspector", "workspace"]


class WorkspaceLayoutMetadata(BaseModel):
    """Optional content sizing; never assigns a role or changes saved layouts."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    min_width: float | None = Field(default=None, ge=96, le=8192)
    min_height: float | None = Field(default=None, ge=96, le=8192)
    ideal_width: float | None = Field(default=None, ge=96, le=8192)
    ideal_height: float | None = Field(default=None, ge=96, le=8192)

    @model_validator(mode="after")
    def validate_sizes(self) -> Self:
        for minimum, ideal in ((self.min_width, self.ideal_width), (self.min_height, self.ideal_height)):
            if minimum is not None and ideal is not None and ideal < minimum:
                raise ValueError("workspace ideal size must not be smaller than its minimum")
        return self


class NodePresentation(BaseModel):
    """Supported canvas surfaces, first appearance, and compact-card open target."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    states: tuple[SurfaceLevel, ...] = Field(min_length=1)
    initial: SurfaceLevel
    open: SurfaceLevel
    sizes: dict[SurfaceLevel, dict[str, float]] = Field(default_factory=dict)
    workspace_layout: WorkspaceLayoutMetadata | None = None

    @classmethod
    def from_legacy_surfaces(cls, surfaces: dict[str, bool]) -> Self:
        states: tuple[SurfaceLevel, ...] = ("node",) + tuple(
            level for level, default in (("preview", True), ("inspector", True), ("workspace", False))
            if surfaces.get(level, default)
        )
        initial = "preview" if "preview" in states else "node"
        target = next((level for level in ("inspector", "workspace", "preview") if level in states), "node")
        return cls(states=states, initial=initial, open=target)

    @model_validator(mode="after")
    def validate_states(self) -> Self:
        if len(set(self.states)) != len(self.states):
            raise ValueError("presentation states must be unique")
        if self.initial not in self.states or self.open not in self.states:
            raise ValueError("presentation initial and open must belong to states")
        for level, size in self.sizes.items():
            if level not in self.states or set(size) != {"width", "height"}:
                raise ValueError("presentation sizes must specify width and height for supported states")
            if not all(96 <= value <= 4096 for value in size.values()):
                raise ValueError("presentation sizes must be between 96 and 4096")
        return self


class CardFaceDesign(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    variant: Literal["icon", "image", "text", "compact", "dark"] = "icon"
    tone: Literal["midnight", "sage", "sand", "sky", "rose", "stone"] = "sage"


class CardFaceSpec(CardFaceDesign):
    """Optional print layout; artwork references this plugin's registered assets."""

    image_asset: str | None = None


class CardFaceCatalog(CardFaceDesign):
    image_url: str | None = None


class NodeTypeCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tutorials: Tutorials = ()

    id: str
    plugin_id: str
    label: str
    description: str
    icon: str
    icon_url: str | None = None
    card_face: CardFaceCatalog | None = None
    frontend: dict[str, str] = Field(default_factory=dict)
    authoring: dict[str, Any] | None = None
    color: str
    deck_id: str
    deck_label: str
    deck_icon: str
    deck_revision: int = 1
    default_name: str
    default_size: dict[str, float]
    default_status: str
    traits: list[str]
    data_consumer: NodeDataConsumer | None = None
    surfaces: dict[str, bool]
    presentation: NodePresentation
    state: PluginStateSpec = LEGACY_STATE
    has_scoped_state: bool = False
    has_document: bool = False
    has_default_equipment: bool = False
    transformations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    has_execution: bool = False
    container: dict[str, Any] | None = None
    summoning: dict[str, Any] | None = None
    default_config: dict[str, Any]
    config_schema: dict[str, Any] = Field(default_factory=dict)
    user_creatable: bool
    templateable: bool
    deletion_warning: str | None = None
    deployment: dict[str, Any] | None = None


class RelationshipCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    plugin_id: str
    label: str
    short_label: str
    description: str
    source_types: list[str]
    target_types: list[str]
    source_traits: list[str]
    target_traits: list[str]
    directions: list[str]
    templateable: bool
    generated: bool = False
    data_read: bool = False


class PluginCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: list[PluginDescriptor]
    enabled_plugin_ids: list[str] = Field(default_factory=list)
    node_types: list[NodeTypeCatalogItem]
    relationships: list[RelationshipCatalogItem]
    packs: list[PackCatalogItem] = Field(default_factory=list)
    frontend_modules: dict[str, dict[str, Any]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CapabilityGrantDefinition:
    """Relationships grant kinds; inline metadata is a legacy install input."""
    kind: str
    tool_prefix: str = ""
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CapabilitySelector:
    """Independent resource selected from live grants, optionally a document member."""
    parameter: str
    argument: str
    capability_kinds: frozenset[str] = frozenset()
    target_traits: frozenset[str] = frozenset()
    document_action: str | None = None
    include_members: bool = False
    required: bool = True


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """Operation metadata separate from the relationships granting its kind.

    Kinds may share a tool_name only with identical operation contracts; their
    handlers still receive the selected, independently authorized capability.
    """
    kind: str
    tool_name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    target_parameter: str = "target"
    selectors: tuple[CapabilitySelector, ...] = ()
    target_capabilities: frozenset[str] = frozenset()
    # Unknown/plugin-defined operations are conservatively treated as writes
    # when deciding whether an interrupted Agent Run may replay its work.
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class PluginAsset:
    """Explicit public resource; bytes are captured during plugin registration."""

    id: str
    content: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class NodeTypeDefinition:
    id: str
    label: str
    description: str
    icon: str
    color: str
    deck_id: str
    deck_label: str
    deck_icon: str
    default_name: str
    default_size: tuple[float, float]
    default_status: str
    statuses: frozenset[str]
    config_model: type[BaseModel]
    # Legacy category revision, used only when importing pre-Pack browser decks.
    # Collected decks are user-owned and are never reassigned by the catalog.
    deck_revision: int = 1
    icon_asset: str | None = None
    frontend: Mapping[str, str] = field(default_factory=dict)
    traits: frozenset[str] = frozenset()
    surfaces: Mapping[str, bool] = field(
        default_factory=lambda: {
            "preview": True,
            "inspector": True,
            "workspace": False,
        }
    )
    presentation: NodePresentation | None = None
    creation_fields: frozenset[str] = frozenset()
    # Persisted node types are not necessarily standalone objects. Managed
    # containers, for example, must be created through their domain operation.
    user_creatable: bool = True
    lifecycle: NodeLifecycleHandler | None = None
    templateable: bool = False
    template_status: str | None = None
    template_handler: NodeTemplateHandler | None = None
    # Convert card references to portable keys at capture and new IDs at restore.
    template_remap_config: Callable[[dict[str, Any], Mapping[str, str]], dict[str, Any]] | None = None
    document: NodeDocumentDefinition | None = None
    resource_actions: Mapping[str, NodeResourceAction] = field(default_factory=dict)
    data_source: NodeDataSource | None = None
    execution_source: NodeExecutionSource | None = None
    data_consumer: NodeDataConsumer | None = None
    # Native state with no browser snapshot must never masquerade as undoable.
    deletion_warning: str | None = None
    execution: NodeExecutionDefinition | None = None
    container: NodeContainerDefinition | None = None
    summoning: NodeSummoningDefinition | None = None
    # Ordinary creation is autonomous; plugins explicitly mark sensitive initialization.
    canvas_create_requires_confirmation: bool = False
    deployment: NodeDeploymentDefinition | None = None
    state: PluginStateSpec | None = None
    card_face: CardFaceSpec | Mapping[str, str] | None = None
    tutorials: Tutorials = ()
    default_equipment: tuple[DefaultEquipment, ...] = ()
    authoring: Mapping[str, Any] | None = None

    def resolved_presentation(self) -> NodePresentation:
        if self.presentation is not None:
            return NodePresentation.model_validate(self.presentation)
        return NodePresentation.from_legacy_surfaces(self.surfaces)

    def catalog_item(self, plugin_id: str) -> NodeTypeCatalogItem:
        default_config = self.config_model().model_dump(mode="json")
        presentation = self.resolved_presentation()
        face = CardFaceSpec.model_validate(self.card_face) if self.card_face is not None else None
        authoring = None
        if self.authoring is not None:
            from backend.packs.interfaces import CardInterface
            authoring = CardInterface.model_validate(self.authoring).model_dump(mode="json")
        return NodeTypeCatalogItem(
            tutorials=validate_tutorials(self.tutorials),
            id=self.id,
            plugin_id=plugin_id,
            label=self.label,
            description=self.description,
            icon=self.icon,
            icon_url=f"/api/plugins/{plugin_id}/assets/{self.icon_asset}" if self.icon_asset else None,
            card_face=CardFaceCatalog(variant=face.variant, tone=face.tone,
                image_url=f"/api/plugins/{plugin_id}/assets/{face.image_asset}" if face.image_asset else None) if face else None,
            frontend=dict(self.frontend),
            authoring=authoring,
            color=self.color,
            deck_id=self.deck_id,
            deck_label=self.deck_label,
            deck_icon=self.deck_icon,
            deck_revision=self.deck_revision,
            default_name=self.default_name,
            default_size={
                "width": self.default_size[0],
                "height": self.default_size[1],
            },
            default_status=self.default_status,
            traits=sorted(self.traits),
            data_consumer=self.data_consumer,
            surfaces={
                level: level in presentation.states for level in ("preview", "inspector", "workspace")
            },
            presentation=presentation,
            default_config=default_config,
            config_schema=self.config_model.model_json_schema(),
            state=self.state or LEGACY_STATE,
            has_scoped_state=self.state is not None and self.state.mode == "scoped",
            has_document=self.document is not None,
            has_default_equipment=bool(self.default_equipment),
            transformations={key: {"label": item.label, "source_traits": sorted(item.source_traits)} for key, item in self.document.transformations.items()} if self.document else {},
            has_execution=self.execution is not None,
            container=self.container.catalog_item() if self.container else None,
            summoning={} if self.summoning else None,
            user_creatable=self.user_creatable,
            templateable=self.templateable,
            deletion_warning=self.deletion_warning,
            deployment=self.deployment.model_dump(mode="json") if self.deployment else None,
        )


@dataclass(frozen=True, slots=True)
class RelationshipDefinition:
    id: str
    label: str
    short_label: str
    description: str
    source_types: frozenset[str] = frozenset()
    target_types: frozenset[str] = frozenset()
    source_traits: frozenset[str] = frozenset()
    target_traits: frozenset[str] = frozenset()
    directions: frozenset[str] = frozenset({"forward"})
    capabilities: tuple[CapabilityGrantDefinition, ...] = ()
    templateable: bool = False
    # Runtime provenance only: never traversed for capabilities or copied into templates.
    generated: bool = False
    # Ordinary connections are autonomous; sensitive grants explicitly require review.
    canvas_requires_confirmation: bool = False
    # Read-only dataset access from source (consumer) to target (provider).
    data_read: bool = False

    def catalog_item(self, plugin_id: str) -> RelationshipCatalogItem:
        return RelationshipCatalogItem(
            id=self.id,
            plugin_id=plugin_id,
            label=self.label,
            short_label=self.short_label,
            description=self.description,
            source_types=sorted(self.source_types),
            target_types=sorted(self.target_types),
            source_traits=sorted(self.source_traits),
            target_traits=sorted(self.target_traits),
            directions=sorted(self.directions),
            templateable=self.templateable,
            generated=self.generated,
            data_read=self.data_read,
        )


CapabilityHandler = Callable[
    ["CapabilityContext", "Capability", dict[str, Any]], Awaitable[Any]
]
RuntimeProviderFactory = Callable[..., "RuntimeProvider"]


class PluginRegistration:
    """Plugin-scoped, staged contribution collector used during installation."""

    def __init__(self, descriptor: PluginDescriptor) -> None:
        self.descriptor = descriptor
        self.nodes: dict[str, NodeTypeDefinition] = {}
        self.relationships: dict[str, RelationshipDefinition] = {}
        self.capability_handlers: dict[str, CapabilityHandler] = {}
        self.capabilities: dict[str, CapabilityDefinition] = {}
        self.runtime_provider_factories: dict[str, RuntimeProviderFactory] = {}
        self.runtime_provider_model_resolvers: set[str] = set()
        self.runtime_provider_context_stores: set[str] = set()
        self.background_job_providers: dict[str, Any] = {}
        self.state_schemas: dict[str, StateSchema] = {}
        self.assets: dict[str, PluginAsset] = {}
        self.packs: dict[str, PackDefinition] = {}
        self.legion_presets: dict[str, LegionPresetDefinition | LegionBlueprintPreset] = {}

    def register_legion_preset(self, definition: LegionPresetDefinition | LegionBlueprintPreset) -> None:
        from backend.legions.models import LegionBlueprintPreset
        if not isinstance(definition, (LegionPresetDefinition, LegionBlueprintPreset)):
            raise TypeError("Legion preset must be a LegionPresetDefinition")
        if not definition.id.startswith(self.descriptor.id + "."):
            raise ValueError("Legion preset IDs must use their plugin namespace")
        self._add(self.legion_presets, definition.id, definition, "Legion preset")

    def register_pack(self, definition: PackDefinition) -> None:
        if not isinstance(definition, PackDefinition):
            raise TypeError("pack must be a PackDefinition")
        self._add(self.packs, definition.id, definition, "pack")

    def register_asset(self, asset: PluginAsset) -> None:
        if not isinstance(asset, PluginAsset):
            raise TypeError("asset must be a PluginAsset")
        self._add(self.assets, asset.id, asset, "asset")

    def register_node_type(self, definition: NodeTypeDefinition) -> None:
        if 'core.agent' in definition.traits or definition.summoning or definition.execution_source:
            definition = replace(definition, traits=definition.traits | frozenset({'core.execution-source'}))
        self._add(self.nodes, definition.id, definition, "node type")

    def register_relationship(self, definition: RelationshipDefinition) -> None:
        self._add(self.relationships, definition.id, definition, "relationship")

    def register_capability_handler(self, kind: str, handler: CapabilityHandler) -> None:
        self._add(self.capability_handlers, kind, handler, "capability handler")

    def register_capability(self, definition: CapabilityDefinition, handler: CapabilityHandler) -> None:
        if not isinstance(definition.read_only, bool):
            raise ValueError("capability read_only must be a boolean")
        self._add(self.capabilities, definition.kind, definition, "capability")
        self.register_capability_handler(definition.kind, handler)

    def register_runtime_provider(
        self, provider_id: str, factory: RuntimeProviderFactory,
        *, needs_model_connection_resolver: bool = False,
        needs_context_store: bool = False,
    ) -> None:
        self._add(
            self.runtime_provider_factories,
            provider_id,
            factory,
            "runtime provider",
        )
        if needs_model_connection_resolver:
            self.runtime_provider_model_resolvers.add(provider_id)
        if needs_context_store:
            self.runtime_provider_context_stores.add(provider_id)

    def register_state_schema(self, schema: StateSchema) -> None:
        self._add(self.state_schemas, schema.id, schema, "state schema")

    def register_background_job_provider(self, provider_id, definition) -> None:
        from .background_jobs import BackgroundJobProvider
        import math
        if not isinstance(definition, BackgroundJobProvider) or not math.isfinite(definition.poll_interval_seconds) or definition.poll_interval_seconds < 1:
            raise ValueError("Background job providers require a positive polling interval")
        if (not isinstance(definition.specification, type) or not issubclass(definition.specification, BaseModel)
                or not callable(definition.inspect)
                or any(value is not None and not callable(value) for value in (definition.prepare, definition.submit, definition.cancel))):
            raise ValueError("Background job providers require a Pydantic specification and callable callbacks")
        self._add(self.background_job_providers, provider_id, definition, "background job provider")

    @staticmethod
    def _add(target: dict[str, Any], identifier: str, value: Any, label: str) -> None:
        PluginRegistry.validate_identifier(identifier, label)
        if identifier in target:
            raise ValueError(f"{label} {identifier!r} is registered twice by this plugin")
        target[identifier] = value


class PluginRegistry:
    """Authoritative registry of installed plugins and their owned contributions."""

    def __init__(self) -> None:
        # Startup diagnostics carry no executable contributions or permissions.
        self.load_failures: list[dict[str, Any]] = []
        self.runtime_requirements: dict[str, tuple[str, ...]] = {}
        self.installed_packs: dict[str, Any] = {}
        self.frontend_modules: dict[str, dict[str, Any]] = {}
        self.pack_specs: dict[str, Any] = {}
        self.pack_roots: dict[str, Any] = {}
        self.pack_sources: dict[str, str] = {}
        self._plugins: dict[str, PluginDescriptor] = {}
        self._nodes: dict[str, NodeTypeDefinition] = {}
        self._relationships: dict[str, RelationshipDefinition] = {}
        self._capability_handlers: dict[str, CapabilityHandler] = {}
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._runtime_provider_factories: dict[str, RuntimeProviderFactory] = {}
        self._runtime_provider_model_resolvers: set[str] = set()
        self._runtime_provider_context_stores: set[str] = set()
        self._background_job_providers: dict[str, Any] = {}
        self._state_schemas: dict[str, StateSchema] = {}
        self._owners: dict[tuple[str, str], str] = {}
        self._assets: dict[tuple[str, str], PluginAsset] = {}
        self._packs: dict[str, PackCatalogItem] = {}
        self._legion_presets: dict[str, LegionPresetDefinition | LegionBlueprintPreset] = {}
        self._disabled: set[str] = set()

    def install(self, plugin: Plugin, *, distribution=None, source_spec=None) -> None:
        descriptor = getattr(plugin, "descriptor", None)
        if not isinstance(descriptor, PluginDescriptor):
            raise TypeError("plugin descriptor must be a PluginDescriptor")
        self.validate_identifier(descriptor.id, "plugin")
        if not _supports_plugin_api(descriptor.plugin_api_version):
            raise PluginCompatibilityError(
                f"plugin {descriptor.id!r} requires Plugin API "
                f"{descriptor.plugin_api_version!r}; host provides {PLUGIN_API_VERSION!r}"
            )
        if descriptor.id in self._plugins:
            raise ValueError(f"plugin {descriptor.id!r} is already installed")
        for dependency in descriptor.requires_plugins:
            self.validate_identifier(dependency, "plugin dependency")
            if not self.is_enabled(dependency):
                raise PluginCompatibilityError(
                    f"plugin {descriptor.id!r} requires plugin {dependency!r} to be installed first"
                )
        register = getattr(plugin, "register", None)
        if not callable(register):
            raise TypeError(f"plugin {descriptor.id!r} must define register(registration)")

        staged = PluginRegistration(descriptor)
        if source_spec is not None and source_spec.entrypoints and source_spec.contributions.cards:
            from backend.plugins.declarative import contribute_cards
            contribute_cards(staged, source_spec)
        register(staged)
        if source_spec is not None and source_spec.designs:
            from backend.packs.interfaces import apply_interfaces
            apply_interfaces(staged, source_spec)
        declaration = distribution if distribution is not None else source_spec
        declared_nodes = getattr(declaration, "node_types", None)
        if declared_nodes is not None and set(staged.nodes) != set(declared_nodes):
            raise ValueError("Plugin must register exactly its declared owned node types")
        if distribution is not None:
            if declared_nodes is None and any(not key.startswith(distribution.owner_id + ".") and not (
                    distribution.kind == 'plugin' and key == distribution.owner_id) for key in staged.nodes):
                raise ValueError("External card IDs must belong to the Pack namespace")
            if distribution.kind == 'content' and any(card not in staged.nodes for pack in staged.packs.values() for card in pack.cards):
                raise ValueError("An external Pack may include only its own cards")
        for key, node in tuple(staged.nodes.items()):
            policy = node.state if node.state is not None else descriptor.state
            if policy is not None:
                policy = TypeAdapter(PluginStateSpec).validate_python(policy)
                if policy.mode == "none" and (node.document or node.execution):
                    raise ValueError("Stateless cards cannot declare persistent documents or execution")
                staged.nodes[key] = replace(node, state=policy)
        if bool(staged.nodes) and not staged.packs:
            staged.register_pack(PackDefinition(
                id=f"{descriptor.id}.default", name=descriptor.name or descriptor.id,
                description=descriptor.description or "", cards=tuple(staged.nodes),
            ))
        if source_spec is not None and source_spec.packs and set(staged.packs) != set(source_spec.packs):
            raise ValueError("Plugin must register exactly its declared provided Pack IDs")
        if distribution is not None and set(staged.packs) != set(distribution.provided_pack_ids):
            raise ValueError("A content Pack must register exactly one Pack with its distribution ID" if distribution.kind == 'content'
                else "An external Pack must register exactly its declared provided Pack IDs")
        self._normalize_capabilities(staged)
        self._validate_registration(staged)

        # Construct all fallible metadata before publishing any contribution.
        packs = {
            key: PackCatalogItem(**pack.model_dump(), plugin_id=descriptor.id, source="installed" if distribution else "bundled",
                artwork_url=f"/api/plugins/{descriptor.id}/assets/{pack.artwork_asset}" if pack.artwork_asset else None)
            for key, pack in staged.packs.items()
        }

        self._plugins[descriptor.id] = descriptor
        self._commit_owned("legion_preset", descriptor.id, self._legion_presets, staged.legion_presets)
        self._commit_owned("pack", descriptor.id, self._packs, packs)
        self._assets.update({(descriptor.id, key): asset for key, asset in staged.assets.items()})
        self._commit_owned("node_type", descriptor.id, self._nodes, staged.nodes)
        self._commit_owned("capability", descriptor.id, self._capabilities, staged.capabilities)
        self._commit_owned(
            "relationship", descriptor.id, self._relationships, staged.relationships
        )
        self._commit_owned(
            "capability_handler",
            descriptor.id,
            self._capability_handlers,
            staged.capability_handlers,
        )
        self._commit_owned(
            "runtime_provider",
            descriptor.id,
            self._runtime_provider_factories,
            staged.runtime_provider_factories,
        )
        self._runtime_provider_model_resolvers.update(staged.runtime_provider_model_resolvers)
        self._runtime_provider_context_stores.update(staged.runtime_provider_context_stores)
        self._commit_owned(
            "state_schema", descriptor.id, self._state_schemas, staged.state_schemas
        )
        self._commit_owned("background_job_provider", descriptor.id,
            self._background_job_providers, staged.background_job_providers)

    def _normalize_capabilities(self, staged: PluginRegistration) -> None:
        # Legacy input is normalized once. Projection uses the same registry.
        for relationship in staged.relationships.values():
            for grant in relationship.capabilities:
                existing = staged.capabilities.get(grant.kind) or self._capabilities.get(grant.kind)
                if grant.tool_prefix:
                    definition = CapabilityDefinition(grant.kind, grant.tool_prefix,
                        grant.description.replace("{target_name!r}", "the selected target").replace("{target_name}", "the selected target"),
                        grant.input_schema)
                    if existing is None:
                        staged.capabilities[grant.kind] = definition
                    elif (existing.tool_name, existing.input_schema) != (definition.tool_name, definition.input_schema):
                        raise ValueError(f"Conflicting operation metadata for capability {grant.kind!r}")
                elif existing is None:
                    raise ValueError(f"Capability {grant.kind!r} needs a registered operation definition")

    def _validate_registration(self, staged: PluginRegistration) -> None:
        from backend.state.schema import StateSchema

        for asset in staged.assets.values():
            if not isinstance(asset.content, bytes) or not asset.content or len(asset.content) > 5 * 1024 * 1024:
                raise ValueError("public assets require 1 byte to 5 MiB of content")
            if asset.media_type not in {"image/svg+xml", "image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise ValueError("unsupported public asset media type")

        contribution_sets = (
            ("background_job_provider", "background job provider", self._background_job_providers, staged.background_job_providers),
            ("legion_preset", "Legion preset", self._legion_presets, staged.legion_presets),
            ("pack", "pack", self._packs, staged.packs),
            ("capability", "capability", self._capabilities, staged.capabilities),
            ("node_type", "node type", self._nodes, staged.nodes),
            (
                "relationship",
                "relationship",
                self._relationships,
                staged.relationships,
            ),
            (
                "capability_handler",
                "capability handler",
                self._capability_handlers,
                staged.capability_handlers,
            ),
            (
                "runtime_provider",
                "runtime provider",
                self._runtime_provider_factories,
                staged.runtime_provider_factories,
            ),
            (
                "state_schema",
                "state schema",
                self._state_schemas,
                staged.state_schemas,
            ),
        )
        for kind, label, installed, incoming in contribution_sets:
            duplicate = next((key for key in incoming if key in installed), None)
            if duplicate is not None:
                owner = self.owner_id(kind, duplicate)
                raise ValueError(
                    f"{label} {duplicate!r} is already owned by plugin {owner!r}"
                )

        for preset in staged.legion_presets.values():
            from backend.legions.presets import plugin_preset_record
            # Validate against a temporary registry, before publishing any contribution.
            candidate = PluginRegistry()
            candidate._nodes = {**self._nodes, **staged.nodes}
            candidate._relationships = {**self._relationships, **staged.relationships}
            candidate._owners = {**self._owners,
                **{("node_type", key): staged.descriptor.id for key in staged.nodes},
                **{("relationship", key): staged.descriptor.id for key in staged.relationships},
                **{("runtime_provider", key): staged.descriptor.id for key in staged.runtime_provider_factories},
                **{("capability_handler", key): staged.descriptor.id for key in staged.capability_handlers},
                **{("state_schema", key): staged.descriptor.id for key in staged.state_schemas}}
            record = plugin_preset_record(preset, candidate)
            nodes = {node.key: node for node in record.blueprint.nodes}
            for edge in record.blueprint.edges:
                if edge.source not in nodes or edge.target not in nodes:
                    raise ValueError("Preset edges must reference preset nodes")
                candidate.validate_relationship_order(nodes[edge.source].type, nodes[edge.target].type, edge.relationship)
                candidate.validate_direction(edge.relationship, edge.direction)

        # Source and schema 3 Packs may collect canonical host cards without
        # taking ownership.
        host_cards = {key for key in self._nodes
                      if self.node_type_owner_id(key) == "open-agent-world.core"}
        covered = set()
        for pack in staged.packs.values():
            if not pack.cards and not staged.legion_presets:
                raise ValueError("A Pack must contain cards or Legion presets")
            if pack.artwork_asset is not None and pack.artwork_asset not in staged.assets:
                raise ValueError("pack artwork must reference an asset registered by the same plugin")
            if len(set(pack.cards)) != len(pack.cards):
                raise ValueError(f"pack {pack.id!r} contains duplicate cards")
            if not set(pack.cards) <= staged.nodes.keys() | host_cards:
                raise ValueError(f"pack {pack.id!r} may reference only this plugin's node types or host cards")
            covered.update(pack.cards)
        if staged.nodes.keys() - covered:
            raise ValueError("Every registered node type must belong to a pack")

        for schema in staged.state_schemas.values():
            if not isinstance(schema, StateSchema):
                raise TypeError("state schema must be a StateSchema")
            if "." not in schema.id:
                raise ValueError("state schema ids must be namespaced")

        for definition in staged.nodes.values():
            validate_tutorials(definition.tutorials)
            definition.resolved_presentation()
            if definition.default_equipment:
                if not all(isinstance(item, DefaultEquipment) for item in definition.default_equipment):
                    raise TypeError("Default equipment entries must be DefaultEquipment declarations")
                if "core.agent" not in definition.traits:
                    raise ValueError("Default equipment requires an Agent owner")
                if len(definition.default_equipment) > 16 or len({item.key for item in definition.default_equipment}) != len(definition.default_equipment):
                    raise ValueError("Default equipment requires at most 16 unique slots")
                for item in definition.default_equipment:
                    target = staged.nodes.get(item.type)
                    relationship = staged.relationships.get(item.relationship)
                    if target is None or relationship is None:
                        raise ValueError("Default equipment types and relationships must belong to the declaring plugin")
                    if (target.document is None or target.document.initial_value is None or target.state is not None
                        or target.lifecycle or target.execution or target.template_handler or target.resource_actions
                        or target.default_equipment or "core.agent" in target.traits):
                        raise ValueError("Default equipment supports inert document resources only")
                    def matches(source, dest):
                        return ((not relationship.source_types or source.id in relationship.source_types)
                            and (not relationship.target_types or dest.id in relationship.target_types)
                            and relationship.source_traits <= source.traits and relationship.target_traits <= dest.traits)
                    if relationship.generated or not (matches(definition, target) or matches(target, definition)):
                        raise ValueError("Default equipment relationship cannot connect its owner and target")
            if definition.card_face is not None:
                face = CardFaceSpec.model_validate(definition.card_face)
                if face.image_asset is not None:
                    asset = staged.assets.get(face.image_asset)
                    if asset is None or not asset.media_type.startswith("image/"):
                        raise ValueError("card face image must reference an image asset registered by the same plugin")
            if definition.authoring is not None:
                from backend.packs.interfaces import CardInterface
                interface = CardInterface.model_validate(definition.authoring)
                actions = {interface.action, *(binding.logic_id for binding in interface.function.button_bindings or [])}
                if actions - definition.resource_actions.keys():
                    raise ValueError("Card interfaces must bind registered resource actions")
            elif 'oaw.authoring-card' in definition.frontend.values():
                raise ValueError("The authoring renderer requires a validated Card interface")
            if definition.icon_asset is not None and definition.icon_asset not in staged.assets:
                raise ValueError("node icon must reference an asset registered by the same plugin")
            for slot, reference in definition.frontend.items():
                if slot not in {"preview", "body", "settings", "workspace"}:
                    raise ValueError(f"unknown frontend slot {slot!r}")
                self.validate_identifier(reference, "frontend view")
            if (
                not definition.statuses
                or definition.default_status not in definition.statuses
            ):
                raise ValueError(
                    f"node type {definition.id!r} has an invalid default status"
                )
            if (
                definition.template_status is not None
                and definition.template_status not in definition.statuses
            ):
                raise ValueError(
                    f"node type {definition.id!r} has an invalid template status"
                )
            if definition.template_status is not None:
                default_config = definition.config_model().model_dump(mode="json")
                try:
                    validated_template_config = definition.config_model.model_validate({
                        **default_config,
                        "status": definition.template_status,
                    }).model_dump(mode="json")
                except ValidationError as exc:
                    raise ValueError(
                        f"node type {definition.id!r} template status cannot be "
                        "represented by its config model"
                    ) from exc
                if validated_template_config.get("status") != definition.template_status:
                    raise ValueError(
                        f"node type {definition.id!r} template status cannot be "
                        "represented by its config model"
                    )
            if definition.template_handler is not None and not definition.templateable:
                raise ValueError(
                    f"node type {definition.id!r} provides a template handler but is not "
                    "templateable"
                )
            if (
                definition.template_handler is not None
                and definition.template_handler.payload_version < 1
            ):
                raise ValueError(
                    f"node type {definition.id!r} template payload version must be positive"
                )
            for name, action in definition.resource_actions.items():
                if not _IDENTIFIER.fullmatch(name) or not callable(action.handler):
                    raise ValueError("resource actions require a valid name and handler")
                if action.capability_kind and action.capability_kind not in staged.capability_handlers:
                    raise ValueError("resource action capabilities must be owned by the same plugin")
            if definition.deployment is not None:
                if not isinstance(definition.deployment, NodeDeploymentDefinition):
                    raise TypeError("deployment must be a NodeDeploymentDefinition")
                definition.deployment.validate_node(definition)
            if definition.document is not None:
                document = definition.document
                document.model()
                if document.initial_value is not None:
                    document.model.model_validate(document.initial_value)
                if not all(callable(fn) for fn in (document.capture, document.summarize, document.remap_references)):
                    raise TypeError("document capture, summary and reference remapping must be callable")
                for name, action in document.actions.items():
                    if not name or name == "replace" or not callable(action.handler):
                        raise ValueError("document actions require a handler and cannot use reserved name 'replace'")
                    if (action.capability_kind and action.capability_kind not in staged.capability_handlers
                        and not (action.read_only and any(
                            existing_action.read_only and existing_action.capability_kind == action.capability_kind
                            for existing_node in self._nodes.values() if existing_node.document
                            for existing_action in existing_node.document.actions.values()))):
                        raise ValueError("document action capabilities must be owned by the same plugin, or reuse an installed read-only operation")
            if definition.summoning:
                if definition.document is None or definition.summoning.capability_kind not in staged.capability_handlers:
                    raise ValueError("Summoning requires a document and a capability owned by the same plugin")
                if definition.container is None:
                    raise ValueError("Summoning catalogs require a container")
            if definition.container:
                if definition.container.member_display not in {"cards", "workspace"}:
                    raise ValueError("Unknown container member display")
                if definition.container.member_display == "workspace" and not definition.frontend.get("workspace"):
                    raise ValueError("Workspace member display requires a frontend workspace")
            if definition.container and definition.container.document_field:
                member = staged.nodes.get(definition.container.member_type)
                if member is None or member.document is None or member.lifecycle is not None:
                    raise ValueError("Document collections require an owned document-only member type")
                if definition.document is None or definition.container.document_field not in definition.document.model.model_fields:
                    raise ValueError("Document collection field must be present in the container document")
            if definition.execution is not None:
                execution = definition.execution
                if definition.document is None:
                    raise ValueError("executable nodes require a document")
                if not all(callable(fn) for fn in (execution.items, execution.apply_outcome, execution.policy)):
                    raise TypeError("execution callbacks must be callable")
                if not execution.summoning and execution.executor_relationship not in staged.relationships:
                    raise ValueError("executor relationship must be owned by the same plugin")
                if execution.control_capability_kind and execution.control_capability_kind not in staged.capability_handlers:
                    raise ValueError("execution control capability must be owned by the same plugin")
            try:
                default_config = definition.config_model().model_dump(mode="json")
            except ValidationError as exc:
                raise ValueError(
                    f"node type {definition.id!r} config model must provide defaults "
                    "for palette creation"
                ) from exc
            if definition.data_consumer is not None:
                consumer = definition.data_consumer
                if any(not isinstance(default_config.get(name), str)
                       for name in (consumer.source_field, consumer.schema_field)):
                    raise ValueError("data consumer fields must name string config fields with defaults")

        operations: dict[str, CapabilityDefinition] = {}
        for definition in (*self._capabilities.values(), *staged.capabilities.values()):
            names = [definition.tool_name, definition.target_parameter,
                     *(s.parameter for s in definition.selectors), *(s.argument for s in definition.selectors)]
            if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", name) or keyword.iskeyword(name) for name in names):
                raise ValueError("Capability operation and selector names must be valid tool identifiers")
            selectors = [definition.target_parameter, *(s.parameter for s in definition.selectors)]
            if len(set(selectors)) != len(selectors) or set(selectors) & definition.input_schema.get("properties", {}).keys():
                raise ValueError("Capability selectors must not collide with operation parameters")
            arguments = [s.argument for s in definition.selectors]
            if len(set(arguments)) != len(arguments) or set(arguments) & definition.input_schema.get("properties", {}).keys():
                raise ValueError("Selector destination arguments must be unique and separate from operation parameters")
            if any(s.argument in set(selectors) - {s.parameter} for s in definition.selectors):
                raise ValueError("Selector destination arguments must not overwrite other selectors")
            if any(s.include_members and not s.document_action for s in definition.selectors):
                raise ValueError("Member selectors require an authorized document action")
            if any(not isinstance(s.required, bool) for s in definition.selectors):
                raise ValueError("Selector required must be a boolean")
            if definition.kind in staged.capabilities and definition.kind not in staged.capability_handlers:
                raise ValueError("Capability definitions must own their handlers")
            previous = operations.get(definition.tool_name)
            if previous is not None and (
                previous.description, previous.input_schema, previous.target_parameter, previous.selectors, previous.target_capabilities
            ) != (definition.description, definition.input_schema, definition.target_parameter, definition.selectors, definition.target_capabilities):
                raise ValueError(f"Conflicting contracts for operation {definition.tool_name!r}")
            operations[definition.tool_name] = definition

        known_handlers = (
            self._capability_handlers.keys() | staged.capability_handlers.keys()
        )
        for definition in staged.capabilities.values():
            required = set(definition.target_capabilities)
            for selector in definition.selectors:
                required.update(selector.capability_kinds)
            if required - known_handlers:
                raise ValueError(f"Capability {definition.kind!r} references unregistered capability kinds: "
                                 + ", ".join(sorted(required - known_handlers)))
        for definition in staged.relationships.values():
            if not definition.directions or not definition.directions <= {
                "forward",
                "bidirectional",
            }:
                raise ValueError(
                    f"relationship {definition.id!r} has invalid directions"
                )
            missing = [
                grant.kind
                for grant in definition.capabilities
                if grant.kind not in known_handlers
            ]
            if missing:
                raise ValueError(
                    f"relationship {definition.id!r} references unregistered "
                    "capability handlers: " + ", ".join(missing)
                )

    def _commit_owned(
        self,
        kind: str,
        plugin_id: str,
        target: dict[str, Any],
        incoming: dict[str, Any],
    ) -> None:
        target.update(incoming)
        self._owners.update({
            (kind, identifier): plugin_id for identifier in incoming
        })

    def plugins(self) -> tuple[PluginDescriptor, ...]:
        return tuple(self._plugins.values())

    def asset(self, plugin_id: str, asset_id: str) -> PluginAsset:
        return self._assets[(plugin_id, asset_id)]

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

    def is_enabled(self, plugin_id: str) -> bool:
        return self.has_plugin(plugin_id) and plugin_id not in self._disabled and not self.unavailable_reason(plugin_id)

    def unavailable_reason(self, plugin_id: str):
        return next((row for row in self.load_failures if row.get('owner') == plugin_id), None)

    def mark_unavailable(self, plugin_id: str, phase: str, error: BaseException, *, source="lifecycle") -> set[str]:
        """Transient availability, separate from the user's durable enabled intent."""
        from backend.plugins.failures import raise_if_unsafe
        raise_if_unsafe(error)
        if plugin_id == 'open-agent-world.core':
            raise error
        affected = {plugin_id}
        while True:
            downstream = {p.id for p in self.plugins() if set(p.requires_plugins) & affected}
            if downstream <= affected:
                break
            affected.update(downstream)
        for owner in sorted(affected):
            if not self.unavailable_reason(owner):
                self.load_failures.append(dict(id=owner, owner=owner, source=source, state='unavailable',
                    phase=phase if owner == plugin_id else 'dependency',
                    error=str(error) if owner == plugin_id else f'Required plugin {plugin_id} is unavailable',
                    retry='repair_and_restart'))
        return affected

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if not self.has_plugin(plugin_id):
            raise PluginUnavailableError(f"Plugin {plugin_id!r} is not installed")
        if enabled:
            self._disabled.discard(plugin_id)
        else:
            self._disabled.add(plugin_id)

    def _assert_enabled(self, kind: str, identifier: str) -> None:
        owner = self._owners.get((kind, identifier))
        if owner is not None and (owner in self._disabled or self.unavailable_reason(owner)):
            raise PluginUnavailableError(f"Plugin {owner!r} is unavailable or disabled")

    def assert_runtime_provider_enabled(self, provider_id: str) -> None:
        self._assert_enabled("runtime_provider", provider_id)
        if any(row['source'] == 'runtime_provider' and row['id'] == provider_id for row in self.load_failures):
            raise PluginUnavailableError(f"Runtime provider {provider_id!r} is unavailable")

    def has_trait(self, type_id: str, trait: str) -> bool:
        definition = self._nodes.get(type_id)
        return bool(definition and self.is_enabled(self.node_type_owner_id(type_id)) and trait in definition.traits)

    def legion_presets(self) -> tuple[LegionPresetDefinition | LegionBlueprintPreset, ...]:
        return tuple(value.model_copy(deep=True) for key, value in self._legion_presets.items()
                     if self.is_enabled(self.owner_id("legion_preset", key))
                     and all(self.is_enabled(self.node_type_owner_id(node.type)) for node in value.nodes)
                     and all(self.is_enabled(self.relationship_owner_id(edge.relationship)) for edge in value.edges))

    def owner_id(self, kind: str, contribution_id: str) -> str:
        try:
            return self._owners[(kind, contribution_id)]
        except KeyError as exc:
            raise ValueError(
                f"{kind.replace('_', ' ')} {contribution_id!r} is not registered"
            ) from exc

    def node_type_owner_id(self, type_id: str) -> str:
        return self.owner_id("node_type", type_id)

    def relationship_owner_id(self, relationship_id: str) -> str:
        return self.owner_id("relationship", relationship_id)

    def capability_handler_owner_id(self, kind: str) -> str:
        return self.owner_id("capability_handler", kind)

    def runtime_provider_owner_id(self, provider_id: str) -> str:
        return self.owner_id("runtime_provider", provider_id)

    def state_schema_owner_id(self, schema_id: str) -> str:
        return self.owner_id("state_schema", schema_id)

    def state_schema(self, schema_id: str) -> StateSchema:
        try:
            return self._state_schemas[schema_id]
        except KeyError as exc:
            raise ValueError(f"state schema {schema_id!r} is not registered") from exc

    def state_schemas(self) -> tuple[StateSchema, ...]:
        return tuple(self._state_schemas.values())

    def create_runtime_provider(
        self,
        provider_id: str,
        capability_provider: AgentCapabilityProvider,
        model_connection_resolver: "ModelConnectionResolver | None" = None,
        managed_context_store: Any = None,
        **options: Any,
    ) -> RuntimeProvider:
        self._assert_enabled("runtime_provider", provider_id)
        try:
            factory = self._runtime_provider_factories[provider_id]
        except KeyError as exc:
            raise ValueError(
                f"runtime provider {provider_id!r} is not registered"
            ) from exc
        if provider_id in self._runtime_provider_model_resolvers:
            if model_connection_resolver is None:
                raise RuntimeError(f"runtime provider {provider_id!r} requires OAW model connections")
            options["model_connection_resolver"] = model_connection_resolver
        if provider_id in self._runtime_provider_context_stores:
            if managed_context_store is None:
                raise RuntimeError(f"runtime provider {provider_id!r} requires OAW managed context")
            options["context_store"] = managed_context_store
        provider = factory(capability_provider, **options)
        from backend.agents import RuntimeProvider

        if not isinstance(provider, RuntimeProvider):
            raise TypeError(
                f"runtime provider factory {provider_id!r} returned an invalid provider"
            )
        return provider

    def has_runtime_provider(self, provider_id: str) -> bool:
        return provider_id in self._runtime_provider_factories and self.is_enabled(self.runtime_provider_owner_id(provider_id))

    def background_job_provider(self, provider_id):
        self._assert_enabled("background_job_provider", provider_id)
        try:
            return self._background_job_providers[provider_id]
        except KeyError as exc:
            raise PluginUnavailableError(f"Background job provider {provider_id!r} is not installed") from exc

    def background_job_providers(self):
        return {key: value for key, value in self._background_job_providers.items()
                if self.is_enabled(self.owner_id("background_job_provider", key))}

    def node_type(self, type_id: str) -> NodeTypeDefinition:
        self._assert_enabled("node_type", type_id)
        try:
            return self._nodes[type_id]
        except KeyError as exc:
            raise GraphValidationError(f"node type {type_id!r} is not registered") from exc

    def relationship(self, relationship_id: str) -> RelationshipDefinition:
        self._assert_enabled("relationship", relationship_id)
        try:
            return self._relationships[relationship_id]
        except KeyError as exc:
            raise GraphValidationError(
                f"relationship {relationship_id!r} is not registered"
            ) from exc

    def capability_handler(self, kind: str) -> CapabilityHandler:
        self._assert_enabled("capability_handler", kind)
        try:
            return self._capability_handlers[kind]
        except KeyError as exc:
            raise GraphValidationError(
                f"capability handler {kind!r} is not registered"
            ) from exc

    def capability_definition(self, kind: str) -> CapabilityDefinition:
        self._assert_enabled("capability", kind)
        try:
            return self._capabilities[kind]
        except KeyError as exc:
            raise GraphValidationError(f"capability {kind!r} is not registered") from exc

    def validate_config(self, type_id: str, value: dict[str, Any]) -> dict[str, Any]:
        definition = self.node_type(type_id)
        try:
            model = definition.config_model.model_validate(value)
        except ValidationError as exc:
            raise GraphValidationError(
                f"invalid {type_id} configuration: {exc}"
            ) from exc
        return model.model_dump(mode="json")

    def validate_status(self, type_id: str, status: str) -> None:
        definition = self.node_type(type_id)
        if status not in definition.statuses:
            raise GraphValidationError(
                f"status {status!r} is not valid for {type_id}"
            )

    def validate_creation_fields(
        self, type_id: str, *, content: str | None, data_base64: str | None
    ) -> None:
        definition = self.node_type(type_id)
        if content is not None and "content" not in definition.creation_fields:
            raise GraphValidationError(f"content is not valid for {type_id} nodes")
        if data_base64 is not None and "data_base64" not in definition.creation_fields:
            raise GraphValidationError(f"data_base64 is not valid for {type_id} nodes")

    def resolve_relationship(
        self, source_type: str, target_type: str, relationship_id: str
    ) -> tuple[RelationshipDefinition, bool]:
        definition = self.relationship(relationship_id)
        if self._matches(definition, source_type, target_type):
            return definition, False
        if self._matches(definition, target_type, source_type):
            return definition, True
        raise GraphValidationError(
            f"{source_type} and {target_type} do not allow {relationship_id!r}"
        )

    def relationship_options(self, source_type: str, target_type: str) -> list[RelationshipDefinition]:
        available = [item for item in self._relationships.values() if self.is_enabled(self.relationship_owner_id(item.id))]
        forward = [item for item in available if self._matches(item, source_type, target_type)]
        return forward or [item for item in available if self._matches(item, target_type, source_type)]

    def validate_relationship_order(
        self, source_type: str, target_type: str, relationship_id: str
    ) -> RelationshipDefinition:
        definition = self.relationship(relationship_id)
        if not self._matches(definition, source_type, target_type):
            raise GraphValidationError(
                f"{source_type} -> {target_type} does not allow {relationship_id!r}"
            )
        return definition

    def validate_direction(self, relationship_id: str, direction: str) -> None:
        definition = self.relationship(relationship_id)
        if direction not in definition.directions:
            allowed = ", ".join(sorted(definition.directions))
            raise GraphValidationError(
                f"relationship {relationship_id!r} does not allow direction {direction!r}; "
                f"allowed directions: {allowed}"
            )

    def catalog(self, *, include_disabled: bool = False) -> PluginCatalog:
        return PluginCatalog(
            plugins=list(self._plugins.values()),
            enabled_plugin_ids=[owner for owner in self._plugins if self.is_enabled(owner)],
            packs=list(self._packs.values()),
            frontend_modules={key: value for key, value in self.frontend_modules.items() if self.is_enabled(key)},
            node_types=[
                item.catalog_item(self.node_type_owner_id(item.id))
                for item in self._nodes.values()
                if include_disabled or self.is_enabled(self.node_type_owner_id(item.id))
            ],
            relationships=[
                item.catalog_item(self.relationship_owner_id(item.id))
                for item in self._relationships.values()
                if include_disabled or self.is_enabled(self.relationship_owner_id(item.id))
            ],
        )

    def _matches(
        self,
        definition: RelationshipDefinition,
        source_type: str,
        target_type: str,
    ) -> bool:
        source = self.node_type(source_type)
        target = self.node_type(target_type)
        return (
            (not definition.source_types or source_type in definition.source_types)
            and (not definition.target_types or target_type in definition.target_types)
            and definition.source_traits <= source.traits
            and definition.target_traits <= target.traits
        )

    @staticmethod
    def validate_identifier(value: str, label: str) -> None:
        if len(value) > 128 or _IDENTIFIER.fullmatch(value) is None:
            raise ValueError(
                f"{label} id {value!r} is not a valid namespaced identifier"
            )
