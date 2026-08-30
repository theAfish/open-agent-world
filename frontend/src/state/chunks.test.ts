import { describe, expect, it } from "vitest";
import { CHUNK_SIZE, filterCardsToChunks, getViewportChunkKeys, positionToChunk } from "./chunks";
import { buildCardDraft } from "./helpers";

describe("world chunks", () => {
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

