import { describe, expect, it } from "vitest";
import type { WorldCard, WorldEdge } from "../types/world";
import { buildCardDraft } from "./helpers";
import { TEST_CATALOG } from "./catalog.fixture";
import { summarizeLegionSelection } from "./legions";

function card(id: string, type: WorldCard["type"], ephemeral = false): WorldCard {
  return { id, ...buildCardDraft(type, { x: 0, y: 0 }), ephemeral };
}

function edge(id: string, source: string, target: string, relationship = "communicate"): WorldEdge {
  return { id, source, target, relationship, direction: "bidirectional" };
}

describe("Legion selection topology", () => {
  it("captures the induced internal graph and reports boundary links", () => {
    const cards = [card("a", "agent"), card("b", "agent"), card("outside", "agent")];
    const internal = edge("internal", "a", "b");
    const external = edge("external", "b", "outside");

    const summary = summarizeLegionSelection(cards, [internal, external], ["a", "b"], TEST_CATALOG);

    expect(summary.cards.map((item) => item.id)).toEqual(["a", "b"]);
    expect(summary.internalEdges).toEqual([internal]);
    expect(summary.externalEdges).toEqual([external]);
    expect(summary.unsupportedCards).toEqual([]);
    expect(summary.unsupportedEdges).toEqual([]);
  });

  it("fails closed for synthetic and plugin-disabled cards", () => {
    const catalog = {
      ...TEST_CATALOG,
      node_types: TEST_CATALOG.node_types.map((item) => (
        item.id === "text" ? { ...item, templateable: false } : item
      )),
    };
    const synthetic = card("synthetic", "agent", true);
    const blocked = card("blocked", "text");
    const summary = summarizeLegionSelection([synthetic, blocked], [], [synthetic.id, blocked.id], catalog);

    expect(summary.unsupportedCards.map((item) => item.id)).toEqual(["synthetic", "blocked"]);
  });

  it("fails closed for internal relationships that are plugin-disabled or missing", () => {
    const catalog = {
      ...TEST_CATALOG,
      relationships: TEST_CATALOG.relationships.map((item) => (
        item.id === "communicate" ? { ...item, templateable: false } : item
      )),
    };
    const cards = [card("a", "agent"), card("b", "agent"), card("outside", "agent")];
    const disabled = edge("disabled", "a", "b");
    const missing = edge("missing", "b", "a", "vendor.unknown");
    const external = edge("external", "b", "outside");

    const summary = summarizeLegionSelection(
      cards,
      [disabled, missing, external],
      ["a", "b"],
      catalog,
    );

    expect(summary.unsupportedEdges.map((item) => item.id)).toEqual(["disabled", "missing"]);
    expect(summary.externalEdges).toEqual([external]);
  });
});
