// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ComponentPalette } from "./ComponentPalette";
import { useWorldStore } from "../state/worldStore";
import { useCardLibrary } from "../state/cardLibrary";
import type { LegionSummary } from "../types/world";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("exposes saved Legions without deck membership and tracks library changes", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const instantiate = vi.fn();
  useCardLibrary.setState({ snapshot: null, refresh: vi.fn().mockResolvedValue(undefined) });
  const legion: LegionSummary = { id: "saved-team", name: "Research team", node_count: 2, edge_count: 1,
    bounds: { width: 200, height: 200 }, node_types: [], plugin_ids: [], compatible: true, issues: [], revision: 1 };
  useWorldStore.setState({ legions: [legion], legionError: undefined, instantiateLegion: instantiate });
  const screen = render(<ComponentPalette />);
  fireEvent.click(screen.getByRole("tab", { name: /Legions/ }));
  const place = screen.getByRole("button", { name: "Place Research team" });
  expect(place.draggable).toBe(true);
  fireEvent.click(place);
  expect(instantiate).toHaveBeenCalledWith("saved-team");
  act(() => useWorldStore.setState({ legions: [{ ...legion, compatible: false }] }));
  expect(screen.getByRole("button", { name: "Research team unavailable" }).getAttribute("aria-disabled")).toBe("true");
  act(() => useWorldStore.setState({ legions: [] }));
  expect(screen.getByText("No saved Legions")).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Legions/ }).getAttribute("aria-selected")).toBe("true");
});

it("deletes a saved Legion directly with confirmation without placing it", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const remove = vi.fn().mockResolvedValue(true);
  const place = vi.fn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  useCardLibrary.setState({ snapshot: null, busy: false, refresh: vi.fn().mockResolvedValue(undefined) });
  useWorldStore.setState({ legions: [{ id: "team", name: "Team", compatible: false } as LegionSummary],
    deleteLegion: remove, instantiateLegion: place });
  const screen = render(<ComponentPalette />);
  fireEvent.click(screen.getByRole("tab", { name: /Legions/ }));
  fireEvent.dragStart(screen.getByRole("button", { name: "Team unavailable" }), { dataTransfer: { setData: vi.fn() } });
  fireEvent.drop(screen.getByRole("region", { name: "Discard card" }));
  expect(remove).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  fireEvent.dragStart(screen.getByRole("button", { name: "Team unavailable" }), { dataTransfer: { setData: vi.fn() } });
  fireEvent.drop(screen.getByRole("region", { name: "Discard card" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("team"));
  expect(place).not.toHaveBeenCalled();
});

it("removes an unavailable entry only from the current deck", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const edit = vi.fn().mockResolvedValue(null);
  const remove = vi.fn();
  useWorldStore.setState({ deleteLegion: remove, legions: [] });
  useCardLibrary.setState({ busy: false, refresh: vi.fn().mockResolvedValue(undefined), edit,
    snapshot: { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {},
      card_definitions: {}, collection: {}, available_card_ids: [], available_pack_ids: [],
      active_deck_id: "custom", decks: [{ id: "custom", name: "Custom", icon: "folder",
        entries: [{ kind: "node", id: "missing" }, { kind: "legion", id: "saved" }] }] } });
  const screen = render(<ComponentPalette />);
  const trash = screen.getByRole("region", { name: "Discard card" });
  fireEvent.drop(trash);
  expect(edit).not.toHaveBeenCalled();
  const card = screen.getByRole("button", { name: "missing unavailable" });
  const dataTransfer = { setData: vi.fn(), dropEffect: "none" };
  fireEvent.dragStart(card, { dataTransfer });
  fireEvent.dragOver(trash, { dataTransfer });
  expect(trash.classList.contains("is-active")).toBe(true);
  expect(dataTransfer.dropEffect).toBe("move");
  fireEvent.drop(trash);
  expect(trash.classList.contains("is-active")).toBe(false);
  await waitFor(() => expect(edit).toHaveBeenCalledWith({ action: "update_deck", id: "custom",
    entries: [{ kind: "legion", id: "saved" }] }));
  expect(remove).not.toHaveBeenCalled();
});
