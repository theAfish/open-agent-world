# Node effects

Canvas effects live in `frontend/src/effects`. They are presentation only: node
positions, containment, execution, and persistence remain owned by their
existing services.

## Activity

`ActivityGlow` accepts a phase and can decorate any positioned element with a
border radius:

```tsx
import { ActivityGlow } from "../effects/ActivityGlow";

<div style={{ position: "relative", borderRadius: 16 }}>
  <ActivityGlow phase="running" />
  {/* Surface content */}
</div>
```

The phases are `idle`, `running`, `waiting`, `completed`, `stopped`, and `failed`.
Running uses an orbiting highlight and four small particles; waiting uses an
amber pulse; failure keeps a static red outline. Completing active work gives
a brief green flash, then settles. Stopped and idle surfaces have no glow.
The component never intercepts pointer input. Continuous effects pause outside
the viewport and in hidden tabs; reduced motion disables orbiting and particles.

`useNodeActivity(card)` adapts world state to this component. Passing `true` as
the second argument aggregates a container's descendants, including equipment.
Operational running/waiting status takes precedence over terminal Run events.
Terminal labels use the current session's bounded event history; they are not
persisted execution history and return to idle when those events leave the
buffer or the page reloads. Plugins with a separate execution snapshot can
pass its phase directly to `ActivityGlow`.

`CardFrame`, `EquipmentCardNode`, and `ContainerFrame` already host the shared
component. Containers also display a textual activity label and active count.

## Generated nodes

After committing node creation and placement, a producer can publish
`EventType.NODES_GENERATED` with this payload:

```python
{
    "source_id": template.id,
    "target_id": instance.id,
    "container_id": workspace.id,  # optional
    "nodes": [workspace.model_dump(mode="json"), instance.model_dump(mode="json")],
}
```

Include all newly created nodes at their final positions. Summoning publishes
this event after laying out the instance and before starting its Run.

`worldStore` merges the final nodes and queues the live visual event in the
non-persisted generation store. `GenerationLayer`, mounted inside React Flow,
waits for measurement and moves an inert DOM copy through a `ViewportPortal`.
It uses rendered absolute coordinates, so containment, zoom, and local surface
expansion are respected. The source stays in place; the real target is revealed
at landing. Flight never changes React Flow positions or draggable settings.

The region reveals over 360 ms. The copy lifts after 120 ms, travels along a
650 ms arc, and lands with a 220 ms fade. Source or destination outside the
viewport, a hidden tab, or reduced motion skips flight. Events expire after
2.4 seconds and are deduplicated by ID. Loading a world snapshot does not replay
birth animations.

Custom node shells can use `useNodeGeneration(id)` to apply their own reveal
styling; `flightPosition` is a pure helper for the shared flight path. The
standard card and container shells already implement these phases.

## Surface origins

`SurfaceBridge` draws a translucent curved area between two React Flow node
IDs. It follows rendered absolute positions and measured dimensions, including
parent movement and viewport transforms. It has no graph edge, handles, or
pointer interaction. Other temporary surfaces can reuse it directly.

Equipment uses `equipmentSurfaceNodes` to render the actual node through
`WorldCardNode` at inspector level beside the backpack, with a synthetic canvas
ID for its source slot. The source slot still offers inventory controls;
only the actual node owns relationship handles. Closing level three returns
the node to its slot, while closing level four returns to level three.
Workspace availability comes from the plugin catalog; Summoning supports
preview and inspector only.

Detail dragging stores a temporary position relative to the backpack owner in
`useEquipmentPanel`. It never unequips the item or writes a new world position.
Each backpack shows one detail at a time, and closing the backpack dismisses
its details. Card editing, execution effects, and workspace controls use the
same components as ordinary world nodes.
