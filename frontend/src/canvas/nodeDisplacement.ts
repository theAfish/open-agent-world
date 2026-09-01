import type { WorldCard, WorldPosition } from "../types/world";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from "../state/nodeSurfaces";

export interface DisplacedPosition {
  position: WorldPosition;
  displaced: boolean;
}

const CLEARANCE = 52;

export function positionSurfaceAtNodeCenter(
  position: WorldPosition,
  level: NodeSurfaceLevel,
): WorldPosition {
  const visualLevel = level === "workspace" ? "inspector" : level;
  const size = NODE_SURFACE_SIZE[visualLevel];
  return {
    x: position.x + (NODE_SURFACE_SIZE.node.width - size.width) / 2,
    y: position.y + (NODE_SURFACE_SIZE.node.height - size.height) / 2,
  };
}

export function nodePositionFromSurfacePosition(
  position: WorldPosition,
  level: NodeSurfaceLevel,
): WorldPosition {
  const visualLevel = level === "workspace" ? "inspector" : level;
  const size = NODE_SURFACE_SIZE[visualLevel];
  return {
    x: position.x - (NODE_SURFACE_SIZE.node.width - size.width) / 2,
    y: position.y - (NODE_SURFACE_SIZE.node.height - size.height) / 2,
  };
}

/**
 * Computes a temporary, local offset around an inspector. The returned values
 * never mutate card positions and decay to zero outside one inspector-sized
 * neighborhood.
 */
export function displacedPosition(
  card: WorldCard,
  inspectorOrInspectors: WorldCard | readonly WorldCard[] | undefined,
): DisplacedPosition {
  const inspectors = !inspectorOrInspectors
    ? []
    : Array.isArray(inspectorOrInspectors) ? inspectorOrInspectors : [inspectorOrInspectors];
  if (inspectors.length === 0 || inspectors.some((inspector) => card.id === inspector.id)) {
    return { position: card.position, displaced: false };
  }

  const nodeRadius = NODE_SURFACE_SIZE.node.width / 2;
  const nodeCenter = {
    x: card.position.x + nodeRadius,
    y: card.position.y + nodeRadius,
  };
  const radiusX = NODE_SURFACE_SIZE.inspector.width / 2 + nodeRadius + CLEARANCE;
  const radiusY = NODE_SURFACE_SIZE.inspector.height / 2 + nodeRadius + CLEARANCE;
  let offsetX = 0;
  let offsetY = 0;
  let displaced = false;

  for (const inspector of inspectors) {
    const inspectorCenter = {
      x: inspector.position.x + nodeRadius,
      y: inspector.position.y + nodeRadius,
    };
    let dx = nodeCenter.x - inspectorCenter.x;
    let dy = nodeCenter.y - inspectorCenter.y;
    let normalizedDistance = Math.hypot(dx / radiusX, dy / radiusY);
    if (normalizedDistance >= 1) continue;

    if (normalizedDistance < 0.001) {
      const angle = ((card.id.length * 47) % 360) * Math.PI / 180;
      dx = Math.cos(angle);
      dy = Math.sin(angle);
      normalizedDistance = Math.hypot(dx / radiusX, dy / radiusY);
    }

    const boundaryScale = 1 / normalizedDistance;
    const proximity = 1 - normalizedDistance;
    const influence = proximity * proximity * (3 - 2 * proximity);
    const scale = Math.min(1, 0.34 + influence * 0.82);
    offsetX += (dx * boundaryScale - dx) * scale;
    offsetY += (dy * boundaryScale - dy) * scale;
    displaced = true;
  }

  return {
    position: {
      x: card.position.x + offsetX,
      y: card.position.y + offsetY,
    },
    displaced,
  };
}
