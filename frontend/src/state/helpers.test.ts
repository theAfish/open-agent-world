import { describe, expect, it } from "vitest";
import { buildCardDraft, makeStressCards } from "./helpers";
import { TEST_CATALOG } from "./catalog.fixture";

describe("card state helpers", () => {
  it("uses catalog frame dimensions for container cards", () => {
    const definition = { ...TEST_CATALOG.node_types[0], default_size: { width: 1100, height: 650 },
      container: { member_traits: [], parentable: true, connectable: true,
        min_size: [800, 500] as [number, number], content_inset: [24, 110, 24, 24] as [number, number, number, number],
        max_members: 100, document_field: null } };
    expect(buildCardDraft(definition.id, { x: 0, y: 0 }, definition).size).toEqual(definition.default_size);
  });
  it("creates every built-in card draft", () => {
    for (const type of ["agent", "conversation", "text", "image", "sandbox"] as const) {
      const card = buildCardDraft(type, { x: 12, y: -8 });
      expect(card.type).toBe(type);
      expect(card.position).toEqual({ x: 12, y: -8 });
      expect(card.expanded).toBe(false);
    }
    expect(buildCardDraft("agent", { x: 0, y: 0 }).config.model).toBe("oaw:default");
  });

  it("uses catalog defaults without injecting historical built-in configuration", () => {
    const definition = { ...TEST_CATALOG.node_types.find(node => node.id === "agent")!,
      default_config: { model: "oaw:model:configured", system_instruction: "Catalog instruction" } };
    const draft = buildCardDraft("agent", { x: 0, y: 0 }, definition);
    expect(draft.config).toEqual(definition.default_config);
    expect(draft.config).not.toBe(definition.default_config);
    expect(buildCardDraft("agent", { x: 0, y: 0 }, { ...definition, default_config: {} }).config).toEqual({});
  });

  it("generates a deterministic virtualized stress world", () => {
    const first = makeStressCards(1_500, 4);
    const second = makeStressCards(1_500, 4);
    expect(first).toHaveLength(1_500);
    expect(first).toEqual(second);
    expect(first.every((item) => item.ephemeral)).toBe(true);
  });
});
