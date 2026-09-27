import { describe, expect, it, vi } from "vitest";
import type { LibrarySnapshot } from "../state/cardLibrary";
import type { LegionSummary } from "../types/world";
import { collectedLibraryLegions, compareLibraryCards, libraryCardMatches, type LibraryCard } from "./libraryCatalog";

vi.mock("../i18n", () => ({ t: (value: string) => value }));

function snapshot(): LibrarySnapshot {
  return {
    schema_version: 2, revision: 1, migration_pending: false,
    plugins: { example: { installed: true, enabled: true, descriptor: {
      id: "example", name: "Example plugin", version: "1", plugin_api_version: "1.18", description: "",
    } } },
    packs: Object.fromEntries(["one", "two"].map(id => [id, {
      definition: { id, plugin_id: "example", name: `Pack ${id}`, description: "", cards: [] },
      owned: true, opened: false, opened_at: null,
    }])),
    card_definitions: {}, collection: {}, decks: [], active_deck_id: "starter",
    available_card_ids: [], available_pack_ids: ["one", "two"],
    preset_pack_ids: { formation: ["one", "two", "one"] },
  };
}

function formation(id = "formation", preset = true): LegionSummary {
  return { id, name: "Z formation", preset, node_count: 2, edge_count: 0,
    bounds: { width: 600, height: 400 }, node_types: ["text"], plugin_ids: ["open-agent-world.core"],
    compatible: true, issues: [], revision: 1 };
}

describe("pack formations in the Library", () => {
  it("uses opened owning packs, not member dependencies or a preset bucket", () => {
    const state = snapshot();
    expect(collectedLibraryLegions(state, [formation()])).toEqual([]);
    state.packs.one.opened = true;
    const [card] = collectedLibraryLegions(state, [formation()]);
    expect(card.sources).toEqual([{ id: "pack:one", name: "Pack one", pluginName: "Example plugin" }]);
    expect(card.category).toBe("Legions");
    expect(libraryCardMatches(card, "Pack one")).toBe(true);
    state.packs.two.opened = true;
    const cards = collectedLibraryLegions(state, [formation()]);
    expect(cards).toHaveLength(1);
    expect(cards[0].sources.map(source => source.id)).toEqual(["pack:one", "pack:two"]);
  });

  it("keeps saved formations separate and hides presets without collected provenance", () => {
    const state = snapshot();
    delete state.preset_pack_ids;
    const cards = collectedLibraryLegions(state, [formation(), formation("user-saved", false)]);
    expect(cards.map(card => card.id)).toEqual(["user-saved"]);
    expect(cards[0].sources[0].id).toBe("saved-legions");
    expect(cards[0].available).toBe(true);
  });

  it("orders formations before ordinary cards before pagination", () => {
    const state = snapshot();
    state.packs.one.opened = true;
    const [preset] = collectedLibraryLegions(state, [formation()]);
    const ordinary: LibraryCard = { ...preset, id: "text", kind: "node", label: "A text" };
    const internal: LibraryCard = { ...ordinary, id: "internal", internal: true };
    expect([internal, ordinary, preset].sort(compareLibraryCards).map(card => card.id))
      .toEqual(["formation", "text", "internal"]);
  });

  it("respects pack ownership and availability as well as formation compatibility", () => {
    const state = snapshot();
    state.packs.one.opened = true;
    state.packs.one.owned = false;
    expect(collectedLibraryLegions(state, [formation()])).toEqual([]);
    state.packs.one.owned = true;
    state.available_pack_ids = [];
    expect(collectedLibraryLegions(state, [formation()])[0].available).toBe(false);
    state.available_pack_ids = ["one"];
    expect(collectedLibraryLegions(state, [{ ...formation(), compatible: false }])[0].available).toBe(false);
  });
});
