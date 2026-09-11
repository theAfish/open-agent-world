import type { CanvasNode } from "../cards/types";

/** Animation frames may decorate data; compare the actual committed geometry. */
export function layoutIsPresented(nodes: CanvasNode[], targets: CanvasNode[]) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  return targets.every(target => {
    const node = byId.get(target.id);
    return node && node.data.card === target.data.card
      && node.parentId === target.parentId && node.hidden === target.hidden
      && Math.abs(node.position.x - target.position.x) < .01
      && Math.abs(node.position.y - target.position.y) < .01
      && node.style?.width === target.style?.width
      && node.style?.height === target.style?.height;
  });
}
