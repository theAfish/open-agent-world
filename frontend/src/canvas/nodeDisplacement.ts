import type { WorldCard, WorldPosition } from "../types/world";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from "../state/nodeSurfaces";

export interface DisplacedPosition {
  position: WorldPosition;
  displaced: boolean;
}

const SURFACE_CLEARANCE = 52;
const PEER_CLEARANCE = 28;
const ITERATIONS = 28;

export interface SurfaceObstacle {
  card: WorldCard;
  level: Extract<NodeSurfaceLevel, "inspector" | "workspace">;
}

function surfaceSize(level: NodeSurfaceLevel) {
  return NODE_SURFACE_SIZE[level];
}

export function positionSurfaceAtNodeCenter(
  position: WorldPosition,
  level: NodeSurfaceLevel,
): WorldPosition {
  const size = surfaceSize(level);
  return {
    x: position.x + (NODE_SURFACE_SIZE.node.width - size.width) / 2,
    y: position.y + (NODE_SURFACE_SIZE.node.height - size.height) / 2,
  };
}

export function nodePositionFromSurfacePosition(
  position: WorldPosition,
  level: NodeSurfaceLevel,
): WorldPosition {
  const size = surfaceSize(level);
  return {
    x: position.x - (NODE_SURFACE_SIZE.node.width - size.width) / 2,
    y: position.y - (NODE_SURFACE_SIZE.node.height - size.height) / 2,
  };
}

function stableDirection(id: string) {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) hash = (hash * 31 + id.charCodeAt(index)) >>> 0;
  const angle = (hash % 360) * Math.PI / 180;
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

function pushOutsideSurface(position: WorldPosition, card: WorldCard, obstacle: SurfaceObstacle): WorldPosition {
  if (card.id === obstacle.card.id) return position;
  const nodeSize = NODE_SURFACE_SIZE.node;
  const obstacleSize = surfaceSize(obstacle.level);
  const obstaclePosition = positionSurfaceAtNodeCenter(obstacle.card.position, obstacle.level);
  const center = { x: position.x + nodeSize.width / 2, y: position.y + nodeSize.height / 2 };
  const obstacleCenter = {
    x: obstaclePosition.x + obstacleSize.width / 2,
    y: obstaclePosition.y + obstacleSize.height / 2,
  };
  const halfWidth = (obstacleSize.width + nodeSize.width) / 2 + SURFACE_CLEARANCE;
  const halfHeight = (obstacleSize.height + nodeSize.height) / 2 + SURFACE_CLEARANCE;
  let dx = center.x - obstacleCenter.x;
  let dy = center.y - obstacleCenter.y;
  let normalized = Math.max(Math.abs(dx) / halfWidth, Math.abs(dy) / halfHeight);
  if (normalized >= 1) return position;
  if (normalized < 0.0001) {
    const direction = stableDirection(`${card.id}:${obstacle.card.id}`);
    dx = direction.x;
    dy = direction.y;
    normalized = Math.max(Math.abs(dx) / halfWidth, Math.abs(dy) / halfHeight);
  }
  const scale = 1.008 / normalized;
  return {
    x: obstacleCenter.x + dx * scale - nodeSize.width / 2,
    y: obstacleCenter.y + dy * scale - nodeSize.height / 2,
  };
}

function resolvePeerCollision(
  first: WorldPosition,
  second: WorldPosition,
  firstId: string,
  secondId: string,
): [WorldPosition, WorldPosition] {
  const size = NODE_SURFACE_SIZE.node;
  const firstCenter = { x: first.x + size.width / 2, y: first.y + size.height / 2 };
  const secondCenter = { x: second.x + size.width / 2, y: second.y + size.height / 2 };
  let dx = secondCenter.x - firstCenter.x;
  let dy = secondCenter.y - firstCenter.y;
  const required = size.width + PEER_CLEARANCE;
  if (Math.abs(dx) >= required || Math.abs(dy) >= required) return [first, second];
  if (Math.abs(dx) + Math.abs(dy) < 0.0001) {
    const direction = stableDirection(`${firstId}:${secondId}`);
    dx = direction.x;
    dy = direction.y;
  }
  const resolveX = (required - Math.abs(dx)) <= (required - Math.abs(dy));
  const sign = (resolveX ? dx : dy) >= 0 ? 1 : -1;
  const distance = resolveX ? required - Math.abs(dx) : required - Math.abs(dy);
  const shift = distance / 2 + 0.5;
  return resolveX
    ? [{ ...first, x: first.x - sign * shift }, { ...second, x: second.x + sign * shift }]
    : [{ ...first, y: first.y - sign * shift }, { ...second, y: second.y + sign * shift }];
}

/**
 * Calculates a temporary reflow around every expanded surface. It uses the
 * rendered rectangle (not a fixed-radius approximation), then repeatedly
 * separates peers so a large workspace cannot leave nearby compact cards on
 * top of one another. Persisted graph coordinates are never changed here.
 */
export function displacedPositions(
  cards: readonly WorldCard[],
  obstacles: readonly SurfaceObstacle[],
): Map<string, DisplacedPosition> {
  const obstacleIds = new Set(obstacles.map((obstacle) => obstacle.card.id));
  const positions = new Map(cards.map((card) => [card.id, { ...card.position }]));
  const movable = cards.filter((card) => !obstacleIds.has(card.id));

  for (let iteration = 0; iteration < ITERATIONS; iteration += 1) {
    let moved = false;
    for (const card of movable) {
      let next = positions.get(card.id) ?? card.position;
      for (const obstacle of obstacles) {
        const pushed = pushOutsideSurface(next, card, obstacle);
        moved ||= pushed.x !== next.x || pushed.y !== next.y;
        next = pushed;
      }
      positions.set(card.id, next);
    }
    for (let firstIndex = 0; firstIndex < movable.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < movable.length; secondIndex += 1) {
        const first = movable[firstIndex];
        const second = movable[secondIndex];
        const firstPosition = positions.get(first.id) ?? first.position;
        const secondPosition = positions.get(second.id) ?? second.position;
        const [nextFirst, nextSecond] = resolvePeerCollision(firstPosition, secondPosition, first.id, second.id);
        moved ||= nextFirst.x !== firstPosition.x || nextFirst.y !== firstPosition.y
          || nextSecond.x !== secondPosition.x || nextSecond.y !== secondPosition.y;
        positions.set(first.id, nextFirst);
        positions.set(second.id, nextSecond);
      }
    }
    if (!moved) break;
  }

  return new Map(cards.map((card) => {
    const position = positions.get(card.id) ?? card.position;
    return [card.id, {
      position,
      displaced: position.x !== card.position.x || position.y !== card.position.y,
    }];
  }));
}

/** Backward-compatible single-card helper for inspector-only callers. */
export function displacedPosition(
  card: WorldCard,
  inspectorOrInspectors: WorldCard | readonly WorldCard[] | undefined,
): DisplacedPosition {
  const inspectors = !inspectorOrInspectors
    ? []
    : Array.isArray(inspectorOrInspectors) ? inspectorOrInspectors : [inspectorOrInspectors];
  return displacedPositions(
    [card, ...inspectors.filter((inspector) => inspector.id !== card.id)],
    inspectors.map((inspector) => ({ card: inspector, level: "inspector" })),
  ).get(card.id) ?? { position: card.position, displaced: false };
}
