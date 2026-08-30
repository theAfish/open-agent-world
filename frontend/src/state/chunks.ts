import type { FlowViewportState, WorldCard, WorldPosition } from "../types/world";

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

export function getViewportChunkKeys(
  viewport: FlowViewportState,
  ring = PREFETCH_RING,
): string[] {
  const safeZoom = Math.max(viewport.zoom, 0.01);
  const left = -viewport.x / safeZoom;
  const top = -viewport.y / safeZoom;
  const right = (viewport.width - viewport.x) / safeZoom;
  const bottom = (viewport.height - viewport.y) / safeZoom;
  const minX = Math.floor(left / CHUNK_SIZE) - ring;
  const maxX = Math.floor(right / CHUNK_SIZE) + ring;
  const minY = Math.floor(top / CHUNK_SIZE) - ring;
  const maxY = Math.floor(bottom / CHUNK_SIZE) + ring;
  const keys: string[] = [];

  for (let y = minY; y <= maxY; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      keys.push(chunkKey(x, y));
    }
  }
  return keys;
}

export function filterCardsToChunks(cards: WorldCard[], keys: Iterable<string>): WorldCard[] {
  const keySet = keys instanceof Set ? keys : new Set(keys);
  return cards.filter((card) => keySet.has(positionToChunk(card.position).key));
}

export function viewportCenterToWorld(viewport: FlowViewportState): WorldPosition {
  const safeZoom = Math.max(viewport.zoom, 0.01);
  return {
    x: (viewport.width / 2 - viewport.x) / safeZoom,
    y: (viewport.height / 2 - viewport.y) / safeZoom,
  };
}
