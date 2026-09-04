import type { WorldCard, WorldPosition } from "../types/world";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from "../state/nodeSurfaces";

export interface DisplacedPosition {
  position: WorldPosition;
  displaced: boolean;
}

const SURFACE_CLEARANCE = 52;
const PREVIEW_CLEARANCE = 14;
const PEER_CLEARANCE = 28;
const ITERATIONS = 28;

export interface SurfaceObstacle {
  card: WorldCard;
  level: Exclude<NodeSurfaceLevel, "node">;
  clearance?: number;
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

function surfaceClearance(obstacle: SurfaceObstacle) {
  return obstacle.clearance ?? (obstacle.level === "preview" ? PREVIEW_CLEARANCE : SURFACE_CLEARANCE);
}

function pushOutsideSurface(
  position: WorldPosition,
  card: WorldCard,
  level: NodeSurfaceLevel,
  obstacle: SurfaceObstacle,
  obstaclePosition: WorldPosition,
): WorldPosition {
  if (card.id === obstacle.card.id) return position;
  const nodeSize = surfaceSize(level);
  const obstacleSize = surfaceSize(obstacle.level);
  const renderedObstaclePosition = positionSurfaceAtNodeCenter(obstaclePosition, obstacle.level);
  const compactSize = NODE_SURFACE_SIZE.node;
  const center = { x: position.x + compactSize.width / 2, y: position.y + compactSize.height / 2 };
  const obstacleCenter = {
    x: renderedObstaclePosition.x + obstacleSize.width / 2,
    y: renderedObstaclePosition.y + obstacleSize.height / 2,
  };
  const clearance = surfaceClearance(obstacle);
  const halfWidth = (obstacleSize.width + nodeSize.width) / 2 + clearance;
  const halfHeight = (obstacleSize.height + nodeSize.height) / 2 + clearance;
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
    x: obstacleCenter.x + dx * scale - compactSize.width / 2,
    y: obstacleCenter.y + dy * scale - compactSize.height / 2,
  };
}

function resolvePeerCollision(
  first: WorldPosition,
  second: WorldPosition,
  firstId: string,
  secondId: string,
  firstLevel: NodeSurfaceLevel,
  secondLevel: NodeSurfaceLevel,
): [WorldPosition, WorldPosition] {
  const compactSize = NODE_SURFACE_SIZE.node;
  const firstSize = surfaceSize(firstLevel);
  const secondSize = surfaceSize(secondLevel);
  const firstCenter = { x: first.x + compactSize.width / 2, y: first.y + compactSize.height / 2 };
  const secondCenter = { x: second.x + compactSize.width / 2, y: second.y + compactSize.height / 2 };
  let dx = secondCenter.x - firstCenter.x;
  let dy = secondCenter.y - firstCenter.y;
  const requiredX = (firstSize.width + secondSize.width) / 2 + PEER_CLEARANCE;
  const requiredY = (firstSize.height + secondSize.height) / 2 + PEER_CLEARANCE;
  if (Math.abs(dx) >= requiredX || Math.abs(dy) >= requiredY) return [first, second];
  if (Math.abs(dx) + Math.abs(dy) < 0.0001) {
    const direction = stableDirection(`${firstId}:${secondId}`);
    dx = direction.x;
    dy = direction.y;
  }
  const resolveX = (requiredX - Math.abs(dx)) <= (requiredY - Math.abs(dy));
  const sign = (resolveX ? dx : dy) >= 0 ? 1 : -1;
  const distance = resolveX ? requiredX - Math.abs(dx) : requiredY - Math.abs(dy);
  const shift = distance / 2 + 0.5;
  return resolveX
    ? [{ ...first, x: first.x - sign * shift }, { ...second, x: second.x + sign * shift }]
    : [{ ...first, y: first.y - sign * shift }, { ...second, y: second.y + sign * shift }];
}

/**
 * Calculates a temporary reflow around every expanded surface. It uses the
 * current rendered rectangles (not a fixed-radius approximation), then
 * propagates peer separation outward only from cards an obstacle actually
 * moved. Persisted graph coordinates are never changed here.
 */
export function displacedPositions(
  cards: readonly WorldCard[],
  obstacles: readonly SurfaceObstacle[],
  surfaceLevels: ReadonlyMap<string, NodeSurfaceLevel> = new Map(),
): Map<string, DisplacedPosition> {
  const obstacleIds = new Set(obstacles.map((obstacle) => obstacle.card.id));
  const fixedObstacles = obstacles.filter((obstacle) => obstacle.level !== "preview");
  const previewObstacles = obstacles.filter((obstacle) => obstacle.level === "preview");
  const positions = new Map(cards.map((card) => [card.id, { ...card.position }]));
  const movablePeers = cards.filter((card) => !obstacleIds.has(card.id));
  const affected = new Set<string>();

  for (let iteration = 0; iteration < ITERATIONS; iteration += 1) {
    let moved = false;

    // A preview repels compact peers, but remains subordinate to persistent
    // inspector/workspace surfaces. Resolve its live obstacle position first.
    for (const preview of previewObstacles) {
      if (!positions.has(preview.card.id)) continue;
      let next = positions.get(preview.card.id) ?? preview.card.position;
      for (const obstacle of fixedObstacles) {
        const pushed = pushOutsideSurface(
          next,
          preview.card,
          surfaceLevels.get(preview.card.id) ?? preview.level,
          obstacle,
          positions.get(obstacle.card.id) ?? obstacle.card.position,
        );
        if (pushed.x !== next.x || pushed.y !== next.y) moved = true;
        next = pushed;
      }
      positions.set(preview.card.id, next);
    }

    // Compact peers then see the preview at its resolved position, rather than
    // the persisted center it occupied before a higher-level surface moved it.
    for (const card of movablePeers) {
      let next = positions.get(card.id) ?? card.position;
      for (const obstacle of obstacles) {
        const pushed = pushOutsideSurface(
          next,
          card,
          surfaceLevels.get(card.id) ?? "node",
          obstacle,
          positions.get(obstacle.card.id) ?? obstacle.card.position,
        );
        if (pushed.x !== next.x || pushed.y !== next.y) {
          affected.add(card.id);
          moved = true;
        }
        next = pushed;
      }
      positions.set(card.id, next);
    }
    for (let firstIndex = 0; firstIndex < movablePeers.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < movablePeers.length; secondIndex += 1) {
        const first = movablePeers[firstIndex];
        const second = movablePeers[secondIndex];
        if (!affected.has(first.id) && !affected.has(second.id)) continue;
        const firstPosition = positions.get(first.id) ?? first.position;
        const secondPosition = positions.get(second.id) ?? second.position;
        const [nextFirst, nextSecond] = resolvePeerCollision(
          firstPosition,
          secondPosition,
          first.id,
          second.id,
          surfaceLevels.get(first.id) ?? "node",
          surfaceLevels.get(second.id) ?? "node",
        );
        const collided = nextFirst.x !== firstPosition.x || nextFirst.y !== firstPosition.y
          || nextSecond.x !== secondPosition.x || nextSecond.y !== secondPosition.y;
        if (collided) {
          affected.add(first.id);
          affected.add(second.id);
          moved = true;
        }
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
