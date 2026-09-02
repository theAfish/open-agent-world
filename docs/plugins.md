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
    NodeTypeDefinition,
    PluginRegistry,
    RelationshipDefinition,
)


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    query_language: str = "sql"


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

Relationship endpoint constraints can use exact `source_types`/`target_types`,
required traits, or both. Connection gestures are unordered: if only the
reverse orientation matches, both the frontend and backend store the endpoints
in the relationship's canonical orientation.

Plugins run inside the trusted FastAPI process and can receive credentials and
managed capabilities. Only install reviewed packages. Sandbox processes remain
untrusted and do not become plugins through this mechanism.
