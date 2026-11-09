// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useRef } from 'react';
import { ComponentPalette } from "./ComponentPalette";
import { useWorldStore } from "../state/worldStore";
import { useCardLibrary, type LibrarySnapshot, type DeckEntry } from "../state/cardLibrary";
import type { LegionSummary } from "../types/world";
import { usePaletteDropTarget } from './pointerDrag';

let hit: Element;
let frame: FrameRequestCallback | undefined;
beforeEach(() => {
  vi.stubGlobal("PointerEvent", class extends MouseEvent {
    pointerId = 1;
    isPrimary = true;
  });
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: () => hit });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frame = callback; return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => { frame = undefined; });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
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
function librarySnapshot(entries: DeckEntry[]): LibrarySnapshot {
  return { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {},
    card_definitions: {}, collection: {}, available_card_ids: [], available_pack_ids: [],
    active_deck_id: "custom", decks: [{ id: "custom", name: "Custom", icon: "folder", entries }] };
}

it("shows only saved Legions explicitly added to the active deck and tracks compatibility", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const instantiate = vi.fn();
  useCardLibrary.setState({ snapshot: librarySnapshot([{ kind: "legion", id: "saved-team" }]), busy: false,
    open: false, inspectedEntry: null, refresh: vi.fn().mockResolvedValue(undefined) });
  const legion: LegionSummary = { id: "saved-team", name: "Research team", node_count: 2, edge_count: 1,
    bounds: { width: 200, height: 200 }, node_types: [], plugin_ids: [], compatible: true, issues: [], revision: 1 };
  const unlisted: LegionSummary = { ...legion, id: "unlisted-team", name: "Unlisted team" };
  useWorldStore.setState({ legions: [legion, unlisted], legionError: undefined, instantiateLegion: instantiate });
  const screen = render(<ComponentPalette />);
  expect(screen.queryByRole("button", { name: "Place Unlisted team" })).toBeNull();
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
  act(() => useWorldStore.setState({ legions: [{ ...legion, compatible: false }, unlisted] }));
  const unavailable = screen.getByRole("button", { name: "Research team unavailable" });
  expect(unavailable.getAttribute("aria-disabled")).toBeNull();
  fireEvent.click(unavailable);
  expect(useCardLibrary.getState().inspectedEntry).toEqual({ kind: "legion", id: "saved-team" });
  act(() => useWorldStore.setState({ legions: [unlisted] }));
  expect(screen.getByRole("button", { name: "saved-team unavailable" })).toBeTruthy();
});

it("removes a saved Legion from the current deck without deleting its source", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const edit = vi.fn().mockResolvedValue(null);
  const remove = vi.fn();
  const place = vi.fn();
  useCardLibrary.setState({ snapshot: librarySnapshot([{ kind: "legion", id: "team" }]), busy: false,
    refresh: vi.fn().mockResolvedValue(undefined), edit });
  useWorldStore.setState({ legions: [{ id: "team", name: "Team", compatible: false } as LegionSummary],
    deleteLegion: remove, instantiateLegion: place });
  const screen = render(<ComponentPalette />);
  startDrag(screen.getByRole("button", { name: "Team unavailable" }));
  dropOn(screen.getByRole("region", { name: "Discard card" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith({ action: "update_deck", id: "custom", entries: [] }));
  expect(remove).not.toHaveBeenCalled();
  expect(place).not.toHaveBeenCalled();
});

it('opens an imported Legion and discards only its deck reference', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const snapshot = librarySnapshot([{ kind: 'legion_instance', id: 'imported' }]);
  snapshot.legion_instances = { imported: { name: 'Imported research', description: 'Migration complete: 12 sessions, 896 messages.' } };
  const open = vi.fn().mockResolvedValue(true);
  const instantiate = vi.fn();
  const remove = vi.fn();
  const edit = vi.fn().mockResolvedValue(null);
  useCardLibrary.setState({ snapshot, busy: false, open: false, refresh: vi.fn(), edit });
  useWorldStore.setState({ legions: [], openLegionInstance: open, instantiateLegion: instantiate, deleteCards: remove });
  const view = render(<ComponentPalette />);
  const button = view.getByRole('button', { name: 'Open Imported research' });
  expect(view.getByRole('status').textContent).toBe('Migration complete: 12 sessions, 896 messages.');
  fireEvent.click(button);
  expect(open).toHaveBeenCalledWith('imported');
  expect(instantiate).not.toHaveBeenCalled();
  startDrag(button);
  dropOn(view.getByRole('region', { name: 'Discard card' }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith({ action: 'update_deck', id: 'custom', entries: [] }));
  expect(remove).not.toHaveBeenCalled();
});

it('drags the existing imported Legion ID from the deck to the canvas', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const snapshot = librarySnapshot([{ kind: 'legion_instance', id: 'imported' }]);
  snapshot.legion_instances = { imported: { name: 'Imported research', description: 'Migration complete.' } };
  useCardLibrary.setState({ snapshot, busy: false, open: false, refresh: vi.fn() });
  const received = vi.fn();
  function CanvasTarget() {
    const ref = useRef<HTMLDivElement>(null);
    usePaletteDropTarget(ref, { accepts: item => Boolean(item.payload), drop: item => received(item.payload) });
    return <div ref={ref} role="region" aria-label="Canvas drop target" />;
  }
  const view = render(<><ComponentPalette /><CanvasTarget /></>);
  startDrag(view.getByRole('button', { name: 'Open Imported research' }));
  dropOn(view.getByRole('region', { name: 'Canvas drop target' }));
  expect(received).toHaveBeenCalledWith({ version: 1, kind: 'legion_instance', id: 'imported' });
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
