# Backend plugin development

Open Agent World discovers trusted Python plugins through the
`open_agent_world.plugins` entry-point group. A plugin registers namespaced
node types, relationship rules, capability grants, and capability handlers in
one backend registry. The backend publishes the serializable portion at
`GET /api/catalog`; the canvas uses that catalog for the palette, labels,
connection matching, direction normalization, and generic node rendering.

## Package entry point

```toml
[project.entry-points."open_agent_world.plugins"]
dataset = "open_agent_world_dataset:register"
```

The entry point receives a `PluginRegistry`:

```python
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.plugins import (
    CapabilityGrantDefinition,
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeTypeDefinition,
    PluginRegistry,
    RelationshipDefinition,
)
from backend.world.models import Card, CardCreate, CardPatch


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    query_language: str = "sql"


class DatasetBehavior(NodeLifecycleHandler):
    """Adapter around a provider-neutral runtime owned by this plugin."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        await self.runtime.open(node.id, node.config)

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        await self.runtime.close(node.id)

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        await self.runtime.create(node.id, node.config)

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        await self.runtime.delete(node.id, missing_ok=True)

    async def on_update(
        self, context: NodeLifecycleContext, node: Card, request: CardPatch
    ) -> None:
        await self.runtime.update(node.id, request.model_dump(exclude_unset=True))

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        await self.runtime.delete(node.id)


dataset_runtime = DatasetRuntime()  # Plugin-owned implementation.


async def query_dataset(
    services: Any, capability: Any, arguments: dict[str, Any]
) -> dict[str, Any]:
    # Re-check plugin-owned authorization or runtime state here as needed.
    return {"target": capability.target_id, "query": arguments["query"]}


def register(registry: PluginRegistry) -> None:
    registry.register_capability_handler("acme.dataset.query", query_dataset)
    registry.register_node_type(NodeTypeDefinition(
        id="acme.dataset",
        label="Dataset",
        description="Queryable dataset",
        icon="database",
        color="#6f7d73",
        deck_id="acme.data",
        deck_label="Data",
        deck_icon="boxes",
        default_name="New Dataset",
        default_size=(320, 210),
        default_status="available",
        statuses=frozenset({"available", "indexing", "error"}),
        config_model=DatasetConfig,
        traits=frozenset({"acme.queryable"}),
        lifecycle=DatasetBehavior(dataset_runtime),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="acme.query",
        label="Query",
        short_label="query",
        description="The agent can query this dataset.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"acme.queryable"}),
        capabilities=(CapabilityGrantDefinition(
            kind="acme.dataset.query",
            tool_prefix="query_dataset",
            description="Query dataset {target_name!r}.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Dataset query."}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),),
    ))
```

Lifecycle handlers receive only the node/request models and a narrow,
provider-neutral `NodeLifecycleContext`; they do not receive
`ApplicationServices`, HTTP requests, provider SDK objects, or database
connections. Nodes without `lifecycle` remain ordinary persisted world
objects. Creation is ordered as persist node, run `on_create`, publish the
generic event. If `on_create` fails, the backend calls `on_create_rollback`,
deletes the persisted node, and re-raises the original error. A handler must
therefore clean up any partial external side effects in `on_create_rollback`.

Relationship endpoint constraints can use exact `source_types`/`target_types`,
required traits, or both. Connection gestures are unordered: if only the
reverse orientation matches, both the frontend and backend store the endpoints
in the relationship's canonical orientation.

Plugins run inside the trusted FastAPI process and can receive credentials and
managed capabilities. Only install reviewed packages. Sandbox processes remain
untrusted and do not become plugins through this mechanism.
