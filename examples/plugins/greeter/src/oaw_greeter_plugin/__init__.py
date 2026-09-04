"""Minimal, authoritative Open Agent World plugin example."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from open_agent_world.plugin_api import (
    Capability,
    CapabilityContext,
    CapabilityGrantDefinition,
    Card,
    CardCreate,
    CardPatch,
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeLifecycleTransaction,
    NodeTypeDefinition,
    PluginDescriptor,
    PluginRegistration,
    RelationshipDefinition,
    ResourceValidationError,
)


class GreeterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    greeting: str = Field(default="Hello", min_length=1, max_length=80)
    punctuation: Literal["!", ".", "?"] = "!"
    uppercase: bool = False


class GreeterRuntime:
    """Runtime state owned by one GreeterPlugin instance."""

    def __init__(self) -> None:
        self._nodes: dict[str, GreeterConfig] = {}

    def set(self, node_id: str, config: GreeterConfig | None) -> None:
        if config is None:
            self._nodes.pop(node_id, None)
        else:
            self._nodes[node_id] = config

    def greet(self, node_id: str, name: str) -> dict[str, str]:
        try:
            config = self._nodes[node_id]
        except KeyError as exc:
            raise RuntimeError(f"greeter {node_id!r} is not loaded") from exc
        text = f"{config.greeting}, {name}{config.punctuation}"
        if config.uppercase:
            text = text.upper()
        return {"text": text, "greeter_id": node_id}


class GreeterMutation(NodeLifecycleTransaction):
    def __init__(
        self,
        runtime: GreeterRuntime,
        node_id: str,
        before: GreeterConfig | None,
        after: GreeterConfig | None,
    ) -> None:
        self.runtime = runtime
        self.node_id = node_id
        self.before = before
        self.after = after

    async def commit(self) -> None:
        self.runtime.set(self.node_id, self.after)

    async def rollback(self, error: BaseException) -> None:
        del error
        self.runtime.set(self.node_id, self.before)


class GreeterLifecycle(NodeLifecycleHandler):
    def __init__(self, runtime: GreeterRuntime) -> None:
        self.runtime = runtime

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        del context
        self.runtime.set(node.id, GreeterConfig.model_validate(node.config))

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        del context
        self.runtime.set(node.id, None)

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del context, request
        return GreeterMutation(
            self.runtime,
            node.id,
            None,
            GreeterConfig.model_validate(node.config),
        )

    async def prepare_update(
        self,
        context: NodeLifecycleContext,
        current: Card,
        updated: Card,
        request: CardPatch,
    ) -> NodeLifecycleTransaction:
        del context, request
        return GreeterMutation(
            self.runtime,
            current.id,
            GreeterConfig.model_validate(current.config),
            GreeterConfig.model_validate(updated.config),
        )

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        del context
        return GreeterMutation(
            self.runtime,
            node.id,
            GreeterConfig.model_validate(node.config),
            None,
        )


class GreeterPlugin:
    descriptor = PluginDescriptor(
        id="community.greeter",
        version="0.1.0",
        plugin_api_version="1.1",
        name="Greeter",
        description="A minimal graph-derived Agent tool example.",
    )

    def __init__(self) -> None:
        self.runtime = GreeterRuntime()

    async def greet(
        self,
        context: CapabilityContext,
        capability: Capability,
        arguments: dict[str, object],
    ) -> dict[str, str]:
        del context
        name = arguments.get("name")
        if set(arguments) != {"name"} or not isinstance(name, str) or not name.strip():
            raise ResourceValidationError("greet requires one non-empty string name")
        return self.runtime.greet(capability.target_id, name.strip())

    def register(self, registration: PluginRegistration) -> None:
        registration.register_capability_handler(
            "community.greeter.greet", self.greet
        )
        registration.register_node_type(NodeTypeDefinition(
            id="community.greeter",
            label="Greeter",
            description="A tiny service that greets a name through an Agent tool.",
            icon="sparkles",
            color="#7c6f9b",
            deck_id="community.examples",
            deck_label="Community examples",
            deck_icon="sparkles",
            default_name="Friendly Greeter",
            default_size=(300, 190),
            default_status="available",
            statuses=frozenset({"available"}),
            config_model=GreeterConfig,
            traits=frozenset({"community.greeting-target"}),
            lifecycle=GreeterLifecycle(self.runtime),
            templateable=True,
        ))
        registration.register_relationship(RelationshipDefinition(
            id="community.greet",
            label="Greet with",
            short_label="greet",
            description="The Agent can ask this Greeter to greet a name.",
            source_traits=frozenset({"core.agent"}),
            target_traits=frozenset({"community.greeting-target"}),
            templateable=True,
            capabilities=(CapabilityGrantDefinition(
                kind="community.greeter.greet",
                tool_prefix="greet_with",
                description="Ask {target_name!r} to greet a person by name.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name to greet.",
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ),),
        ))


def create_plugin() -> GreeterPlugin:
    """Entry-point factory; each registry receives a fresh plugin/runtime."""

    return GreeterPlugin()


__all__ = ["GreeterConfig", "create_plugin"]
