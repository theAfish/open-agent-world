export interface Point {
  x: number;
  y: number;
}

export interface NodeRect extends Point {
  width: number;
  height: number;
}

export interface BoundaryAnchor extends Point {
  normalX: number;
  normalY: number;
}

export interface RelationshipPath {
  path: string;
  markerPath: string;
  bidirectionalMarkerPath: string;
  markerSource: Point;
  markerTarget: Point;
  labelX: number;
  labelY: number;
  source: BoundaryAnchor;
  target: BoundaryAnchor;
}

const EPSILON = 1e-6;
// Keep arrowheads clear of the visible endpoint dots at both boundaries.
const MARKER_OFFSET = 12;

function roundedRectSignedDistance(
  x: number,
  y: number,
  halfWidth: number,
  halfHeight: number,
  radius: number,
): number {
  const qx = Math.abs(x) - (halfWidth - radius);
  const qy = Math.abs(y) - (halfHeight - radius);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - radius;
}

/** Finds the ray intersection and outward normal of an axis-aligned rounded rectangle. */
export function roundedRectAnchor(
  rect: NodeRect,
  toward: Point,
  cornerRadius = 22,
): BoundaryAnchor {
  const halfWidth = Math.max(rect.width / 2, EPSILON);
  const halfHeight = Math.max(rect.height / 2, EPSILON);
  const radius = Math.max(0, Math.min(cornerRadius, halfWidth, halfHeight));
  const centerX = rect.x + halfWidth;
  const centerY = rect.y + halfHeight;
  let dx = toward.x - centerX;
  let dy = toward.y - centerY;
  const length = Math.hypot(dx, dy);
  if (length < EPSILON) {
    dx = 1;
    dy = 0;
  } else {
    dx /= length;
    dy /= length;
  }

  let inside = 0;
  let outside = Math.hypot(halfWidth, halfHeight) + radius + 1;
  for (let iteration = 0; iteration < 42; iteration += 1) {
    const distance = (inside + outside) / 2;
    const signedDistance = roundedRectSignedDistance(
      dx * distance,
      dy * distance,
      halfWidth,
      halfHeight,
      radius,
    );
    if (signedDistance <= 0) inside = distance;
    else outside = distance;
  }

  const localX = dx * ((inside + outside) / 2);
  const localY = dy * ((inside + outside) / 2);
  const straightHalfWidth = halfWidth - radius;
  const straightHalfHeight = halfHeight - radius;
  let normalX = 0;
  let normalY = 0;

  if (Math.abs(localX) <= straightHalfWidth + EPSILON) {
    normalY = Math.sign(localY) || 1;
  } else if (Math.abs(localY) <= straightHalfHeight + EPSILON) {
    normalX = Math.sign(localX) || 1;
  } else {
    const cornerX = Math.sign(localX) * straightHalfWidth;
    const cornerY = Math.sign(localY) * straightHalfHeight;
    const cornerDx = localX - cornerX;
    const cornerDy = localY - cornerY;
    const cornerLength = Math.hypot(cornerDx, cornerDy) || 1;
    normalX = cornerDx / cornerLength;
    normalY = cornerDy / cornerLength;
  }

  return {
    x: centerX + localX,
    y: centerY + localY,
    normalX,
    normalY,
  };
}

function cubicPoint(a: number, b: number, c: number, d: number, t: number): number {
  const inverse = 1 - t;
  return inverse ** 3 * a + 3 * inverse ** 2 * t * b + 3 * inverse * t ** 2 * c + t ** 3 * d;
}

export function relationshipPath(
  sourceRect: NodeRect,
  targetRect: NodeRect,
  cornerRadius = 22,
): RelationshipPath {
  const sourceCenter = {
    x: sourceRect.x + sourceRect.width / 2,
    y: sourceRect.y + sourceRect.height / 2,
  };
  const targetCenter = {
    x: targetRect.x + targetRect.width / 2,
    y: targetRect.y + targetRect.height / 2,
  };
  const source = roundedRectAnchor(sourceRect, targetCenter, cornerRadius);
  const target = roundedRectAnchor(targetRect, sourceCenter, cornerRadius);
  const endpointDistance = Math.hypot(target.x - source.x, target.y - source.y);
  const controlDistance = Math.max(42, Math.min(180, endpointDistance * 0.32));
  const sourceControl = {
    x: source.x + source.normalX * controlDistance,
    y: source.y + source.normalY * controlDistance,
  };
  const targetControl = {
    x: target.x + target.normalX * controlDistance,
    y: target.y + target.normalY * controlDistance,
  };
  const markerTarget = {
    x: target.x + target.normalX * MARKER_OFFSET,
    y: target.y + target.normalY * MARKER_OFFSET,
  };
  const markerSource = {
    x: source.x + source.normalX * MARKER_OFFSET,
    y: source.y + source.normalY * MARKER_OFFSET,
  };
  const markerSourceControl = {
    x: markerSource.x + source.normalX * controlDistance,
    y: markerSource.y + source.normalY * controlDistance,
  };
  const markerTargetControl = {
    x: markerTarget.x + target.normalX * controlDistance,
    y: markerTarget.y + target.normalY * controlDistance,
  };

  return {
    path: `M ${source.x},${source.y} C ${sourceControl.x},${sourceControl.y} ${targetControl.x},${targetControl.y} ${target.x},${target.y}`,
    markerPath: `M ${source.x},${source.y} C ${sourceControl.x},${sourceControl.y} ${markerTargetControl.x},${markerTargetControl.y} ${markerTarget.x},${markerTarget.y}`,
    bidirectionalMarkerPath: `M ${markerSource.x},${markerSource.y} C ${markerSourceControl.x},${markerSourceControl.y} ${markerTargetControl.x},${markerTargetControl.y} ${markerTarget.x},${markerTarget.y}`,
    markerSource,
    markerTarget,
    labelX: cubicPoint(source.x, sourceControl.x, targetControl.x, target.x, 0.5),
    labelY: cubicPoint(source.y, sourceControl.y, targetControl.y, target.y, 0.5),
    source,
    target,
  };
}

export function relationshipPathToPoint(
  sourceRect: NodeRect,
  target: Point,
  cornerRadius = 22,
): string {
  const source = roundedRectAnchor(sourceRect, target, cornerRadius);
  const distance = Math.hypot(target.x - source.x, target.y - source.y);
  const controlDistance = Math.max(36, Math.min(150, distance * 0.32));
  const sourceControlX = source.x + source.normalX * controlDistance;
  const sourceControlY = source.y + source.normalY * controlDistance;
  return `M ${source.x},${source.y} C ${sourceControlX},${sourceControlY} ${target.x - source.normalX * controlDistance},${target.y - source.normalY * controlDistance} ${target.x},${target.y}`;
}
