import type { CanvasNode } from "../cards/types";
import { NODE_SURFACE_RADIUS } from "../state/nodeSurfaces";
import type { NodeSurfaceLevel, PluginCatalog, WorldCard } from '../types/world';

/** Shape is shared by the visible surface, edge paths and connection affordances. */
export function cardBoundaryShape(card: WorldCard, level: NodeSurfaceLevel | undefined, catalog: PluginCatalog): 'ellipse' | undefined {
  return !card.missing_plugin && level === 'preview'
    && catalog.node_types.find(item => item.id === card.type)?.traits.includes('ui.ellipse.v1') ? 'ellipse' : undefined;
}

export function nodeBoundaryShape(node: Pick<CanvasNode, 'data'> | undefined, catalog: PluginCatalog) {
  return node && cardBoundaryShape(node.data.card, node.data.surfaceLevel, catalog);
}

/** Match the surface actually rendered, including compact equipment slots. */
export function nodeCornerRadius(node?: Pick<CanvasNode, "type" | "data">): number {
  if (node?.type === "equipment") return 8;
  if (node?.type === "equipmentPanel") return 12;
  if (node?.data.surfaceLevel) return NODE_SURFACE_RADIUS[node.data.surfaceLevel];
  return 24;
}
