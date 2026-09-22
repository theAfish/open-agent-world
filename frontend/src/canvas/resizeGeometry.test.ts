import { describe, expect, it } from 'vitest';
import { freeCorners, resizeFromCorner, RESIZE_CORNERS } from './resizeGeometry';
import { minimumSurfaceSize } from '../state/surfaceGeometry';

describe('shared corner resizing', () => {
  const box = { x: 100, y: 80, width: 800, height: 600, level: 'inspector' as const };
  it.each(RESIZE_CORNERS)('keeps the opposite corner fixed and bounds size at %s', corner => {
    for (const delta of [-10000, 10000]) {
      const next = resizeFromCorner(box, corner, delta, delta, { min: minimumSurfaceSize('inspector') });
      expect(next.width).toBeGreaterThanOrEqual(320);
      expect(next.height).toBeGreaterThanOrEqual(240);
      expect(next.width).toBeLessThanOrEqual(4096);
      expect(next.height).toBeLessThanOrEqual(4096);
      expect(corner.endsWith('left') ? next.x + next.width : next.x).toBe(corner.endsWith('left') ? 900 : 100);
      expect(corner.startsWith('top') ? next.y + next.height : next.y).toBe(corner.startsWith('top') ? 680 : 80);
    }
  });
  it('encloses stationary members when moving either side of a container', () => {
    const constraints = { min: { width: 100, height: 100 }, contains: { x: 200, y: 180, width: 500, height: 350 } };
    expect(resizeFromCorner(box, 'top-left', 1000, 1000, constraints)).toEqual({ x: 200, y: 180, width: 700, height: 500 });
    expect(resizeFromCorner(box, 'bottom-right', -1000, -1000, constraints)).toEqual({ x: 100, y: 80, width: 600, height: 450 });
  });
  it('offers partial-seam free corners while pinning the bonded edge and preserving overlap', () => {
    const peer = { x: 900, y: 300, width: 224, height: 300, level: 'preview' as const };
    expect(freeCorners('a', { a: box, b: peer }, [{ a: 'a', b: 'b', side: 'right' }])).toEqual([...RESIZE_CORNERS]);
    const constraints = { min: minimumSurfaceSize('inspector'), bonds: [{ side: 'right' as const, peer }] };
    const top = resizeFromCorner(box, 'top-right', 500, 1000, constraints);
    expect(top.x + top.width).toBe(900);
    expect(Math.min(top.y + top.height, peer.y + peer.height) - Math.max(top.y, peer.y)).toBeGreaterThanOrEqual(24);
    const bottom = resizeFromCorner(box, 'bottom-right', -500, -1000, constraints);
    expect(bottom.x + bottom.width).toBe(900);
    expect(bottom.y + bottom.height - peer.y).toBe(24);
  });
  it('stops a member at its parent header and inset', () => {
    expect(resizeFromCorner(box, 'top-left', -1000, -1000, { min: minimumSurfaceSize('inspector'), originMin: { x: 50, y: 40 } }))
      .toEqual({ x: 50, y: 40, width: 850, height: 640 });
  });
});
