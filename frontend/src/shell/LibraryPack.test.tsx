// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LibraryPack } from "./LibraryPack";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { TEST_CATALOG } from "../state/catalog.fixture";
import { worldApi } from "../api/client";

const originalRefresh = useCardLibrary.getState().refresh;
const opened = vi.fn();
const browse = vi.fn();
function fixture(): LibrarySnapshot {
  const card = TEST_CATALOG.node_types[0];
  const descriptor = TEST_CATALOG.plugins.find(plugin => plugin.id === card.plugin_id)!;
  return { schema_version: 1, revision: 1, migration_pending: false,
    plugins: { [card.plugin_id]: { descriptor, enabled: true, installed: true } },
    packs: { tools: { definition: { id: "tools", plugin_id: card.plugin_id, name: "Tools", description: "Useful tools", cards: [card.id], compatibility: false }, owned: true, opened: false, opened_at: null } },
    card_definitions: { [card.id]: card }, collection: {}, decks: [], active_deck_id: "",
    available_pack_ids: ["tools"], available_card_ids: [] };
}
function Harness() {
  const snapshot = useCardLibrary(state => state.snapshot)!;
  return <LibraryPack pack={snapshot.packs.tools} snapshot={snapshot} onOpened={opened} onBrowse={browse} />;
}
afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); opened.mockClear(); browse.mockClear();
  useCardLibrary.setState({ snapshot: null, busy: false, error: "", refresh: originalRefresh });
});

it("keeps a failed or pending open sealed, then reveals only an authoritative successful retry", async () => {
  vi.useFakeTimers();
  const state = fixture();
  useCardLibrary.setState({ snapshot: state, refresh: async () => {} });
  let reject!: (reason: Error) => void;
  const edit = vi.spyOn(worldApi, "editCardLibrary").mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
  const { container } = render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Tear open Tools" }));
  expect(screen.getByRole("button", { name: "Tear open Tools" }).hasAttribute("disabled")).toBe(true);
  expect(container.querySelector(".is-opened")).toBeNull();
  expect(opened).not.toHaveBeenCalled();
  await act(async () => { reject(new Error("Please retry")); });
  expect(container.querySelector(".is-revealing")).toBeNull();
  expect(screen.getByRole("button", { name: "Tear open Tools" }).hasAttribute("disabled")).toBe(false);
  const saved = structuredClone(state);
  saved.revision = 2;
  saved.packs.tools.opened = true;
  const id = saved.packs.tools.definition.cards[0];
  saved.collection[id] = { card_id: id, plugin_id: saved.packs.tools.definition.plugin_id, source_pack_ids: ["tools"], unlocked: true, unlocked_at: "2026-09-10T00:00:00Z" };
  edit.mockResolvedValueOnce(saved);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Tear open Tools" })); });
  expect(edit).toHaveBeenCalledTimes(2);
  expect(container.querySelector(".is-opened.is-revealing")).toBeTruthy();
  expect(opened).toHaveBeenCalledWith("tools");
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "View cards in Tools" }).hasAttribute("disabled")).toBe(true);
  act(() => vi.advanceTimersByTime(2100));
  fireEvent.click(screen.getByRole("button", { name: "View cards in Tools" }));
  expect(browse).toHaveBeenCalledWith("tools");
});

it("retains the torn wrapper for an opened pack with new cards and falls back when artwork is missing", () => {
  const state = fixture();
  state.packs.tools.opened = true;
  state.packs.tools.definition.artwork_url = "/api/plugins/example/assets/cover";
  useCardLibrary.setState({ snapshot: state });
  const { container } = render(<Harness />);
  expect(container.querySelector(".is-opened")).toBeTruthy();
  expect(container.querySelector(".is-revealing")).toBeNull();
  expect(screen.getByRole("button", { name: "View cards in Tools" }).hasAttribute("disabled")).toBe(false);
  fireEvent.error(container.querySelector("img")!);
  expect(container.querySelector(".pack-guilloche")).toBeTruthy();
  expect(container.querySelector(".pack-title")?.textContent).toBe("Tools");
  const unavailable = structuredClone(state);
  unavailable.available_pack_ids = [];
  act(() => useCardLibrary.setState({ snapshot: unavailable }));
  fireEvent.click(screen.getByRole("button", { name: "View cards in Tools" }));
  expect(browse).toHaveBeenCalledWith("tools");
  expect(container.querySelector(".is-opened")).toBeTruthy();
  expect(container.querySelector("details")).toBeNull();
});
