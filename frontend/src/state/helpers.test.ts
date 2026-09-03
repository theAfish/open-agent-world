import { describe, expect, it } from "vitest";
import { buildCardDraft, makeStressCards } from "./helpers";

describe("card state helpers", () => {
  it("creates every built-in card draft", () => {
    for (const type of ["agent", "conversation", "text", "image", "sandbox"] as const) {
      const card = buildCardDraft(type, { x: 12, y: -8 });
      expect(card.type).toBe(type);
      expect(card.position).toEqual({ x: 12, y: -8 });
      expect(card.expanded).toBe(false);
    }
    expect(buildCardDraft("agent", { x: 0, y: 0 }).config.model).toBe("gemini-3.7-flash");
  });

  it("generates a deterministic virtualized stress world", () => {
    const first = makeStressCards(1_500, 4);
    const second = makeStressCards(1_500, 4);
    expect(first).toHaveLength(1_500);
    expect(first).toEqual(second);
    expect(first.every((item) => item.ephemeral)).toBe(true);
  });
});
