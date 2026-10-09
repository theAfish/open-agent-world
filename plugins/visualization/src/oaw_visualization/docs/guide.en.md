# Data visualization guide

Choose a **Graph**, **Line chart**, **Bar chart**, **Scatter plot**, or
**Distribution** card. Cards open in a large details view. Resize the card or
open its workspace for more room; graph cards start closer to square.

## Connect and choose a schema

Drag between a visualization and a database card, or choose a database in
**Source**. Select **Schema** in the connection dialog, then click **Connect**.
Cancelling creates no connection. SQL offers tables and views. MKB offers
structured schema datasets, including arrays of objects; Graph cards can also
use the published knowledge graph. Empty schemas need fields before plotting.

Choose fields inside the card:

- **Line / Scatter:** X, numeric Y, optional Series.
- **Bar:** X category, numeric Y, optional SQL aggregate.
- **Distribution:** numeric Value; bins count returned rows.
- **Graph from a table:** From, To, optional Relation.
- **Published graph:** optional entity and relation type filters.

SQL supports count, sum, mean, minimum and maximum grouped over the full table.
Count does not need a Y field. Change Schema in the card to select another
dataset, then check the fields. Saved selections survive reloads.

## Explore and refresh

Scroll over the plot to zoom, drag to pan, and double-click or choose **1:1** to
reset. Keyboard controls: `+`, `-`, arrow keys and Home. Hover to inspect values.
Click **Refresh data** after database contents change; refresh is manual.

Deleting the connection clears the plot and stops data access. Choose
**Reconnect** and confirm a schema to restore it. Missing schemas or fields
keep their saved names until you choose replacements. A loading error offers
**Retry**. These cards only read the database.

## Understand the displayed data

The row limit offers 500, 2,000 or 10,000 items. SQL results also have a 1 MiB
cell-data limit and a three-second query timeout. MKB structured datasets scan
at most 500 recent records, with optional array expansion; sorting covers that
bounded set. Native graph nodes and edges are bounded separately.

**Partial data** indicates truncation. Distributions describe returned rows;
**Full-data aggregate** identifies SQL statistics computed before the result
limit. Empty or nonnumeric values are skipped with a visible count. Dataset
results are transient; only selections are saved. These cards are not currently
available in published workspaces.

Reopen these instructions through **Help → Card tutorials & docs**. They are
included in the Pack and can be read offline.
