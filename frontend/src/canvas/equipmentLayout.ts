import type { CanvasNode } from "../cards/types";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import type { WorldPosition } from "../types/world";

export const equipmentOriginId = (id: string) => `${id}:equipment-origin`;

/** Temporary canvas placement; equipped nodes retain their persisted ownership and position. */
export function equipmentSurfaceNodes(node: CanvasNode, ownerId: string, ownerLevel: NodeSurfaceLevel,
  index: number, open: boolean, position?: WorldPosition): CanvasNode[] {
  const slot = { x: 13, y: NODE_SURFACE_SIZE[ownerLevel].height + 51 + index * 48 };
  const row: CanvasNode = { ...node, type: "equipment", parentId: ownerId, draggable: false,
    hidden: !open, position: slot, style: { width: 294, height: 40 }, zIndex: 26 };
  if (!["inspector", "workspace"].includes(node.data.surfaceLevel)) return [row];
  return [
    { ...node, type: "worldCard", parentId: ownerId, hidden: !open, zIndex: 40,
      position: position ?? { x: 368, y: NODE_SURFACE_SIZE[ownerLevel].height + 8 },
      data: { ...node.data, equipmentDetail: true } },
    { ...row, id: equipmentOriginId(node.id), selectable: true, connectable: false, zIndex: 30,
      data: { ...node.data, equipmentOrigin: true } },
  ];
}
