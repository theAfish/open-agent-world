# Reusable canvas placement

Placement finds space for a new or moved region without moving other objects. It
does not perform global packing or continuously rearrange the canvas.

## Pure geometry

`backend.spatial.Rectangle` and `find_free_region` have no world, plugin, database,
or frontend dependencies. The input is a desired rectangle, occupied rectangles,
and a nonnegative clearance in the same coordinate units.

```python
from backend.spatial import Rectangle, find_free_region

region = find_free_region(
    Rectangle(x=1200, y=200, width=800, height=500),
    [Rectangle(x=0, y=0, width=1100, height=650)],
    gap=80,
)
```

The solver considers the desired position and candidates aligned with each
obstacle's four boundaries. It sorts them by squared distance from the desired
position, with deterministic coordinate tie-breaking, and chooses the first clear
candidate. Rectangle size remains unchanged. With finite positive rectangles on
an unbounded plane, an outer boundary always provides a candidate. Worst-case
cost is quadratic in obstacle count. This finds the nearest *candidate*, not the
globally closest free point or best arrangement.

## World snapshots and plans

`backend.world.layout.WorldLayout.capture(world)` reads the graph once and keeps
occupied rectangles keyed by node ID. `card_footprints` applies container minima,
content insets, and nested member growth. Ordinary cards reserve conservative
preview bounds; equipment belongs to its owner's presentation. Local expanded
panels are not represented in this backend snapshot.

For a card or arbitrary region, use `layout.place_region(rectangle, gap=80)`.
When moving an existing region, `exclude_ids` can omit the moving nodes and their
descendants from collision checks. The caller supplies the IDs explicitly.

For a nonempty group with at least one spatial root:

```python
from backend.world.layout import WorldLayout
from backend.world.models import Point

layout = WorldLayout.capture(services.world)
spec = services.plugins.node_type(container_type).container
plan = layout.plan_container(
    nodes, spec, preferred=Point(x=1200, y=200), gap=80,
)
```

`plan.position` and `plan.size` describe the new container. Apply `plan.offset`
to every node's position, retaining relative geometry; attach only
`plan.root_node_ids` to the new container. Nested membership and equipment
ownership remain intact. The plan automatically excludes the supplied node IDs
from the captured obstacles. Its insets and minimum size come from `spec`, so
it also works with other registered container types.

Planning is read-only. The caller owns node creation, events, relationship
creation, persistence, and rollback. Capture, planning, and application should
run inside the existing node mutation scope to prevent concurrent placement
against a stale snapshot. For sequential placements, capture again after each
application so the next plan sees the newly occupied space. Summoning uses this
path while retaining its own instance and Run lifecycle.

The frontend's `nodeDisplacement.ts` handles temporary movement around locally
expanded surfaces. It has different inputs and interaction semantics, and is
separate from backend placement. Backend plans do not disable dragging or move
existing regions when a local panel opens.
