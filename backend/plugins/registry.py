from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.errors import GraphValidationError, PluginCompatibilityError
from backend.plugins.lifecycle import NodeLifecycleHandler

if TYPE_CHECKING:
    from backend.agents import AgentCapabilityProvider, RuntimeProvider
    from backend.capabilities import Capability
    from backend.plugins.capability import CapabilityContext
    from backend.state.schema import StateSchema


PLUGIN_API_VERSION = "1.0"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$")


class PluginDescriptor(BaseModel):
    """Stable identity and compatibility metadata for one installed plugin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str = Field(min_length=1, max_length=64)
    plugin_api_version: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


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
    color: str
    deck_id: str
    deck_label: str
    deck_icon: str
    default_name: str
    default_size: dict[str, float]
    default_status: str
    traits: list[str]
    surfaces: dict[str, bool]
    default_config: dict[str, Any]


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


class PluginCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: list[PluginDescriptor]
    node_types: list[NodeTypeCatalogItem]
    relationships: list[RelationshipCatalogItem]


@dataclass(frozen=True, slots=True)
class CapabilityGrantDefinition:
    kind: str
    tool_prefix: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)


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
    traits: frozenset[str] = frozenset()
    surfaces: Mapping[str, bool] = field(
        default_factory=lambda: {
            "preview": True,
            "inspector": True,
            "workspace": False,
        }
    )
    creation_fields: frozenset[str] = frozenset()
    lifecycle: NodeLifecycleHandler | None = None

    def catalog_item(self, plugin_id: str) -> NodeTypeCatalogItem:
        default_config = self.config_model().model_dump(mode="json")
        return NodeTypeCatalogItem(
            id=self.id,
            plugin_id=plugin_id,
            label=self.label,
            description=self.description,
            icon=self.icon,
            color=self.color,
            deck_id=self.deck_id,
            deck_label=self.deck_label,
            deck_icon=self.deck_icon,
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
        self.runtime_provider_factories: dict[str, RuntimeProviderFactory] = {}
        self.state_schemas: dict[str, StateSchema] = {}

    def register_node_type(self, definition: NodeTypeDefinition) -> None:
        self._add(self.nodes, definition.id, definition, "node type")

    def register_relationship(self, definition: RelationshipDefinition) -> None:
        self._add(self.relationships, definition.id, definition, "relationship")

    def register_capability_handler(self, kind: str, handler: CapabilityHandler) -> None:
        self._add(self.capability_handlers, kind, handler, "capability handler")

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
        self._plugins: dict[str, PluginDescriptor] = {}
        self._nodes: dict[str, NodeTypeDefinition] = {}
        self._relationships: dict[str, RelationshipDefinition] = {}
        self._capability_handlers: dict[str, CapabilityHandler] = {}
        self._runtime_provider_factories: dict[str, RuntimeProviderFactory] = {}
        self._state_schemas: dict[str, StateSchema] = {}
        self._owners: dict[tuple[str, str], str] = {}

    def install(self, plugin: Plugin) -> None:
        descriptor = getattr(plugin, "descriptor", None)
        if not isinstance(descriptor, PluginDescriptor):
            raise TypeError("plugin descriptor must be a PluginDescriptor")
        self.validate_identifier(descriptor.id, "plugin")
        if descriptor.plugin_api_version != PLUGIN_API_VERSION:
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
        self._validate_registration(staged)

        self._plugins[descriptor.id] = descriptor
        self._commit_owned("node_type", descriptor.id, self._nodes, staged.nodes)
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

    def _validate_registration(self, staged: PluginRegistration) -> None:
        from backend.state.schema import StateSchema

        contribution_sets = (
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

        for schema in staged.state_schemas.values():
            if not isinstance(schema, StateSchema):
                raise TypeError("state schema must be a StateSchema")
            if "." not in schema.id:
                raise ValueError("state schema ids must be namespaced")

        for definition in staged.nodes.values():
            if (
                not definition.statuses
                or definition.default_status not in definition.statuses
            ):
                raise ValueError(
                    f"node type {definition.id!r} has an invalid default status"
                )
            try:
                definition.config_model()
            except ValidationError as exc:
                raise ValueError(
                    f"node type {definition.id!r} config model must provide defaults "
                    "for palette creation"
                ) from exc

        known_handlers = (
            self._capability_handlers.keys() | staged.capability_handlers.keys()
        )
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

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

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
        return provider_id in self._runtime_provider_factories

    def node_type(self, type_id: str) -> NodeTypeDefinition:
        try:
            return self._nodes[type_id]
        except KeyError as exc:
            raise GraphValidationError(f"node type {type_id!r} is not registered") from exc

    def relationship(self, relationship_id: str) -> RelationshipDefinition:
        try:
            return self._relationships[relationship_id]
        except KeyError as exc:
            raise GraphValidationError(
                f"relationship {relationship_id!r} is not registered"
            ) from exc

    def capability_handler(self, kind: str) -> CapabilityHandler:
        try:
            return self._capability_handlers[kind]
        except KeyError as exc:
            raise GraphValidationError(
                f"capability handler {kind!r} is not registered"
            ) from exc

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

    def catalog(self) -> PluginCatalog:
        return PluginCatalog(
            plugins=list(self._plugins.values()),
            node_types=[
                item.catalog_item(self.node_type_owner_id(item.id))
                for item in self._nodes.values()
            ],
            relationships=[
                item.catalog_item(self.relationship_owner_id(item.id))
                for item in self._relationships.values()
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
