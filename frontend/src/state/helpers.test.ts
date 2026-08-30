import { describe, expect, it } from "vitest";
import { buildCardDraft, expandedPatch, makeStressCards } from "./helpers";
import { EXPANDED_CARD_SIZES } from "../types/world";

describe("card state helpers", () => {
  it("creates all four concrete card drafts", () => {
    for (const type of ["agent", "text", "image", "sandbox"] as const) {
      const card = buildCardDraft(type, { x: 12, y: -8 });
      expect(card.type).toBe(type);
      expect(card.position).toEqual({ x: 12, y: -8 });
      expect(card.expanded).toBe(false);
    }
    expect(buildCardDraft("agent", { x: 0, y: 0 }).config.model).toBe("gemini-3.7-flash");
  });

  it("expands in place and generates a deterministic virtualized stress world", () => {
    const card = { id: "a", ...buildCardDraft("agent", { x: 5, y: 7 }) };
    expect(expandedPatch(card)).toEqual({ expanded: true, size: EXPANDED_CARD_SIZES.agent });
    const first = makeStressCards(1_500, 4);
    const second = makeStressCards(1_500, 4);
    expect(first).toHaveLength(1_500);
    expect(first).toEqual(second);
    expect(first.every((item) => item.ephemeral)).toBe(true);
  });
});

