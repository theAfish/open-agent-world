import type { GlueBond, GlueBox } from '../state/glue';
import { SURFACE_MAX_SIZE, type SurfaceSize } from '../state/surfaceGeometry';

export const RESIZE_CORNERS = ['top-left', 'top-right', 'bottom-left', 'bottom-right'] as const;
export type ResizeCorner = typeof RESIZE_CORNERS[number];
export interface ResizeBox extends SurfaceSize { x: number; y: number }
export interface ResizeConstraints {
  min: SurfaceSize;
  max?: SurfaceSize;
  /** Inner bounds that the frame must continue to enclose. */
  contains?: ResizeBox;
  /** A member cannot cross its parent's header or left inset. */
  originMin?: { x: number; y: number };
  bonds?: Array<{ side: GlueBond['side']; peer: ResizeBox }>;
  snap?: { peers: ResizeBox[]; threshold: number };
}

export function seam(a: GlueBox, b: GlueBox, side: GlueBond['side']) {
  const vertical = side === 'left' || side === 'right';
  const start = vertical ? Math.max(a.y, b.y) : Math.max(a.x, b.x);
  const end = vertical ? Math.min(a.y + a.height, b.y + b.height) : Math.min(a.x + a.width, b.x + b.width);
  const axis = side === 'right' ? (a.x + a.width + b.x) / 2 : side === 'left' ? (a.x + b.x + b.width) / 2
    : side === 'bottom' ? (a.y + a.height + b.y) / 2 : (a.y + b.y + b.height) / 2;
  return { vertical, start, end, axis };
}

/** Corners touched by a seam are occupied; a partial seam can leave an end free. */
export function freeCorners(id: string, boxes: Record<string, GlueBox>, bonds: GlueBond[]): ResizeCorner[] {
  const box = boxes[id];
  if (!box) return [];
  return RESIZE_CORNERS.filter(corner => {
    const x = box.x + (corner.endsWith('right') ? box.width : 0);
    const y = box.y + (corner.startsWith('bottom') ? box.height : 0);
    return !bonds.some(bond => {
      if ((bond.a !== id && bond.b !== id) || !boxes[bond.a] || !boxes[bond.b]) return false;
      const s = seam(boxes[bond.a], boxes[bond.b], bond.side);
      return Math.abs((s.vertical ? x : y) - s.axis) < 2 && (s.vertical ? y : x) >= s.start - 2 && (s.vertical ? y : x) <= s.end + 2;
    });
  });
}

/** One edge solver for cards, container frames and glued surfaces. Opposite
 * edges stay fixed; constraints and snapping apply before producing a rectangle. */
export function resizeFromCorner(box: ResizeBox, corner: ResizeCorner, dx: number, dy: number, constraints: ResizeConstraints): ResizeBox {
  const { min, contains, originMin, bonds = [], snap } = constraints;
  const max = constraints.max ?? SURFACE_MAX_SIZE;
  const right = box.x + box.width, bottom = box.y + box.height;
  let leftMax = Math.min(right - min.width, contains?.x ?? Infinity);
  let topMax = Math.min(bottom - min.height, contains?.y ?? Infinity);
  let rightMin = Math.max(box.x + min.width, contains ? contains.x + contains.width : -Infinity);
  let bottomMin = Math.max(box.y + min.height, contains ? contains.y + contains.height : -Infinity);
  for (const { side, peer } of bonds) {
    if (side === 'left' || side === 'right') {
      topMax = Math.min(topMax, peer.y + peer.height - 24);
      bottomMin = Math.max(bottomMin, peer.y + 24);
    } else {
      leftMax = Math.min(leftMax, peer.x + peer.width - 24);
      rightMin = Math.max(rightMin, peer.x + 24);
    }
  }
  const snapped = (value: number, axis: 'x' | 'y') => {
    let best = value, distance = snap?.threshold ?? 0;
    for (const peer of snap?.peers ?? []) for (const edge of [peer[axis], peer[axis] + peer[axis === 'x' ? 'width' : 'height']]) {
      if (Math.abs(edge - value) < distance) { best = edge; distance = Math.abs(edge - value); }
    }
    return best;
  };
  const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
  const pinned = (side: GlueBond['side']) => bonds.some(bond => bond.side === side);
  let x = box.x, y = box.y, r = right, b = bottom;
  if (corner.endsWith('left') && !pinned('left')) x = clamp(snapped(x + dx, 'x'), Math.max(right - Math.max(max.width, box.width), originMin?.x ?? -Infinity), leftMax);
  if (corner.endsWith('right') && !pinned('right')) r = clamp(snapped(r + dx, 'x'), rightMin, box.x + Math.max(max.width, box.width, rightMin - box.x));
  if (corner.startsWith('top') && !pinned('top')) y = clamp(snapped(y + dy, 'y'), Math.max(bottom - Math.max(max.height, box.height), originMin?.y ?? -Infinity), topMax);
  if (corner.startsWith('bottom') && !pinned('bottom')) b = clamp(snapped(b + dy, 'y'), bottomMin, box.y + Math.max(max.height, box.height, bottomMin - box.y));
  return { x, y, width: r - x, height: b - y };
}
