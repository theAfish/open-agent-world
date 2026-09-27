import type { FlowViewportState, WorldCard, WorldPosition, PluginCatalog } from "../types/world";
import { isContainer, parentFirst } from "./containers";

export const CHUNK_SIZE = 2048;
export const PREFETCH_RING = 1;

export function chunkKey(x: number, y: number): string {
  return `${x}:${y}`;
}

export function positionToChunk(position: WorldPosition): { x: number; y: number; key: string } {
  const x = Math.floor(position.x / CHUNK_SIZE);
  const y = Math.floor(position.y / CHUNK_SIZE);
  return { x, y, key: chunkKey(x, y) };
}

export function getViewportChunkBounds(
  viewport: FlowViewportState,
  ring = PREFETCH_RING,
) {
  const safeZoom = Math.max(viewport.zoom, 0.01);
  const left = -viewport.x / safeZoom;
  const top = -viewport.y / safeZoom;
  const right = (viewport.width - viewport.x) / safeZoom;
  const bottom = (viewport.height - viewport.y) / safeZoom;
  const minX = Math.floor(left / CHUNK_SIZE) - ring;
  const maxX = Math.floor(right / CHUNK_SIZE) + ring;
  const minY = Math.floor(top / CHUNK_SIZE) - ring;
  const maxY = Math.floor(bottom / CHUNK_SIZE) + ring;
  return { minX, maxX, minY, maxY };
}

export function getViewportChunkKeys(
  viewport: FlowViewportState,
  ring = PREFETCH_RING,
): string[] {
  const { minX, maxX, minY, maxY } = getViewportChunkBounds(viewport, ring);
  const keys: string[] = [];

  for (let y = minY; y <= maxY; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      keys.push(chunkKey(x, y));
    }
  }
  return keys;
}

/** Compare coverage without allocating the full prefetch list on camera moves. */
export function sameViewportChunks(a: FlowViewportState, b: FlowViewportState): boolean {
  const left = getViewportChunkBounds(a);
  const right = getViewportChunkBounds(b);
  return left.minX === right.minX && left.maxX === right.maxX
    && left.minY === right.minY && left.maxY === right.maxY;
}

export function filterCardsToChunks(cards: WorldCard[], keys: Iterable<string>, catalog?: PluginCatalog): WorldCard[] {
  // A boundary update can include a large Legion. Build direct children once;
  // descendants(cards, id) rescanned every card even for each leaf in that Legion.
  const childrenByParent = new Map<string, WorldCard[]>();
  for (const card of cards) {
    if (card.parent_id == null) continue;
    const children = childrenByParent.get(card.parent_id) ?? [];
    children.push(card);
    childrenByParent.set(card.parent_id, children);
  }
  const keySet = keys instanceof Set ? keys : new Set(keys);
  const visible = new Set(cards.filter((card) => keySet.has(positionToChunk(card.position).key)).map((c) => c.id));
  for (const group of parentFirst(cards.filter((c) => catalog ? isContainer(c, catalog) : childrenByParent.has(c.id))).reverse()) {
    const members: WorldCard[] = [];
    const pending = [...(childrenByParent.get(group.id) ?? [])].reverse();
    while (pending.length) {
      const member = pending.pop()!;
      members.push(member);
      const children = childrenByParent.get(member.id);
      if (children) for (let index = children.length - 1; index >= 0; index--) pending.push(children[index]);
    }
    const intersects = [...keySet].some((key) => {
      const [x, y] = key.split(":").map(Number);
      return group.position.x < (x + 1) * CHUNK_SIZE && group.position.x + group.size.width >= x * CHUNK_SIZE
        && group.position.y < (y + 1) * CHUNK_SIZE && group.position.y + group.size.height >= y * CHUNK_SIZE;
    });
    if (intersects || members.some((c) => visible.has(c.id))) {
      visible.add(group.id); members.forEach((c) => visible.add(c.id));
    }
  }
  return cards.filter((card) => visible.has(card.id));
}

export function viewportCenterToWorld(viewport: FlowViewportState): WorldPosition {
  const safeZoom = Math.max(viewport.zoom, 0.01);
  return {
    x: (viewport.width / 2 - viewport.x) / safeZoom,
    y: (viewport.height / 2 - viewport.y) / safeZoom,
  };
}
