# Native shadow collections

Entry: **Fields → Shadow collection** in the normal canvas at http://127.0.0.1:5173/.
The historical browser checks used a separate disposable world (frontend 5174/backend 8002).
That preview is not a shipped service and is not assumed to be running. Test PDFs were placeholders,
not copies of research documents.

## Contract

- Stable type `core.shadow-collection`; `config.display_state` is validated as minimal/stacked/expanded.
- Native UUID, `parent_id`, positions, node config, edges, template support, undo and deletion are reused.
- Membership does not copy content. It follows the host's single-parent, cycle-checking and eligibility rules.
  Existing non-parentable containers and equipped resources retain their original restrictions.
- Positions remain world-space authoritative coordinates. Folded presentation coordinates never overwrite them.
  Collection movement uses existing descendant translation, preserving relative layout.
- A new member dropped into a folded collection retains its previous stored location; an expanded drop keeps
  its drop position. Membership failure rolls back through the existing batch-update path. Escape and
  pointer cancellation discard the pending drag without a membership commit.
- Automatic minimal-to-stacked transition waits for synchronization, rather than reacting to uncommitted membership.
- Collection boundary handles retain `boundary-top/right/bottom/left` identifiers. External references end at the
  collection itself. Folded member edges use display proxies; their source/target IDs remain in edge data/storage.
- `core.collection.reference` grants no content/execution capability. An Agent's `core.collection.inspect`
  connection grants `list_collection_members` (IDs, names, types only), with live reauthorization.
  This never invokes members or grants their content permissions.
- Expanded collections use an explicit **移出成员** mode. Without it, member drags retain membership;
  with it, dropping at the saved boundary can release a member. The animated mist is not a moving hit target.
  Legion's existing Dissolve action is unchanged.

## Rendering

No new rendering dependency. SVG builds a fixed-topology, 96-point radial envelope around padded member
surfaces and controls. Samples follow the outer member hull rather than joining radial spokes; a separate Gaussian-blurred
shadow feathers into the unmodified canvas. Clear card content uses existing native card/plugin rendering.
The SVG core supplies hit testing; transparent bounding corners and feather tails have no pointer capture.
Ports, saved edges and new-connection previews use the same outline geometry.
The activated decorative material uses a shared WebGL shader and generated distance texture; see
[SHADOW_GAS_BOUNDARY.md](SHADOW_GAS_BOUNDARY.md) for parameters, lifecycle, verification and limits.

The existing finite canvas animation loop interpolates positions, outline points and dimensions. It restarts
from the current presentation when interrupted. Hover state and live member-drag positions are transient,
not persisted. Static collections have no animation timer. Reduced-motion bypasses the position animation.

Parameters: `frontend/src/state/shadowCollection.ts` (`SHADOW`), exposed to CSS custom properties on WorldCanvas.
Business registration: `backend/plugins/shadow_collection.py`.
View: `frontend/src/cards/ShadowCollection.tsx`, `shadowCollection.css`.
Canvas/proxy integration: `WorldCanvas.tsx`, `CardFrame.tsx`, `state/containers.ts`, `edges/geometry.ts`.

## Verification

- 2026-09-11: frontend suite 41 files / 232 tests and application TypeScript check passed.
- 2026-09-11: 10 backend tests passed across test_shadow_collection.py, test_node_containers.py,
  and test_research_plugins.py. Earlier implementation-stage production build also passed.
- Browser, separate test world: created collection from Fields, undid creation, exercised both close levels,
  expanded native member cards, dropped PDF into minimal and observed updated counts/stacked state,
  created an Artifact-to-collection edge through the real chooser, moved minimal collection and round-trip
  checked identical translation of all original members, refreshed and restored the saved state.
- Tests verify an inspection connection cannot read a contained text resource; state cycles/reload preserve
  member UUIDs and all edge IDs/endpoints.
- Main backend restarted after confirming no running/waiting nodes; native catalog registration and HTTP 200 checked.
  Research data was not replaced by preview fixtures.

## Specific limits

- Stacked rendering shows at most six member faces; counts include all direct members. Expanded shows every member.
  Capacity remains the shared container limit (100 direct members).
- This is a radial envelope, not a full signed-distance/metaball union. Extremely sparse or strongly non-star-shaped
  layouts may have broader filled bridges than the reference; it does not cut holes through the collection.
- Deeply nested plugin containers use their declared/persisted surface dimensions; unusual plugin overflow needs
  an adapter if it exceeds those dimensions. Single-member live drag updates the contour; group-drag contour
  refinements settle on the authoritative layout at release.
- Edges internal to the same folded collection are hidden by presentation filtering, without changing stored
  endpoints. External member connections use proxies; expanded members recover their actual endpoints.
- No physical touch-device or large-many-collection frame-rate benchmark was performed. No FPS claim is made.
  The existing >500 kB main-bundle warning remains. The browser session logged a transient HMR hook warning while
  the component was edited; the hook ordering was corrected and subsequent page loads used the corrected code.

Research data, preview databases, generated screenshots and machine-specific launch shortcuts are not source artifacts.
