from __future__ import annotations

import re
import keyword
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.errors import GraphValidationError, PluginCompatibilityError, PluginUnavailableError
from backend.plugins.lifecycle import NodeLifecycleHandler
from backend.plugins.template import NodeTemplateHandler

if TYPE_CHECKING:
    from backend.plugins.summoning import NodeSummoningDefinition
    from backend.agents import AgentCapabilityProvider, RuntimeProvider
    from backend.capabilities import Capability
    from backend.plugins.capability import CapabilityContext
    from backend.state.schema import StateSchema


from backend.plugins.documents import NodeDocumentDefinition
from backend.plugins.containers import NodeContainerDefinition
from backend.plugins.execution import NodeExecutionDefinition

PLUGIN_API_VERSION = "1.15"
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


class PackDefinition(BaseModel):
    """A stable distribution manifest referencing canonical node type IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    cards: tuple[str, ...] = Field(min_length=1)
    artwork_asset: str | None = Field(default=None, min_length=1, max_length=128)
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class PackCatalogItem(PackDefinition):
    plugin_id: str
    compatibility: bool = False
    artwork_url: str | None = None


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


class NodeTypeCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    plugin_id: str
    label: str
    description: str
    icon: str
    icon_url: str | None = None
    frontend: dict[str, str] = Field(default_factory=dict)
    color: str
    deck_id: str
    deck_label: str
    deck_icon: str
    deck_revision: int = 1
    default_name: str
    default_size: dict[str, float]
    default_status: str
    traits: list[str]
    surfaces: dict[str, bool]
    has_document: bool = False
    transformations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    has_execution: bool = False
    container: dict[str, Any] | None = None
    summoning: dict[str, Any] | None = None
    default_config: dict[str, Any]
    config_schema: dict[str, Any] = Field(default_factory=dict)
    user_creatable: bool
    templateable: bool


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


class PluginCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: list[PluginDescriptor]
    node_types: list[NodeTypeCatalogItem]
    relationships: list[RelationshipCatalogItem]
    packs: list[PackCatalogItem] = Field(default_factory=list)


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
    creation_fields: frozenset[str] = frozenset()
    # Persisted node types are not necessarily standalone objects. Managed
    # containers, for example, must be created through their domain operation.
    user_creatable: bool = True
    lifecycle: NodeLifecycleHandler | None = None
    templateable: bool = False
    template_status: str | None = None
    template_handler: NodeTemplateHandler | None = None
    document: NodeDocumentDefinition | None = None
    execution: NodeExecutionDefinition | None = None
    container: NodeContainerDefinition | None = None
    summoning: NodeSummoningDefinition | None = None

    def catalog_item(self, plugin_id: str) -> NodeTypeCatalogItem:
        default_config = self.config_model().model_dump(mode="json")
        return NodeTypeCatalogItem(
            id=self.id,
            plugin_id=plugin_id,
            label=self.label,
            description=self.description,
            icon=self.icon,
            icon_url=f"/api/plugins/{plugin_id}/assets/{self.icon_asset}" if self.icon_asset else None,
            frontend=dict(self.frontend),
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
            surfaces={
                "preview": bool(self.surfaces.get("preview", True)),
                "inspector": bool(self.surfaces.get("inspector", True)),
                "workspace": bool(self.surfaces.get("workspace", False)),
            },
            default_config=default_config,
            config_schema=self.config_model.model_json_schema(),
            has_document=self.document is not None,
            transformations={key: {"label": item.label, "source_traits": sorted(item.source_traits)} for key, item in self.document.transformations.items()} if self.document else {},
            has_execution=self.execution is not None,
            container=self.container.catalog_item() if self.container else None,
            summoning={} if self.summoning else None,
            user_creatable=self.user_creatable,
            templateable=self.templateable,
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
        self.state_schemas: dict[str, StateSchema] = {}
        self.assets: dict[str, PluginAsset] = {}
        self.packs: dict[str, PackDefinition] = {}

    def register_pack(self, definition: PackDefinition) -> None:
        if not isinstance(definition, PackDefinition):
            raise TypeError("pack must be a PackDefinition")
        self._add(self.packs, definition.id, definition, "pack")

    def register_asset(self, asset: PluginAsset) -> None:
        if not isinstance(asset, PluginAsset):
            raise TypeError("asset must be a PluginAsset")
        self._add(self.assets, asset.id, asset, "asset")

    def register_node_type(self, definition: NodeTypeDefinition) -> None:
        self._add(self.nodes, definition.id, definition, "node type")

    def register_relationship(self, definition: RelationshipDefinition) -> None:
        self._add(self.relationships, definition.id, definition, "relationship")

    def register_capability_handler(self, kind: str, handler: CapabilityHandler) -> None:
        self._add(self.capability_handlers, kind, handler, "capability handler")

    def register_capability(self, definition: CapabilityDefinition, handler: CapabilityHandler) -> None:
        self._add(self.capabilities, definition.kind, definition, "capability")
        self.register_capability_handler(definition.kind, handler)

    def register_runtime_provider(
        self, provider_id: str, factory: RuntimeProviderFactory
    ) -> None:
        self._add(
            self.runtime_provider_factories,
            provider_id,
            factory,
            "runtime provider",
        )

    def register_state_schema(self, schema: StateSchema) -> None:
        self._add(self.state_schemas, schema.id, schema, "state schema")

    @staticmethod
    def _add(target: dict[str, Any], identifier: str, value: Any, label: str) -> None:
        PluginRegistry.validate_identifier(identifier, label)
        if identifier in target:
            raise ValueError(f"{label} {identifier!r} is registered twice by this plugin")
        target[identifier] = value


class PluginRegistry:
    """Authoritative registry of installed plugins and their owned contributions."""

    def __init__(self) -> None:
        self.runtime_requirements: dict[str, tuple[str, ...]] = {}
        self._plugins: dict[str, PluginDescriptor] = {}
        self._nodes: dict[str, NodeTypeDefinition] = {}
        self._relationships: dict[str, RelationshipDefinition] = {}
        self._capability_handlers: dict[str, CapabilityHandler] = {}
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._runtime_provider_factories: dict[str, RuntimeProviderFactory] = {}
        self._state_schemas: dict[str, StateSchema] = {}
        self._owners: dict[tuple[str, str], str] = {}
        self._assets: dict[tuple[str, str], PluginAsset] = {}
        self._packs: dict[str, PackCatalogItem] = {}
        self._disabled: set[str] = set()

    def install(self, plugin: Plugin) -> None:
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
        register = getattr(plugin, "register", None)
        if not callable(register):
            raise TypeError(f"plugin {descriptor.id!r} must define register(registration)")

        staged = PluginRegistration(descriptor)
        register(staged)
        compatibility = bool(staged.nodes) and not staged.packs
        if compatibility:
            staged.register_pack(PackDefinition(
                id=f"{descriptor.id}.default", name=descriptor.name or descriptor.id,
                description=descriptor.description or "", cards=tuple(staged.nodes),
            ))
        self._normalize_capabilities(staged)
        self._validate_registration(staged)

        self._plugins[descriptor.id] = descriptor
        self._commit_owned("pack", descriptor.id, self._packs, {
            key: PackCatalogItem(**pack.model_dump(), plugin_id=descriptor.id, compatibility=compatibility,
                artwork_url=f"/api/plugins/{descriptor.id}/assets/{pack.artwork_asset}" if pack.artwork_asset else None)
            for key, pack in staged.packs.items()
        })
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
        self._commit_owned(
            "state_schema", descriptor.id, self._state_schemas, staged.state_schemas
        )

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

        covered = set()
        for pack in staged.packs.values():
            if pack.artwork_asset is not None and pack.artwork_asset not in staged.assets:
                raise ValueError("pack artwork must reference an asset registered by the same plugin")
            if len(set(pack.cards)) != len(pack.cards):
                raise ValueError(f"pack {pack.id!r} contains duplicate cards")
            if not set(pack.cards) <= staged.nodes.keys():
                raise ValueError(f"pack {pack.id!r} may reference only this plugin's node types")
            covered.update(pack.cards)
        if staged.nodes.keys() - covered:
            raise ValueError("Every registered node type must belong to a pack")

        for schema in staged.state_schemas.values():
            if not isinstance(schema, StateSchema):
                raise TypeError("state schema must be a StateSchema")
            if "." not in schema.id:
                raise ValueError("state schema ids must be namespaced")

        for definition in staged.nodes.values():
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
                if execution.executor_relationship not in staged.relationships:
                    raise ValueError("executor relationship must be owned by the same plugin")
                if execution.control_capability_kind and execution.control_capability_kind not in staged.capability_handlers:
                    raise ValueError("execution control capability must be owned by the same plugin")
            try:
                definition.config_model()
            except ValidationError as exc:
                raise ValueError(
                    f"node type {definition.id!r} config model must provide defaults "
                    "for palette creation"
                ) from exc

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
        return self.has_plugin(plugin_id) and plugin_id not in self._disabled

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if not self.has_plugin(plugin_id):
            raise PluginUnavailableError(f"Plugin {plugin_id!r} is not installed")
        if enabled:
            self._disabled.discard(plugin_id)
        else:
            self._disabled.add(plugin_id)

    def _assert_enabled(self, kind: str, identifier: str) -> None:
        owner = self._owners.get((kind, identifier))
        if owner in self._disabled:
            raise PluginUnavailableError(f"Plugin {owner!r} is disabled")

    def assert_runtime_provider_enabled(self, provider_id: str) -> None:
        self._assert_enabled("runtime_provider", provider_id)

    def has_trait(self, type_id: str, trait: str) -> bool:
        return trait in self.node_type(type_id).traits

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
        **options: Any,
    ) -> RuntimeProvider:
        self._assert_enabled("runtime_provider", provider_id)
        try:
            factory = self._runtime_provider_factories[provider_id]
        except KeyError as exc:
            raise ValueError(
                f"runtime provider {provider_id!r} is not registered"
            ) from exc
        provider = factory(capability_provider, **options)
        from backend.agents import RuntimeProvider

        if not isinstance(provider, RuntimeProvider):
            raise TypeError(
                f"runtime provider factory {provider_id!r} returned an invalid provider"
            )
        return provider

    def has_runtime_provider(self, provider_id: str) -> bool:
        return provider_id in self._runtime_provider_factories and self.is_enabled(self.runtime_provider_owner_id(provider_id))

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
            packs=list(self._packs.values()),
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
