# Connected data sources

[Plugin API](../plugins.md) · [Visualization Pack](../../plugins/visualization/README.md)

Plugin API **1.27** adds `NodeDataSource`, `NodeDataConsumer` and `DataQuery`, exported from
`open_agent_world.plugin_api`. The provider owns its schema, persistence and
read-only queries. A visualization only consumes a common JSON dataset.

```python
from open_agent_world.plugin_api import NodeDataSource

NodeTypeDefinition(
    # ... existing metadata, config and lifecycle ...
    traits=frozenset({"data.source"}),
    data_source=NodeDataSource(schemas=list_schemas, read=read_dataset),
)
```

`list_schemas(context)` returns:

```json
{"schemas": [{"id": "readings", "label": "main / readings", "kind": "table",
  "fields": [{"name": "time", "type": "number"}, {"name": "temperature", "type": "number"}],
  "aggregates": true}], "truncated": false}
```

Use `kind: "graph"` and an empty `fields` list for a native graph dataset.
`aggregates: true` means that the provider can group over the full source data;
omit it for providers that only expose bounded raw records.

`read_dataset(context, query: DataQuery)` receives `schema_id`, selected `columns`,
`limit` (1–10,000), optional `order_by`/`descending`, `group_by`, `value`, and
`aggregate` (`none`, `count`, `sum`, `mean`, `min`, `max`). Graph queries may include
`entity_type` and `relation_type`. Validate every identifier against the schema;
never concatenate unvalidated strings into SQL. Providers must bound work and
respect cancellation, and must never mutate source data.

Table result:

```json
{"kind": "table", "columns": ["time", "temperature"],
 "rows": [[1, 21.5], [2, 22.1]], "truncated": false, "scope": "rows"}
```

Full-data aggregates set `scope: "full"` and return a grouping column plus the
aggregate column named by `value_column` (default `value`). Use a distinct name
when the grouping field itself is named `value`.

Graph result:

```json
{"kind": "graph", "nodes": [{"id": "a", "name": "Copper", "type": "Material"},
 {"id": "b", "name": "Conductivity", "type": "Property"}],
 "edges": [{"id": "e", "source": "a", "target": "b", "type": "has"}],
 "truncated": false, "scope": "published"}
```

Only return edges whose endpoints are present. `truncated` must cover both node
and edge limits. Numbers must be finite JSON numbers; absent values are null.
The host rejects responses exceeding 4 MiB.

## Authorization and frontend

A relationship with `data_read=True` authorizes its source card to read datasets
from its target. The Visualization Pack registers `data.visualization.source`
from the `data.visualization` trait to the `data.source` trait. These read-only
connections grant no Agent tools and no ordinary resource actions.

- `POST /api/nodes/{reader}/data-source-schemas` takes
  `{"source_id": "provider-id", "relationship": "data.visualization.source"}`.
  It previews schema metadata before connection confirmation, after validating
  the proposed relationship. It creates no edge and grants no dataset reads.
- `GET /api/nodes/{reader}/data-sources` lists connected providers.
- `POST /api/nodes/{reader}/data-sources/{provider}/schemas` takes
  `{"arguments": {}}`.
- `POST /api/nodes/{reader}/data-sources/{provider}/read` takes
  `{"arguments": {"schema_id": "readings", "columns": ["time", "temperature"]}}`.

Every read rechecks the live relationship under the resource mutation lock.
The provider executes off the event loop with a host-managed `NodeResourceContext`.
Deletion/revocation waits for in-flight resource work, including cancellation.
The endpoints are builder APIs; deployed workspaces do not receive them.

Frontend Packs use optional `host.dataSources.list()`, `.schemas(sourceId)`,
`.read(sourceId, query)`, `.connect(sourceId, relationshipId)` and `.subscribe(fn)`.
`connect()` opens the host connection dialog; its promise resolves when the dialog
opens, not when the user confirms. The user must select a compatible schema before
confirmation saves the edge and selection. The consuming Pack declares its own
config field names and compatible dataset kinds:

```python
from open_agent_world.plugin_api import NodeDataConsumer

NodeTypeDefinition(
    # ... config_model must provide string defaults for these fields ...
    data_consumer=NodeDataConsumer(
        source_field="provider_ref", schema_field="dataset_key", kinds=("table",),
    ),
)
```

The host writes only the declared fields. It does not assume visualization config
names or choose chart axes. The catalog exposes `data_consumer` and relationship
`data_read` metadata. A data reader without a consumer declaration may manage its
own selection and can still use the dataset API with a valid connection.

Subscription invalidates connection changes; data refresh is explicit. Unsubscribe
on unmount and ignore obsolete async responses. Save only selections in config.

## Details presentation

`NodePresentation` now accepts optional `sizes` for supported surfaces:

```python
NodePresentation(states=("node", "preview", "inspector", "workspace"),
                 initial="inspector", open="inspector",
                 sizes={"inspector": {"width": 720, "height": 480}})
```

The host applies these defaults only when no instance size has been saved.
Manual resizing, collapse preferences and restored template geometry take priority.
