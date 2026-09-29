import type { Node } from "@xyflow/react";
import type { WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";

export interface CanvasNodeData extends Record<string, unknown> {
  card: WorldCard;
  surfaceLevel: NodeSurfaceLevel;
  displaced: boolean;
  equipmentDetail?: boolean;
  equipmentOrigin?: boolean;
  /** Transient canvas policy, never persisted as a presentation level. */
  renderLOD?: import('../canvas/cardRendering').CardRenderLOD;
}

export type CanvasNode = Node<CanvasNodeData, "worldCard" | "container" | "equipment" | "equipmentPanel">;
