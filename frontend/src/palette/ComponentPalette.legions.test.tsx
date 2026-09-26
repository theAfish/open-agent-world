// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ComponentPalette } from "./ComponentPalette";
import { CardLibrary } from "../shell/CardLibrary";
import { worldApi } from "../api/client";
import { useLocale } from "../i18n";
import { useWorldStore } from "../state/worldStore";
import { useCardLibrary, type DeckEntry, type LibrarySnapshot } from "../state/cardLibrary";
import type { LegionSummary } from "../types/world";

const originalLibraryState = useCardLibrary.getState();
function librarySnapshot(entries: DeckEntry[] = []): LibrarySnapshot {
  return { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {},
    card_definitions: {}, collection: {}, available_card_ids: [], available_pack_ids: [],
    active_deck_id: "custom", decks: [{ id: "custom", name: "Custom", icon: "folder", entries }] };
}

let hit: Element;
let frame: FrameRequestCallback | undefined;
beforeEach(() => {
  useLocale.setState({ locale: "en" });
  useCardLibrary.setState({ ...originalLibraryState, refresh: vi.fn().mockResolvedValue(undefined) });
  vi.stubGlobal("PointerEvent", class extends MouseEvent {
    pointerId = 1;
    isPrimary = true;
  });
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: () => hit });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frame = callback; return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => { frame = undefined; });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); useCardLibrary.setState(originalLibraryState); });
function startDrag(source: HTMLElement) {
  source.setPointerCapture = vi.fn(); source.hasPointerCapture = () => false;
  fireEvent.pointerDown(source, { button: 0, buttons: 1, clientX: 10, clientY: 10 });
  hit = source;
  fireEvent.pointerMove(window, { buttons: 1, clientX: 30, clientY: 30 });
}
function moveOver(target: HTMLElement) {
  hit = target;
  fireEvent.pointerMove(window, { buttons: 1, clientX: 50, clientY: 50 });
  act(() => { const callback = frame; frame = undefined; callback?.(performance.now()); });
}
function dropOn(target: HTMLElement) {
  hit = target;
  fireEvent.pointerUp(window, { button: 0, clientX: 50, clientY: 50 });
}

it("discovers saved Legions in the Library before adding them to a deck and tracks availability", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const instantiate = vi.fn();
  const snapshot = librarySnapshot();
  useCardLibrary.setState({ snapshot, open: true, tab: "cards" });
  const saved = { ...librarySnapshot([{ kind: "legion", id: "saved-team" }]), revision: 2 };
  const edit = vi.spyOn(worldApi, "editCardLibrary").mockResolvedValue(saved);
  const legion: LegionSummary = { id: "saved-team", name: "Research team", node_count: 2, edge_count: 1,
    bounds: { width: 200, height: 200 }, node_types: [], plugin_ids: [], compatible: true, issues: [], revision: 1 };
  useWorldStore.setState({ legions: [legion], legionError: undefined, instantiateLegion: instantiate });
  const screen = render(<><CardLibrary /><ComponentPalette /></>);
  expect(screen.getByRole("button", { name: "Inspect Research team" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Place Research team" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add Research team to deck" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith({ action: "move_entry", id: "custom",
    entry: { kind: "legion", id: "saved-team" }, expected_revision: 1 }));
  await waitFor(() => expect(useCardLibrary.getState().snapshot).toEqual(saved));
  fireEvent.click(screen.getByRole("button", { name: "Close Library" }));
  const place = screen.getByRole("button", { name: "Place Research team" });
  expect(place.draggable).toBe(false); // Deck previews use Pointer Events, not an OS drag image.
  fireEvent.click(place);
  expect(instantiate).toHaveBeenCalledWith("saved-team");
  instantiate.mockClear();
  startDrag(place);
  fireEvent.click(place); // Keyboard activation must not instantiate an in-flight preview.
  expect(instantiate).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(document.querySelector(".palette-drag-preview")).toBeNull();
  fireEvent.click(place);
  expect(instantiate).toHaveBeenCalledWith("saved-team");
  act(() => useWorldStore.setState({ legions: [{ ...legion, compatible: false }] }));
  const unavailable = screen.getByRole("button", { name: "Research team unavailable" });
  expect(unavailable.getAttribute("aria-disabled")).toBeNull();
  fireEvent.click(unavailable);
  expect(useCardLibrary.getState().inspectedEntry).toEqual({ kind: "legion", id: "saved-team" });
  act(() => useWorldStore.setState({ legions: [] }));
  expect(screen.queryByRole("button", { name: "Inspect Research team" })).toBeNull();
  expect(screen.getByRole("button", { name: "saved-team unavailable" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Custom/ }).getAttribute("aria-selected")).toBe("true");
});

it("deletes a saved Legion from the Library with confirmation without placing it", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const remove = vi.fn().mockResolvedValue(true);
  const place = vi.fn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  useCardLibrary.setState({ snapshot: librarySnapshot(), open: true, tab: "cards" });
  useWorldStore.setState({ legions: [{ id: "team", name: "Team", compatible: false, issues: [],
    node_count: 2, edge_count: 1, node_types: [], plugin_ids: [], revision: 1,
    bounds: { width: 200, height: 200 } }],
    deleteLegion: remove, instantiateLegion: place });
  const screen = render(<><CardLibrary /><ComponentPalette /></>);
  fireEvent.click(screen.getByRole("button", { name: "Inspect Team" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete saved formation" }));
  expect(confirm).toHaveBeenCalledWith("Remove Team from the Legion library?");
  expect(remove).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Delete saved formation" }));
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
  dropOn(trash);
  expect(edit).not.toHaveBeenCalled();
  const card = screen.getByRole("button", { name: "missing unavailable" });
  startDrag(card);
  moveOver(trash);
  expect(trash.classList.contains("is-active")).toBe(true);
  dropOn(trash);
  expect(trash.classList.contains("is-active")).toBe(false);
  await waitFor(() => expect(edit).toHaveBeenCalledWith({ action: "update_deck", id: "custom",
    entries: [{ kind: "legion", id: "saved" }] }));
  expect(remove).not.toHaveBeenCalled();
});

it("moves a dragged entry to a deck tab using one revisioned library action", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const edit = vi.fn().mockResolvedValue(null);
  const place = vi.fn();
  useWorldStore.setState({ createCard: place, legions: [] });
  useCardLibrary.setState({ busy: false, refresh: vi.fn().mockResolvedValue(undefined), edit,
    snapshot: { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {},
      card_definitions: {}, collection: {}, available_card_ids: [], available_pack_ids: [], active_deck_id: "source",
      decks: [{ id: "source", name: "Source", icon: "folder", entries: [{ kind: "node", id: "missing" }] },
        { id: "target", name: "Target", icon: "folder", entries: [] }] } });
  const screen = render(<ComponentPalette />);
  const target = screen.getByRole("tab", { name: /Target/ });
  dropOn(target);
  expect(edit).not.toHaveBeenCalled();
  startDrag(screen.getByRole("button", { name: "missing unavailable" }));
  const sourceTab = screen.getByRole("tab", { name: /Source/ });
  moveOver(sourceTab);
  expect(sourceTab.classList.contains("is-drop-target")).toBe(false);
  moveOver(target);
  expect(target.classList.contains("is-drop-target")).toBe(true);
  dropOn(target);
  await waitFor(() => expect(edit).toHaveBeenCalledWith({ action: "move_entry", source_deck_id: "source", id: "target", entry: { kind: "node", id: "missing" } }));
  expect(target.classList.contains("is-drop-target")).toBe(false);
  expect(place).not.toHaveBeenCalled();
});
