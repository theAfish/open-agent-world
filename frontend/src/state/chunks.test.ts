import { describe, expect, it } from "vitest";
import { CHUNK_SIZE, filterCardsToChunks, getViewportChunkBounds, getViewportChunkKeys, positionToChunk } from "./chunks";
import { buildCardDraft } from "./helpers";

describe("world chunks", () => {
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
