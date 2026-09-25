import { describe, expect, it } from "vitest";
import { CHUNK_SIZE, filterCardsToChunks, getViewportChunkBounds, getViewportChunkKeys, positionToChunk } from "./chunks";
import { buildCardDraft } from "./helpers";
import type { PluginCatalog } from '../types/world';

describe("world chunks", () => {
  const catalog = { node_types: [{ id: 'legion', container: {} }, { id: 'text' }] } as unknown as PluginCatalog;
  const node = (id: string, x: number, y: number, parent_id?: string) => ({ id, ...buildCardDraft('text', { x, y }), parent_id });
  const group = (id: string, x: number, y: number, parent_id?: string) => ({ ...node(id, x, y, parent_id), type: 'legion', size: { width: 800, height: 800 } });

  it.each([catalog, undefined])('retains nested descendants in input order across negative and disjoint coverage', catalog => {
    const cards = [node('leaf', 21000, 100, 'inner'),
      { ...node('equipment', 50000, 50000), equipment: { owner_id: 'inner', relationship: null } },
      node('sibling', 30000, 30000, 'outer'), group('inner', -2400, -2400, 'outer'),
      group('outer', -2500, -2500), node('free', 9000, 100)];
    expect(filterCardsToChunks(cards, ['-2:-2'], catalog).map(card => card.id)).toEqual(['leaf', 'sibling', 'inner', 'outer']);
    expect(filterCardsToChunks(cards, new Set(['10:0', '4:0']), catalog).map(card => card.id)).toEqual(['leaf', 'sibling', 'inner', 'outer', 'free']);
    expect(filterCardsToChunks(cards, ['20:20'], catalog)).toEqual([]);
  });

  it('does not fill the gap between disjoint active chunks and tolerates an unloaded ancestor', () => {
    const cards = [node('first', 100, 100), group('gap', 2300, 2300), node('second', 4500, 4500),
      node('far-child', 30000, 30000, 'loaded'), group('loaded', 100, 100, 'unloaded')];
    expect(filterCardsToChunks(cards, ['0:0', '2:2'], catalog).map(card => card.id)).toEqual(['first', 'second', 'far-child', 'loaded']);
  });
  it("keeps coverage stable within a tile and detects negative boundary and resize crossings", () => {
    const viewport = { x: 100, y: 100, zoom: 0.12, width: 1920, height: 1080 };
    const bounds = getViewportChunkBounds(viewport, 0);
    expect(bounds).toEqual({ minX: -1, maxX: 7, minY: -1, maxY: 3 });
    expect(getViewportChunkBounds({ ...viewport, x: 110 }, 0)).toEqual(bounds);
    expect(getViewportChunkBounds({ ...viewport, x: 246 }, 0).minX).toBe(-2);
    expect(getViewportChunkBounds({ ...viewport, width: 2560 }, 0).maxX).toBe(10);
    expect(getViewportChunkBounds(viewport)).toEqual({ minX: -2, maxX: 8, minY: -2, maxY: 4 });
    expect(getViewportChunkKeys(viewport)).toHaveLength(77);
  });
  it("keeps a spanning Legion and its members together across chunk boundaries", () => {
    const group = { id: "group", ...buildCardDraft("agent", { x: 1800, y: 100 }), type: "legion", size: { width: 1100, height: 700 } };
    const member = { id: "member", ...buildCardDraft("agent", { x: 2400, y: 200 }), parent_id: group.id };
    expect(filterCardsToChunks([group, member], ["1:0"]).map((c) => c.id)).toEqual(["group", "member"]);
    expect(filterCardsToChunks([group, member], ["9:9"])).toEqual([]);
  });
  it("indexes positive and negative world coordinates consistently", () => {
    expect(positionToChunk({ x: 0, y: 0 }).key).toBe("0:0");
    expect(positionToChunk({ x: CHUNK_SIZE, y: -1 }).key).toBe("1:-1");
    expect(positionToChunk({ x: -CHUNK_SIZE - 1, y: CHUNK_SIZE * 2 }).key).toBe("-2:2");
  });

  it("adds a prefetch ring and filters distant card components", () => {
    const keys = getViewportChunkKeys({ x: 0, y: 0, zoom: 1, width: 800, height: 600 });
    expect(keys).toContain("0:0");
    expect(keys).toContain("-1:-1");
    const near = { id: "near", ...buildCardDraft("text", { x: 100, y: 100 }) };
    const far = { id: "far", ...buildCardDraft("text", { x: 20_000, y: 20_000 }) };
    expect(filterCardsToChunks([near, far], keys).map((card) => card.id)).toEqual(["near"]);
  });
});
