import { describe, expect, it } from "vitest";
import { TEST_CATALOG } from "../state/catalog.fixture";
import { defaultDecks, normalizeDecks } from "./ComponentPalette";

describe("component palette creation policy", () => {
  const managedLegion = {
    ...TEST_CATALOG.node_types[0],
    id: "legion",
    label: "Legion",
    deck_id: "fields",
    deck_label: "Fields",
    user_creatable: false,
  };
  const catalog = {
    ...TEST_CATALOG,
    node_types: [...TEST_CATALOG.node_types, managedLegion],
  };

  it("does not materialize managed node types into their default deck", () => {
    expect(defaultDecks(catalog).flatMap((deck) => deck.cardTypes)).not.toContain("legion");
  });

  it("removes managed node types from previously stored custom decks", () => {
    const decks = normalizeDecks([{
      id: "custom-old",
      label: "Old",
      icon: "folder",
      cardTypes: ["legion", "agent"],
      custom: true,
    }], catalog);

    expect(decks.flatMap((deck) => deck.cardTypes)).not.toContain("legion");
    expect(decks.flatMap((deck) => deck.cardTypes)).toContain("agent");
  });
});
