"""A complete, deliberately small Open Agent World plugin.

The plugin contributes a canvas node, a relationship-derived Agent tool, and
node lifecycle behavior. It keeps its executable runtime private and exposes
only declarations and callbacks through the host registry.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.capabilities import Capability
from backend.errors import ResourceValidationError
from backend.plugins import (
    CapabilityGrantDefinition,
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeTypeDefinition,
    PluginRegistry,
    RelationshipDefinition,
)
from backend.world.models import Card, CardCreate, CardPatch


class GreeterConfig(BaseModel):
    """Persisted and backend-validated configuration for one Greeter node."""

    model_config = ConfigDict(extra="forbid")

    greeting: str = Field(default="Hello", min_length=1, max_length=80)
    punctuation: Literal["!", ".", "?"] = "!"
    uppercase: bool = False


class GreeterRuntime:
    """Plugin-owned executable state; no provider SDK leaks into the host."""

    def __init__(self) -> None:
        self._nodes: dict[str, GreeterConfig] = {}

    def load(self, node: Card) -> None:
        self._nodes[node.id] = GreeterConfig.model_validate(node.config)

    def update(self, node: Card, request: CardPatch) -> None:
        merged = {**node.config, **(request.config or {})}
        self._nodes[node.id] = GreeterConfig.model_validate(merged)

    def unload(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)

    def greet(self, node_id: str, name: str) -> dict[str, str]:
        try:
            config = self._nodes[node_id]
        except KeyError as exc:
            raise RuntimeError(f"greeter {node_id!r} is not loaded") from exc
        text = f"{config.greeting}, {name}{config.punctuation}"
        if config.uppercase:
            text = text.upper()
        return {"text": text, "greeter_id": node_id}


runtime = GreeterRuntime()


class GreeterLifecycle(NodeLifecycleHandler):
    """Keeps plugin-owned runtime state aligned with persisted world nodes."""

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        del context
        runtime.load(node)

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        del context
        runtime.unload(node.id)

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        del context, request
        runtime.load(node)

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        del context, request, error
        runtime.unload(node.id)

    async def on_update(
        self, context: NodeLifecycleContext, node: Card, request: CardPatch
    ) -> None:
        del context
        runtime.update(node, request)

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        del context
        runtime.unload(node.id)


async def greet(
    host: Any, capability: Capability, arguments: dict[str, Any]
) -> dict[str, str]:
    """Execute the scoped tool granted by a ``community.greet`` edge."""

    # Capability authorization was re-derived immediately before this callback.
    # This example needs no host service, so it deliberately keeps that boundary
    # unused and talks only to its own runtime.
    del host
    name = arguments.get("name")
    if set(arguments) != {"name"} or not isinstance(name, str) or not name.strip():
        raise ResourceValidationError("greet requires one non-empty string name")
    return runtime.greet(capability.target_id, name.strip())


def register(registry: PluginRegistry) -> None:
    """Called once by the host's ``open_agent_world.plugins`` loader."""

    # Handlers must be registered before relationships that reference them.
    registry.register_capability_handler("community.greeter.greet", greet)

    registry.register_node_type(NodeTypeDefinition(
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
        lifecycle=GreeterLifecycle(),
    ))

    registry.register_relationship(RelationshipDefinition(
        id="community.greet",
        label="Greet with",
        short_label="greet",
        description="The Agent can ask this Greeter to greet a name.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"community.greeting-target"}),
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


__all__ = ["GreeterConfig", "register"]
