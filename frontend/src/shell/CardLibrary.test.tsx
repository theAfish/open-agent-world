// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CardLibrary } from "./CardLibrary";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { TEST_CATALOG } from "../state/catalog.fixture";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";

const originalRefresh = useCardLibrary.getState().refresh;
function snapshot(revision = 1): LibrarySnapshot {
  const definitions = Array.from({ length: 75 }, (_, i) => ({ ...TEST_CATALOG.node_types[0], id: `card.${i + 1}`,
    label: `Tool ${String(i + 1).padStart(2, "0")}`, deck_label: i % 2 ? "Even" : "Odd", user_creatable: true }));
  return { schema_version: 1, revision, migration_pending: false, plugins: {}, packs: {},
    card_definitions: Object.fromEntries(definitions.map(card => [card.id, card])),
    collection: Object.fromEntries(definitions.map(card => [card.id, { card_id: card.id, plugin_id: card.plugin_id,
      source_pack_ids: [], unlocked: true, unlocked_at: "2026-09-10T00:00:00Z" }])),
    decks: [{ id: "test", name: "My deck", icon: "layers", entries: [] }], active_deck_id: "test",
    available_card_ids: definitions.map(card => card.id), available_pack_ids: [] };
}

afterEach(() => {
  cleanup(); vi.restoreAllMocks();
  useCardLibrary.setState({ snapshot: null, busy: false, open: false, error: "", refresh: originalRefresh });
});

it("browses a large collection in bounded pages and resets pagination for search and filters", () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  useCardLibrary.setState({ snapshot: snapshot(), open: true, refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  render(<CardLibrary />);
  fireEvent.click(screen.getByRole("button", { name: /^Cards/ }));
  expect(screen.getAllByRole("button", { name: /^Inspect Tool/ })).toHaveLength(30);
  fireEvent.click(screen.getByLabelText("Next cards"));
  fireEvent.click(screen.getByLabelText("Next cards"));
  expect(screen.getAllByRole("button", { name: /^Inspect Tool/ })).toHaveLength(15);
  expect(screen.getByText("Page 3 of 3")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search cards"), { target: { value: "Tool 75" } });
  expect(screen.getAllByRole("button", { name: /^Inspect Tool/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Inspect Tool 75" }));
  expect(screen.getByRole("complementary", { name: "Card details" }).textContent).toContain("Tool 75");
  fireEvent.change(screen.getByLabelText("Card category"), { target: { value: "Even" } });
  expect(screen.getByText("No matching cards")).toBeTruthy();
  expect(screen.queryByLabelText("Next cards")).toBeNull();
});

function packSnapshot() {
  const state = snapshot();
  state.card_definitions = Object.fromEntries(Object.entries(state.card_definitions).slice(0, 4));
  state.collection = Object.fromEntries(Object.entries(state.collection).slice(0, 4));
  const pluginId = TEST_CATALOG.plugins[0].id;
  state.plugins = { [pluginId]: { descriptor: TEST_CATALOG.plugins[0], installed: true, enabled: true } };
  for (const [id, name, cards] of [["alpha", "Alpha pack", ["card.1", "card.2"]], ["beta", "Beta pack", ["card.3", "card.4"]]] as const) {
    state.packs[id] = { definition: { id, name, plugin_id: pluginId, cards: [...cards], description: "Research tools", compatibility: false }, owned: true, opened: true, opened_at: null };
    for (const card of cards) state.collection[card].source_pack_ids = [id];
  }
  state.card_definitions["card.1"].label = "Review toolbox";
  state.card_definitions["card.1"].container = { ...TEST_CATALOG.node_types[0].container!, member_type: "card.2" };
  state.card_definitions["card.2"].label = "Skill";
  state.card_definitions["card.2"].description = "Review code with the toolbox's shared conventions.";
  state.card_definitions["card.2"].user_creatable = false;
  state.card_definitions["card.3"].label = "Simulation toolbox";
  state.card_definitions["card.3"].container = { ...TEST_CATALOG.node_types[0].container!, member_type: "card.4" };
  state.card_definitions["card.4"].label = "Skill";
  state.card_definitions["card.4"].user_creatable = false;
  state.available_card_ids = ["card.1", "card.3"];
  return state;
}

function openCards(state: LibrarySnapshot) {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  useCardLibrary.setState({ snapshot: state, open: true, refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  render(<CardLibrary />);
  fireEvent.click(screen.getByRole("button", { name: /^Cards/ }));
}

it("groups by collected pack provenance, deduplicates shared cards and filters by every source", () => {
  const state = packSnapshot();
  state.collection["card.1"].source_pack_ids.push("beta");
  openCards(state);
  expect(screen.getByRole("heading", { name: "Alpha pack" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Beta pack" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Alpha pack" }).closest("details")?.hasAttribute("open")).toBe(false);
  for (const name of ["Alpha pack", "Beta pack"]) fireEvent.click(screen.getByRole("heading", { name }).closest("summary")!);
  expect(screen.getAllByRole("button", { name: "Inspect Review toolbox" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: /^Inspect / })).toHaveLength(2);
  fireEvent.change(screen.getByLabelText("Source pack"), { target: { value: "pack:beta" } });
  expect(screen.queryByRole("heading", { name: "Alpha pack" })).toBeNull();
  expect(screen.getByRole("button", { name: "Inspect Review toolbox" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search cards"), { target: { value: "beta pack" } });
  expect(screen.getAllByRole("button", { name: /^Inspect / })).toHaveLength(2);
  fireEvent.change(screen.getByLabelText("Card category"), { target: { value: "Even" } });
  expect(screen.getByText("No matching cards")).toBeTruthy();
  expect(screen.getByText(/1 matching internal cards are hidden/)).toBeTruthy();
});

it("identifies internal cards by their exact container, retains descriptions and leads to the usable card", () => {
  openCards(packSnapshot());
  expect(screen.queryByRole("button", { name: "Inspect Review toolbox · Skill" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: /Show internal cards/ }));
  const inspect = screen.getByRole("button", { name: "Inspect Review toolbox · Skill" });
  expect(inspect.textContent).toContain("Review code with the toolbox's shared conventions.");
  expect(screen.getByRole("button", { name: "Inspect Simulation toolbox · Skill" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add Review toolbox · Skill to deck" })).toBeNull();
  fireEvent.click(inspect);
  const detail = within(screen.getByRole("complementary", { name: "Card details" }));
  expect(detail.getByText(/Open Review toolbox to use this card/)).toBeTruthy();
  fireEvent.click(detail.getByRole("button", { name: "Inspect Review toolbox" }));
  expect(detail.getByRole("heading", { name: "Review toolbox" })).toBeTruthy();
});

it("keeps disabled cards visible with their purpose, and distinguishes unavailability from internal use", () => {
  const state = packSnapshot();
  state.plugins[TEST_CATALOG.plugins[0].id].enabled = false;
  state.available_card_ids = [];
  openCards(state);
  fireEvent.change(screen.getByLabelText("Source pack"), { target: { value: "pack:alpha" } });
  expect(screen.getByRole("button", { name: "Inspect Review toolbox" }).textContent).toContain(state.card_definitions["card.1"].description);
  expect(screen.getByRole("button", { name: "Add Review toolbox to deck" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: /Show internal cards/ }));
  fireEvent.click(screen.getByRole("button", { name: "Inspect Review toolbox · Skill" }));
  const detail = within(screen.getByRole("complementary", { name: "Card details" }));
  expect(detail.getByText(/Install or enable its plugin/)).toBeTruthy();
  expect(detail.getByText(/Open Review toolbox to use this card/)).toBeTruthy();
});

it("refetches an invalidation arriving during an older request", async () => {
  let resolve!: (value: LibrarySnapshot) => void;
  const pending = new Promise<LibrarySnapshot>(done => { resolve = done; });
  const read = vi.spyOn(worldApi, "getCardLibrary").mockReturnValueOnce(pending).mockResolvedValue(snapshot(2));
  vi.spyOn(worldApi, "getCatalog").mockResolvedValue(TEST_CATALOG);
  const first = originalRefresh();
  const invalidated = originalRefresh();
  resolve(snapshot(1));
  await act(async () => { await Promise.all([first, invalidated]); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(useCardLibrary.getState().snapshot?.revision).toBe(2);
});

it("lets an empty wrapper lead to new-card collection and plugin controls on the source page", async () => {
  const state = packSnapshot();
  const pluginId = "example.tools";
  state.plugins[pluginId] = { descriptor: { ...TEST_CATALOG.plugins[0], id: pluginId }, installed: true, enabled: true };
  state.packs.alpha.definition.plugin_id = pluginId;
  for (const id of state.packs.alpha.definition.cards) state.card_definitions[id].plugin_id = pluginId;
  state.available_pack_ids = ["alpha", "beta"];
  state.collection["card.2"].source_pack_ids = [];
  openCards(state);
  fireEvent.click(screen.getByRole("button", { name: /^Packs/ }));
  const pack = within(screen.getByRole("article", { name: "Alpha pack" }));
  expect(pack.getAllByRole("button")).toHaveLength(1);
  fireEvent.click(pack.getByRole("button", { name: "View cards in Alpha pack" }));
  expect((screen.getByLabelText("Source pack") as HTMLSelectElement).value).toBe("pack:alpha");
  const controls = within(screen.getByLabelText("Source pack controls"));
  expect(controls.getByRole("button", { name: "Disable plugin" })).toBeTruthy();
  const saved = structuredClone(state);
  saved.revision++;
  saved.collection["card.2"].source_pack_ids = ["alpha"];
  const edit = vi.spyOn(worldApi, "editCardLibrary").mockResolvedValue(saved);
  await act(async () => { fireEvent.click(controls.getByRole("button", { name: "Collect 1 new card" })); });
  expect(edit).toHaveBeenCalledWith({ action: "open_pack", id: "alpha", expected_revision: state.revision });
  expect(controls.queryByRole("button", { name: /Collect/ })).toBeNull();
});

it("does not replace a successful edit with an older read", async () => {
  useCardLibrary.setState({ snapshot: snapshot(9) });
  vi.spyOn(worldApi, "getCardLibrary").mockResolvedValue(snapshot(8));
  vi.spyOn(worldApi, "getCatalog").mockResolvedValue(TEST_CATALOG);
  await originalRefresh();
  expect(useCardLibrary.getState().snapshot?.revision).toBe(9);
});
