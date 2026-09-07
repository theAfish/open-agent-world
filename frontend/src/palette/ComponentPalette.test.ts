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

  it("moves a catalog card when its deck revision changes, then preserves later user moves", () => {
    const skill = {
      ...TEST_CATALOG.node_types[1],
      id: "oaw.barracks.summoner",
      label: "Barracks skill",
      deck_id: "tools",
      deck_label: "Tools",
      deck_icon: "boxes",
      deck_revision: 2,
    };
    const catalogWithSkill = { ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types, skill] };
    const migrated = normalizeDecks([{
      id: "agents", label: "Agents", icon: "bot", cardTypes: ["agent", skill.id],
      catalogVersions: { agent: 1, [skill.id]: 1 }, custom: false,
    }], catalogWithSkill);
    expect(migrated.find((deck) => deck.id === "agents")?.cardTypes).not.toContain(skill.id);
    expect(migrated.find((deck) => deck.id === "tools")?.cardTypes).toContain(skill.id);

    const userMoved = normalizeDecks([
      { id: "agents", label: "Agents", icon: "bot", cardTypes: ["agent"], catalogVersions: { agent: 1 }, custom: false },
      { id: "tools", label: "Tools", icon: "boxes", cardTypes: [], catalogVersions: {}, custom: false },
      { id: "custom-kit", label: "Kit", icon: "folder", cardTypes: [skill.id], catalogVersions: { [skill.id]: 2 }, custom: true },
    ], catalogWithSkill);
    expect(userMoved.find((deck) => deck.id === "custom-kit")?.cardTypes).toContain(skill.id);
  });
});
