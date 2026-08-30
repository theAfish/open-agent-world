import type { Node } from "@xyflow/react";
import type { WorldCard } from "../types/world";

export interface CanvasNodeData extends Record<string, unknown> {
  card: WorldCard;
}

export type CanvasNode = Node<CanvasNodeData, "worldCard">;
