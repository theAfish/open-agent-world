import { describe, expect, it } from 'vitest';
import { vi } from 'vitest';
import { worldApi } from '../api/client';
import { findGlue, freeCorners, glueGroup, resizeGlued, reflowGlueSurfaces, refreshGlue, cancelGlueRefresh, beginGlueEdit, useGlueStore, type GlueBox, type SharedGlue } from './glue';
const box = (x: number, y: number, width = 200, height = 160): GlueBox => ({ x, y, width, height, level: 'preview' });
describe('glue geometry', () => {
  it('defers background refreshes until all overlapping layout edits finish', async () => {
    const get = vi.spyOn(worldApi, 'getGlue').mockResolvedValue({ revision: 4, boxes: {}, bonds: [] });
    const first = beginGlueEdit(), second = beginGlueEdit();
    try {
      await refreshGlue();
      first(); first();
      expect(useGlueStore.getState().activeEdits).toBe(1);
      await refreshGlue();
      expect(get).not.toHaveBeenCalled();
      second();
      await refreshGlue();
      expect(get).toHaveBeenCalledOnce();
    } finally { first(); second(); get.mockRestore(); }
  });
  it.each(['top-left', 'top-right', 'bottom-left', 'bottom-right'])('keeps workspace content usable when shrinking from %s', corner => {
    const workspace: GlueBox = { ...box(10, 20, 1020, 700), level: 'workspace' };
    const resized = resizeGlued(workspace, corner, corner.endsWith('left') ? 2000 : -2000,
      corner.startsWith('top') ? 2000 : -2000, [], 10);
    expect(resized).toMatchObject({ width: 640, height: 420 });
    expect(corner.endsWith('left') ? resized.x + resized.width : resized.x).toBe(corner.endsWith('left') ? 1030 : 10);
    expect(corner.startsWith('top') ? resized.y + resized.height : resized.y).toBe(corner.startsWith('top') ? 720 : 20);
  });
  it('repairs saved flattened workspaces and reflows their bonded neighbours', () => {
    const boxes = { a: { ...box(0, 0, 1020, 96), level: 'workspace' as const }, b: box(0, 96) };
    const bonds = [{ a: 'a', b: 'b', side: 'bottom' as const }];
    const levels = new Map([['a', 'workspace' as const]]);
    const repaired = reflowGlueSurfaces(boxes, bonds, levels, {});
    expect(repaired.a).toMatchObject({ width: 1020, height: 420 });
    expect(repaired.b.y).toBe(420);
    expect(reflowGlueSurfaces(repaired, bonds, levels, {})).toBe(repaired);
    const collapsed = reflowGlueSurfaces(repaired, bonds, new Map([['a', 'preview']]), {});
    expect(reflowGlueSurfaces(collapsed, bonds, levels, {}).a.height).toBe(420);
  });
  it('stores a snapped bond without its temporary movement offsets', () => {
    const boxes = { a: box(0, 0), b: box(210, 30) };
    const candidate = findGlue({ a: boxes.a }, { b: boxes.b }, 16)!;
    try {
      useGlueStore.setState({ boxes: {}, bonds: [] });
      useGlueStore.getState().setLayout(boxes, candidate);
      expect(useGlueStore.getState().bonds).toEqual([{ a: 'a', b: 'b', side: 'right' }]);
    } finally { useGlueStore.setState({ boxes: {}, bonds: [] }); }
  });
  it('does not apply an in-flight remote layout over a newly started drag', async () => {
    let finish!: (value: SharedGlue) => void;
    const remote = new Promise<SharedGlue>(resolve => { finish = resolve; });
    const get = vi.spyOn(worldApi, 'getGlue').mockReturnValue(remote);
    try {
      const pending = refreshGlue();
      await vi.waitFor(() => expect(get).toHaveBeenCalledOnce());
      cancelGlueRefresh();
      useGlueStore.setState({ boxes: { a: box(80, 90) }, bonds: [] });
      finish({ revision: 3, boxes: { a: box(0, 0) }, bonds: [] });
      await pending;
      expect(useGlueStore.getState().boxes.a.x).toBe(80);
    } finally { get.mockRestore(); useGlueStore.setState({ boxes: {}, bonds: [] }); }
  });
  it('reflows a chain across state changes and restores custom sizes on returning', () => {
    const boxes = { a: box(0, 0, 320, 200), b: box(320, 0), c: box(520, 0) };
    const bonds = [{ a: 'a', b: 'b', side: 'right' as const }, { a: 'b', b: 'c', side: 'right' as const }];
    const collapsed = reflowGlueSurfaces(boxes, bonds, new Map([['a', 'node']]), {});
    expect(collapsed.a.width).toBe(96);
    expect(collapsed.b.x).toBe(96);
    expect(collapsed.c.x).toBe(296);
    const restored = reflowGlueSurfaces(collapsed, bonds, new Map([['a', 'preview']]), {});
    expect(restored.a).toMatchObject({ width: 320, height: 200 });
    expect(restored.b.x).toBe(320);
    expect(reflowGlueSurfaces(restored, bonds, new Map([['a', 'preview']]), {})).toBe(restored);
    const workspace = reflowGlueSurfaces(restored, bonds, new Map([['a', 'workspace']]), { a: { width: 1200, height: 800 } });
    expect(workspace.a).toMatchObject({ width: 1200, height: 800 });
    expect(workspace.b.x).toBe(1200);
  });
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
