// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CardLibrary } from "./CardLibrary";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { TEST_CATALOG } from "../state/catalog.fixture";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";

const originalRefresh = useCardLibrary.getState().refresh;
function snapshot(revision = 1): LibrarySnapshot {
  const definitions = Array.from({ length: 75 }, (_, i) => ({ ...TEST_CATALOG.node_types[0], id: `card.${i + 1}`,
    label: `Tool ${String(i + 1).padStart(2, "0")}`, deck_label: i % 2 ? "Even" : "Odd" }));
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

it("does not replace a successful edit with an older read", async () => {
  useCardLibrary.setState({ snapshot: snapshot(9) });
  vi.spyOn(worldApi, "getCardLibrary").mockResolvedValue(snapshot(8));
  vi.spyOn(worldApi, "getCatalog").mockResolvedValue(TEST_CATALOG);
  await originalRefresh();
  expect(useCardLibrary.getState().snapshot?.revision).toBe(9);
});
