// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LibraryPack } from "./LibraryPack";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { TEST_CATALOG } from "../state/catalog.fixture";
import { worldApi } from "../api/client";
import { paintPackCover } from "./pack3d/artwork";

vi.mock('./pack3d/artwork', () => ({ paintPackCover: vi.fn() }));

const originalRefresh = useCardLibrary.getState().refresh;
const opened = vi.fn();
const browse = vi.fn();
function fixture(): LibrarySnapshot {
  const card = TEST_CATALOG.node_types[0];
  const descriptor = TEST_CATALOG.plugins.find(plugin => plugin.id === card.plugin_id)!;
  return { schema_version: 1, revision: 1, migration_pending: false,
    plugins: { [card.plugin_id]: { descriptor, enabled: true, installed: true } },
    packs: { tools: { definition: { id: "tools", plugin_id: card.plugin_id, name: "Tools", description: "Useful tools", cards: [card.id] }, owned: true, opened: false, opened_at: null } },
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

it.each(['standard', 'premium', 'paper', 'collector'] as const)("keeps a pending or failed %s pack sealed, then reveals only a successful retry", async packaging => {
  vi.useFakeTimers();
  const state = fixture();
  state.packs.tools.definition.packaging = packaging;
  const openLabel = packaging === 'paper' || packaging === 'collector' ? 'Open Tools' : 'Tear open Tools';
  useCardLibrary.setState({ snapshot: state, refresh: async () => {} });
  let reject!: (reason: Error) => void;
  const edit = vi.spyOn(worldApi, "editCardLibrary").mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
  const { container } = render(<Harness />);
  expect(container.querySelector('article')?.dataset.packaging).toBe(packaging);
  fireEvent.click(screen.getByRole("button", { name: openLabel }));
  expect(screen.getByRole("button", { name: openLabel }).hasAttribute("disabled")).toBe(true);
  expect(container.querySelector(".is-opened")).toBeNull();
  expect(opened).not.toHaveBeenCalled();
  await act(async () => { reject(new Error("Please retry")); });
  expect(container.querySelector(".is-revealing")).toBeNull();
  expect(screen.getByRole("button", { name: openLabel }).hasAttribute("disabled")).toBe(false);
  const saved = structuredClone(state);
  saved.revision = 2;
  saved.packs.tools.opened = true;
  const id = saved.packs.tools.definition.cards[0];
  saved.collection[id] = { card_id: id, plugin_id: saved.packs.tools.definition.plugin_id, source_pack_ids: ["tools"], unlocked: true, unlocked_at: "2026-09-10T00:00:00Z" };
  edit.mockResolvedValueOnce(saved);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: openLabel })); });
  expect(edit).toHaveBeenCalledTimes(2);
  expect(container.querySelector(".is-opened.is-revealing")).toBeTruthy();
  expect(opened).toHaveBeenCalledWith("tools");
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "View cards in Tools" }).hasAttribute("disabled")).toBe(true);
  act(() => vi.advanceTimersByTime(2100));
  fireEvent.click(screen.getByRole("button", { name: "View cards in Tools" }));
  expect(browse).toHaveBeenCalledWith("tools");
});

it("keeps the opened flat cover and its artwork when a pack becomes unavailable", () => {
  const state = fixture();
  state.packs.tools.opened = true;
  state.packs.tools.definition.artwork_url = "/api/plugins/example/assets/cover";
  useCardLibrary.setState({ snapshot: state });
  const { container } = render(<Harness />);
  expect(container.querySelector(".is-opened")).toBeTruthy();
  expect(container.querySelector(".is-revealing")).toBeNull();
  expect(screen.getByRole("button", { name: "View cards in Tools" }).hasAttribute("disabled")).toBe(false);
  expect(container.querySelector(".pack-cover")).toBeTruthy();
  expect(paintPackCover).toHaveBeenCalledWith(expect.any(HTMLCanvasElement), expect.objectContaining({ name: "Tools", artwork: state.packs.tools.definition.artwork_url }));
  const unavailable = structuredClone(state);
  unavailable.available_pack_ids = [];
  act(() => useCardLibrary.setState({ snapshot: unavailable }));
  fireEvent.click(screen.getByRole("button", { name: "View cards in Tools" }));
  expect(browse).toHaveBeenCalledWith("tools");
  expect(container.querySelector(".is-opened")).toBeTruthy();
  expect(container.querySelector("details")).toBeNull();
});

it("reveals the saved finish after the face emerges even when parent props are stale", async () => {
  vi.useFakeTimers();
  const state = fixture();
  const id = state.packs.tools.definition.cards[0];
  state.collection[id] = { card_id: id, plugin_id: state.packs.tools.definition.plugin_id, source_pack_ids: [],
    unlocked: true, unlocked_at: "2026-09-10T00:00:00Z", finish: "foil" };
  const saved = structuredClone(state);
  saved.packs.tools.opened = true;
  saved.collection[id].finish = "laser";
  saved.card_definitions[id] = { ...saved.card_definitions[id], label: 'Saved photo',
    card_face: { variant: 'image', tone: 'rose', image_url: '/saved-photo.png' } };
  saved.card_definitions[id] = { ...saved.card_definitions[id], label: 'Saved photo',
    card_face: { variant: 'image', tone: 'rose', image_url: '/saved-photo.png' } };
  useCardLibrary.setState({ snapshot: state, refresh: async () => {} });
  let resolve!: (value: LibrarySnapshot) => void;
  vi.spyOn(worldApi, "editCardLibrary").mockReturnValue(new Promise(done => { resolve = done; }));
  const element = <LibraryPack pack={state.packs.tools} snapshot={state} onOpened={opened} onBrowse={browse} />;
  const { container, rerender } = render(element);
  expect(container.querySelector(".card-finish-layer")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Tear open Tools" }));
  expect(container.querySelector(".card-finish-layer")).toBeNull();
  await act(async () => resolve(saved));
  expect(container.querySelector(".is-opened.is-revealing")).toBeTruthy();
  expect(container.querySelector('.pack-reveal-card .card-face-copy strong')?.textContent).toBe('Saved photo');
  expect(container.querySelector('.pack-reveal-card .card-face-image')?.getAttribute('src')).toBe('/saved-photo.png');
  expect(container.querySelector('.pack-reveal-card .card-face-copy strong')?.textContent).toBe('Saved photo');
  expect(container.querySelector('.pack-reveal-card .card-face-image')?.getAttribute('src')).toBe('/saved-photo.png');
  expect(container.querySelector(".card-finish-layer")).toBeNull();
  act(() => vi.advanceTimersByTime(720));
  const material = container.querySelector(".pack-reveal-card .card-finish-layer");
  expect(material?.getAttribute("data-finish")).toBe("laser");
  expect(material?.getAttribute("data-reveal")).toBe("true");
  rerender(element);
  expect(container.querySelector('.pack-reveal-card .card-face')?.getAttribute('data-tone')).toBe('rose');
  expect(container.querySelector('.pack-reveal-card .card-face')?.getAttribute('data-tone')).toBe('rose');
  expect(container.querySelector(".card-finish-layer")?.getAttribute("data-finish")).toBe("laser");
  act(() => vi.advanceTimersByTime(1380));
  expect(container.querySelector(".card-finish-layer")).toBeNull();
});

it.each(['missing', 'error'] as const)('marks a %s pack with a warning while preserving its artwork and inspection, then clears it after recovery', kind => {
  const state = fixture();
  const pluginId = state.packs.tools.definition.plugin_id;
  state.packs.tools.definition.artwork_url = '/cover.png';
  const versions = [{ id: pluginId, name: 'Tools', version: '1.0.0', selected: true, loaded: true,
    environment: { state: kind === 'error' ? 'environment_failed' : 'environment_ready', error: null } }];
  if (kind === 'missing') state.plugins[pluginId].installed = false;
  const inspect = vi.fn();
  const props = { pack: state.packs.tools, snapshot: state, versions, onOpened: opened, onBrowse: browse, onInspect: inspect };
  const { container, rerender } = render(<LibraryPack {...props} />);
  expect(container.querySelector('article')?.dataset.packIssue).toBe(kind);
  expect(screen.getByText(kind === 'missing' ? 'Pack uninstalled' : 'Environment failed').closest('.library-pack')?.getAttribute('data-pack-notice')).toBe('warning');
  expect(paintPackCover).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), expect.objectContaining({ artwork: '/cover.png', notice: expect.objectContaining({ kind: 'warning' }) }));
  fireEvent.click(screen.getByRole('button', { name: 'View pack Tools' }));
  expect(inspect).toHaveBeenCalledOnce();
  expect(opened).not.toHaveBeenCalled();
  state.plugins[pluginId].installed = true;
  versions[0].environment.state = 'environment_ready';
  rerender(<LibraryPack {...props} />);
  expect(container.querySelector('article')?.hasAttribute('data-pack-issue')).toBe(false);
  expect(container.querySelector('article')?.hasAttribute('data-pack-notice')).toBe(false);
  expect(paintPackCover).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), expect.objectContaining({ artwork: '/cover.png', notice: undefined }));
});
