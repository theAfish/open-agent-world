# Data visualization

Five connected cards: **Graph**, **Line chart**, **Bar chart**, **Scatter plot**
and **Distribution**. Source plugin discovery installs them as the
`data.visualization` Pack. Restart the backend and frontend after adding the Pack,
then collect its cards in Pack & Card Library.

Read the Pack's [English guide](src/oaw_visualization/docs/guide.en.md) or
[中文使用指南](src/oaw_visualization/docs/guide.zh-CN.md). The same documents and
a guided connection tutorial are available offline in **Help → Card tutorials & docs**.

1. Place a visualization card. It opens directly in a wide details view; graphs
   start closer to square. Resize it normally or choose **Open workspace**.
2. Pick a database in **Source**, or draw
   a **Visualization data** connection from the chart to a data-source card.
3. Choose **Schema** in the connection dialog, then click **Connect**. The
   connection and selection are saved together; cancelling creates no connection.
   SQL lists `main / table` and views. MKB lists compatible published graph and
   structured schema datasets, including arrays of objects. You can change the
   schema later in the card.
4. Choose X/Y and optional series; graph tables use From/To and optional relation.
   SQL supports count, sum, mean, min and max grouped over the full source table.
   Distribution bins the selected numeric field in the returned rows.
5. Scroll to zoom, drag to pan, double-click or click **1:1** to reset. The plot
   also supports keyboard `+`, `-`, arrows and Home. Hover a point, node or edge
   for its value. Refresh explicitly after changing database contents.

Selections persist in card config. Query results stay transient, never copied
into card state or templates. Deleting a connection clears the plot and revokes
reads on the backend. Reconnect or choose another source to recover. Missing
schemas and fields retain their saved selection until the user replaces it.

Rendering uses responsive, high-DPI Canvas; dense lines retain per-pixel extrema.
Charts do not create one DOM element per data point. Graphs fit their actual
bounds and use a deterministic layout, without an unbounded force simulation.
Dark/light theme changes redraw the canvas.

## Limits

- Dataset requests accept 1–10,000 rows/nodes/edges. SQL additionally caps returned
  cells at 1 MiB and retains its read-only authorizer and three-second SQL timeout.
  Full-table aggregates run before the result limit.
- MKB graph queries bound nodes and edges in SQL. Structured MKB records read at
  most 500 recent records, with optional array expansion and selected columns.
  MKB record sorting applies to that bounded set, not the entire knowledge base.
- The host rejects serialized responses above 4 MiB. Truncated results display
  **Partial data**; distributions describe only the returned rows. Null, empty
  and nonnumeric values are omitted with a visible skipped count.
- Refresh is manual; no background polling. Deployed workspace publication is
  not yet enabled for these cards.

Other database Packs can implement the public
[data-source protocol](../../docs/developers/data-sources.md), without importing
this visualization Pack or SQL/MKB internals. The Pack has no external renderer
dependency or CDN requirement.

## Ownership and development

- This Pack owns chart rendering, field controls, defaults, config mapping,
  icons, tutorials, documentation and component tests.
- SQL and MKB own their data adapters in their respective Packs. This Pack
  imports neither provider; other providers can implement the same public API.
- The host owns the public dataset protocol, live connection authorization,
  schema selection dialog, save/undo transactions and generic surface sizing.
  It learns this Pack's config field names from `NodeDataConsumer` metadata.
- The shared catalog translation dictionary still supplies catalog labels;
  chart UI text and tutorial/document translations are Pack-local. The host
  currently has no Pack-owned catalog translation registration API.

Run component tests from the repository's `frontend` directory:

```shell
npx vitest run --config ../plugins/visualization/vitest.config.mts
```

From the repository root, run Pack registration and dependency-boundary tests:

```shell
python -m pytest plugins/visualization/tests/test_pack.py
```

Host API and canvas integration checks remain in
`backend/tests/test_visualization.py` and `frontend/e2e/visualization.spec.ts`:
they exercise real connection permissions, persistence and SQL/MKB interoperability.
The source-plugin frontend uses the shared `@oaw/plugin-api` SDK and React runtime;
this does not claim a separately built Marketplace `.oawpack` artifact.
