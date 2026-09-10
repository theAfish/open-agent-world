import { describe, expect, it } from 'vitest';
import { findGlue, freeCorners, glueGroup, resizeGlued, type GlueBox } from './glue';
const box = (x: number, y: number, width = 200, height = 160): GlueBox => ({ x, y, width, height, level: 'preview' });
describe('glue geometry', () => {
  it('attaches opposite edges and rejects corner-only contact and distant cards', () => {
    expect(findGlue({ a: box(0, 0) }, { b: box(210, 30) }, 16)).toMatchObject({ side: 'right', dx: 10, dy: 0 });
    expect(findGlue({ a: box(0, 0) }, { b: box(0, 170) }, 16)).toMatchObject({ side: 'bottom', dy: 10 });
    expect(findGlue({ a: box(0, 0) }, { b: box(-210, 0) }, 16)?.side).toBe('left');
    expect(findGlue({ a: box(0, 0) }, { b: box(0, -170) }, 16)?.side).toBe('top');
    expect(findGlue({ a: box(0, 0) }, { b: box(210, 159) }, 16)).toBeUndefined();
    expect(findGlue({ a: box(0, 0) }, { b: box(230, 0) }, 16)).toBeUndefined();
  });
  it('moves transitive groups and hides corners occupied by seams', () => {
    const bonds = [{ a: 'a', b: 'b', side: 'right' as const }, { a: 'b', b: 'c', side: 'bottom' as const }];
    expect([...glueGroup('c', bonds)].sort()).toEqual(['a', 'b', 'c']);
    expect(freeCorners('a', { a: box(0, 0), b: box(200, 0) }, bonds.slice(0, 1))).toEqual(['top-left', 'bottom-left']);
  });
  it('snaps unequal bottom edges and preserves opposite edges during top-left resize', () => {
    expect(resizeGlued(box(0, 0), 'bottom-left', 0, 35, [box(200, 0, 200, 200)], 10).height).toBe(200);
    const resized = resizeGlued(box(0, 0), 'top-left', -50, -30, [], 10);
    expect(resized.x + resized.width).toBe(200);
    expect(resized.y + resized.height).toBe(160);
  });
});
