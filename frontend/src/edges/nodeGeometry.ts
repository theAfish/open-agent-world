import type { CanvasNode } from "../cards/types";

/** Match the surface actually rendered, including compact equipment slots. */
export function nodeCornerRadius(node?: Pick<CanvasNode, "type" | "data">): number {
  if (node?.type === "equipment") return 8;
  if (node?.type === "equipmentPanel") return 12;
  if (node?.data.surfaceLevel === "node") return 48;
  if (node?.data.surfaceLevel === "preview") return 30;
  return 24;
}
