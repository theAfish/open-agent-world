import type { Node } from "@xyflow/react";
import type { WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";

export interface CanvasNodeData extends Record<string, unknown> {
  card: WorldCard;
  surfaceLevel: NodeSurfaceLevel;
  displaced: boolean;
}

export type CanvasNode = Node<CanvasNodeData, "worldCard">;
