export interface Point {
  x: number;
  y: number;
}

export interface NodeRect extends Point {
  width: number;
  height: number;
  outline?: Point[];
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

  if(rect.outline?.length) {
    let nearest=Infinity,result:BoundaryAnchor|undefined;
    for(let i=0;i<rect.outline.length;i++) {
      const a=rect.outline[i],b=rect.outline[(i+1)%rect.outline.length];
      const ex=b.x-a.x,ey=b.y-a.y,den=dx*ey-dy*ex;
      if(Math.abs(den)<EPSILON)continue;
      const ax=rect.x+a.x-centerX,ay=rect.y+a.y-centerY;
      const distance=(ax*ey-ay*ex)/den,t=(ax*dy-ay*dx)/den;
      if(distance<0||t<0||t>1||distance>=nearest)continue;
      nearest=distance;const norm=Math.hypot(ex,ey)||1;
      result={x:centerX+dx*distance,y:centerY+dy*distance,normalX:ey/norm,normalY:-ex/norm};
    }
    if(result)return result;
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
  sourceCornerRadius = 22,
  targetCornerRadius = sourceCornerRadius,
  options: { offset?: number; selfLoop?: boolean; markerOffset?: number } = {},
): RelationshipPath {
  const sourceCenter = {
    x: sourceRect.x + sourceRect.width / 2,
    y: sourceRect.y + sourceRect.height / 2,
  };
  const targetCenter = {
    x: targetRect.x + targetRect.width / 2,
    y: targetRect.y + targetRect.height / 2,
  };
  const offset = options.offset ?? 0;
  const distance = Math.hypot(targetCenter.x - sourceCenter.x, targetCenter.y - sourceCenter.y) || 1;
  const bend = { x: -(targetCenter.y - sourceCenter.y) / distance * offset,
    y: (targetCenter.x - sourceCenter.x) / distance * offset };
  // Parallel/return edges get distinct boundary anchors. A self-loop leaves and
  // returns on opposite sides of the lower arc, outside the node's silhouette.
  const source = roundedRectAnchor(sourceRect, options.selfLoop
    ? { x: sourceCenter.x + sourceRect.width, y: sourceCenter.y + sourceRect.height }
    : { x: targetCenter.x + bend.x, y: targetCenter.y + bend.y }, sourceCornerRadius);
  const target = roundedRectAnchor(targetRect, options.selfLoop
    ? { x: targetCenter.x - targetRect.width, y: targetCenter.y + targetRect.height }
    : { x: sourceCenter.x + bend.x, y: sourceCenter.y + bend.y }, targetCornerRadius);
  const endpointDistance = Math.hypot(target.x - source.x, target.y - source.y);
  const controlDistance = options.selfLoop ? 86 + Math.abs(offset) : Math.max(42, Math.min(180, endpointDistance * 0.32));
  if (options.selfLoop) { bend.x = 0; bend.y = 0; }
  const markerOffset = options.markerOffset ?? MARKER_OFFSET;
  const sourceControl = {
    x: source.x + source.normalX * controlDistance + bend.x,
    y: source.y + source.normalY * controlDistance + bend.y,
  };
  const targetControl = {
    x: target.x + target.normalX * controlDistance + bend.x,
    y: target.y + target.normalY * controlDistance + bend.y,
  };
  const markerTarget = {
    x: target.x + target.normalX * markerOffset,
    y: target.y + target.normalY * markerOffset,
  };
  const markerSource = {
    x: source.x + source.normalX * markerOffset,
    y: source.y + source.normalY * markerOffset,
  };
  const markerSourceControl = {
    x: markerSource.x + source.normalX * controlDistance + bend.x,
    y: markerSource.y + source.normalY * controlDistance + bend.y,
  };
  const markerTargetControl = {
    x: markerTarget.x + target.normalX * controlDistance + bend.x,
    y: markerTarget.y + target.normalY * controlDistance + bend.y,
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
