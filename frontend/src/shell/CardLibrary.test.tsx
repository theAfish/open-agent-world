// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ComponentPalette } from "../palette/ComponentPalette";
import { CardLibrary } from "./CardLibrary";
import { useCardLibrary, type LibrarySnapshot } from "../state/cardLibrary";
import { TEST_CATALOG } from "../state/catalog.fixture";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import * as hostedAuth from '../auth/hostedSession';

vi.mock('./pack3d/artwork', () => ({ paintPackCover: vi.fn() }));

const originalRefresh = useCardLibrary.getState().refresh;
beforeEach(() => {
  vi.spyOn(worldApi, 'getInstalledPacks').mockResolvedValue({ restart_required: false, versions: [] });
  vi.spyOn(worldApi, 'getPackUninstallPlan').mockImplementation(async id => ({ id, owner: id, can_uninstall: true, blockers: [], resources: [], restart_required: true, source: 'installed' }));
});

it('keeps the IDE in collapsed developer tools and returns to the Library after closing it', async () => {
  useCardLibrary.setState({ snapshot: snapshot(), open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  vi.spyOn(worldApi, 'listPackProjects').mockResolvedValue({ projects: [], default_directory: '/projects' });
  const { container } = render(<CardLibrary />);
  expect(container.querySelector('.library-tab-row')!.textContent).not.toContain('Pack IDE');
  const tools = screen.getByText('Developer tools').closest('details')!;
  expect(tools.open).toBe(false);
  tools.open = true;
  await act(async () => fireEvent.click(within(tools).getByRole('button', { name: 'Pack IDE Experimental' })));
  const ide = screen.getByRole('dialog', { name: 'Pack IDE' });
  expect(within(ide).getByText('Visual authoring is still in development.')).toBeTruthy();
  fireEvent.click(within(ide).getByRole('button', { name: 'Close Pack IDE' }));
  expect(screen.queryByRole('dialog', { name: 'Pack IDE' })).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Pack & Card Library' })).toBeTruthy();
});
const installedContentPack = { id: 'local.research', name: 'Research kit', version: '1.0.0', selected: true, loaded: false,
  kind: 'content' as const, environment: null };

it('keeps hosted collections available without exposing plugin installation or store controls', async () => {
  vi.spyOn(hostedAuth, 'isHosted').mockReturnValue(true);
  useCardLibrary.setState({ snapshot: snapshot(), open: true, tab: 'store', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  await act(async () => { render(<CardLibrary />); });
  expect(screen.queryByRole('button', { name: 'Store' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Install Pack from File...' })).toBeNull();
  expect(screen.queryByLabelText('Pack file')).toBeNull();
  expect(worldApi.getInstalledPacks).not.toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: 'Pack inventory' })).toBeTruthy();
});

it('adds an installed file to the inventory immediately and closes its installation panel', async () => {
  useCardLibrary.setState({ snapshot: snapshot(), open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  vi.spyOn(worldApi, 'inspectPack').mockResolvedValue({ manifest: installedContentPack, sha256: 'digest' });
  vi.spyOn(worldApi, 'installPack').mockResolvedValue({ restart_required: true, versions: [installedContentPack] });
  const { container } = render(<CardLibrary />);
  const file = new File(['zip'], 'research.oawpack');
  vi.spyOn(file, 'slice').mockReturnValue({ arrayBuffer: async () => new Uint8Array([0x50, 0x4b, 3, 4]).buffer } as Blob);
  await act(async () => fireEvent.change(screen.getByLabelText('Pack file'), { target: { files: [file] } }));
  expect(screen.getByRole('region', { name: 'Install Pack' })).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Install Pack' })));
  const pack = screen.getByRole('article', { name: 'Research kit' });
  expect(within(pack).getByText('Restart required')).toBeTruthy();
  expect(container.querySelector('.pack-install-popover')).toBeNull();
  expect(container.querySelector('.pack-installer')).toBeNull();
  expect(screen.getByRole('button', { name: 'Install Pack from File...' }).hasAttribute('disabled')).toBe(false);
  expect(screen.queryByRole('button', { name: 'Uninstall Pack' })).toBeNull();
  fireEvent.click(within(pack).getByRole('button', { name: /^(Tear open|View pack)/ }));
  expect(screen.getByRole('button', { name: 'Uninstall Pack' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Open pack' })).toBeNull();
});

it('opens installed packs and browses their cards before entering secondary management', async () => {
  const state = packSnapshot();
  state.packs.alpha.opened = false;
  state.available_pack_ids = ['alpha'];
  const item = { ...installedContentPack, id: state.packs.alpha.definition.plugin_id, loaded: true };
  vi.mocked(worldApi.getInstalledPacks).mockResolvedValue({ restart_required: false, versions: [item] });
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  const saved = structuredClone(state);
  saved.revision++;
  saved.packs.alpha.opened = true;
  vi.spyOn(worldApi, 'editCardLibrary').mockResolvedValue(saved);
  await act(async () => { render(<CardLibrary />); });
  const pack = screen.getByRole('article', { name: 'Alpha pack' });
  expect(within(pack).getByRole('button', { name: 'Tear open Alpha pack' })).toBeTruthy();
  await act(async () => fireEvent.click(within(pack).getByRole('button', { name: 'Tear open Alpha pack' })));
  expect(pack.classList.contains('is-revealing')).toBe(true);
  expect(screen.queryByRole('region', { name: 'Pack details' })).toBeNull();
  expect(within(pack).getByRole('button', { name: 'View cards in Alpha pack' }).hasAttribute('disabled')).toBe(true);
  const animationEnd = new Event('animationend', { bubbles: true });
  Object.defineProperty(animationEnd, 'animationName', { value: 'packOpeningDip' });
  fireEvent(pack.querySelector('.pack-cover')!, animationEnd);
  fireEvent.click(within(pack).getByRole('button', { name: 'View cards in Alpha pack' }));
  expect((screen.getByLabelText('Source pack') as HTMLSelectElement).value).toBe('pack:alpha');
  expect(screen.queryByRole('button', { name: 'Uninstall Pack' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Pack details' }));
  expect(screen.getByRole('button', { name: 'View cards' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Uninstall Pack' })).toBeTruthy();
});

it('shows source Pack environments and previews source removal without assuming Sandbox readiness', async () => {
  const state = packSnapshot();
  const owner = state.packs.alpha.definition.plugin_id;
  vi.mocked(worldApi.getInstalledPacks).mockResolvedValue({ restart_required: false, versions: [], inventory: [{
    id: 'distribution.alpha', owner, name: 'Alpha pack', version: '1.0.0', source: 'development', enabled: true, loaded: true,
    environments: [
      { id: 'engine', target: 'host-worker', provider: 'python', management: 'managed', state: 'ready', python: ['numpy'], node: [] },
      { id: 'analysis', target: 'sandbox', provider: 'python', management: 'managed', state: 'on_use', python: ['numpy'], node: ['bohr'] },
    ], backend_requirements: [], frontend_dependencies: ['three'], warnings: [],
    backend_libraries: [{ name: 'google-adk', version: '2.8.0', management: 'host-provided' },
      { name: 'pymatgen', version: null, management: 'managed' }],
  }] });
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  await act(async () => { render(<CardLibrary />); });
  fireEvent.click(screen.getByRole('button', { name: 'View pack Alpha pack' }));
  const disclosure = screen.getByText('Pack dependencies').closest('details')!;
  expect(disclosure.open).toBe(false);
  fireEvent.click(disclosure.querySelector('summary')!);
  const dependencies = screen.getByRole('region', { name: 'Pack dependencies' });
  expect(dependencies.textContent).toContain('Development source');
  expect(within(dependencies).getByText('Ready')).toBeTruthy();
  expect(within(dependencies).getByText('Prepared when used')).toBeTruthy();
  expect(within(dependencies).getByText('Provided by OAW').parentElement?.textContent).toContain('google-adk 2.8.0');
  expect(within(dependencies).getByText('Kept when this Pack is uninstalled.')).toBeTruthy();
  expect(within(dependencies).getByText('Shared Pack libraries').parentElement?.textContent).toBe('Shared Pack libraries · pymatgen');
  expect(within(dependencies).getByText('Removed when no Pack or running process needs them.')).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Uninstall Pack' })));
  expect(worldApi.getPackUninstallPlan).toHaveBeenCalledWith('distribution.alpha');
  expect(screen.queryByText('Retained version')).toBeNull();
});

it('shows a local installation plan and prevents installing with unresolved blockers', async () => {
  useCardLibrary.setState({ snapshot: snapshot(), open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  vi.spyOn(worldApi, 'inspectPack').mockResolvedValue({ manifest: installedContentPack, sha256: 'digest', plan: {
    pack_id: 'local.research', name: 'Research kit', version: '1.0.0', source: 'archive', restart_required: true,
    dependencies: [], environments: [], warnings: [], blockers: ['A required engine is unsupported'],
    backend_libraries: [{ name: 'litellm', version: '1.81.0', management: 'host-provided' }],
  } });
  const install = vi.spyOn(worldApi, 'installPack');
  render(<CardLibrary />);
  const file = new File(['zip'], 'research.oawpack');
  vi.spyOn(file, 'slice').mockReturnValue({ arrayBuffer: async () => new Uint8Array([0x50, 0x4b, 3, 4]).buffer } as Blob);
  await act(async () => fireEvent.change(screen.getByLabelText('Pack file'), { target: { files: [file] } }));
  const plan = screen.getByRole('region', { name: 'Installation plan' });
  expect(within(plan).getByText('Provided by OAW').parentElement?.textContent).toContain('litellm 1.81.0');
  expect(within(plan).getByText('Kept when this Pack is uninstalled.')).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('A required engine is unsupported');
  expect((screen.getByRole('button', { name: 'Install Pack' }) as HTMLButtonElement).disabled).toBe(true);
  expect(install).not.toHaveBeenCalled();
});

it('shows uninstall resource ownership and blocks removal until dependencies permit it', async () => {
  const state = packSnapshot();
  const id = state.packs.alpha.definition.plugin_id;
  vi.mocked(worldApi.getInstalledPacks).mockResolvedValue({ restart_required: false, versions: [{ ...installedContentPack, id, loaded: true }] });
  vi.mocked(worldApi.getPackUninstallPlan).mockResolvedValue({ id, owner: id, can_uninstall: false, blockers: ['Another Pack requires this renderer'],
    source: 'installed', restart_required: true, resources: [{ id: 'shared-science', kind: 'python', action: 'retain', reason: 'Used by XRD' }] });
  const remove = vi.spyOn(worldApi, 'managePack');
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  await act(async () => { render(<CardLibrary />); });
  fireEvent.click(screen.getByRole('button', { name: 'View pack Alpha pack' }));
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Uninstall Pack' })));
  expect(screen.getByRole('alert').textContent).toContain('Another Pack requires this renderer');
  expect(screen.getByText('Used by XRD')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Confirm uninstall' }) as HTMLButtonElement).disabled).toBe(true);
  expect(remove).not.toHaveBeenCalled();
});

it('restores an unenrolled source Pack using its distribution identity', async () => {
  const state = packSnapshot();
  const source = { id: 'distribution.alpha', owner: state.packs.alpha.definition.plugin_id, name: 'Alpha pack', version: '1.0.0',
    source: 'development' as const, enabled: false, loaded: false, enrolled: false,
    environments: [], backend_requirements: [], frontend_dependencies: [], warnings: [] };
  vi.mocked(worldApi.getInstalledPacks).mockResolvedValue({ restart_required: false, versions: [], inventory: [source] });
  const restore = vi.spyOn(worldApi, 'managePack').mockResolvedValue({ restart_required: true, versions: [], inventory: [{ ...source, enrolled: true }] });
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  await act(async () => { render(<CardLibrary />); });
  expect(screen.queryByRole('article', { name: 'Alpha pack' })).toBeNull();
  const uninstalled = screen.getByText('Uninstalled Packs (2)').closest('details')!;
  expect(uninstalled.open).toBe(false);
  uninstalled.open = true;
  fireEvent.click(screen.getByRole('button', { name: 'View pack Alpha pack' }));
  expect(screen.queryByRole('button', { name: 'Uninstall Pack' })).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Restore Pack' })));
  expect(restore).toHaveBeenCalledWith('distribution.alpha/restore', 'POST', undefined);
});

it('removes an orphaned Pack entry while retaining collected cards and surfaces failures', async () => {
  const state = packSnapshot();
  const owner = state.packs.alpha.definition.plugin_id;
  state.plugins[owner].installed = false;
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  const saved = structuredClone(state);
  saved.revision++;
  saved.packs.alpha.dismissed = true;
  const remove = vi.spyOn(worldApi, 'editCardLibrary').mockRejectedValueOnce(new Error('Inventory update failed')).mockResolvedValueOnce(saved);
  await act(async () => { render(<CardLibrary />); });
  fireEvent.click(screen.getByRole('button', { name: 'View pack Alpha pack' }));
  expect(screen.queryByRole('button', { name: 'Uninstall Pack' })).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Remove Pack' })));
  expect(screen.getByRole('alert').textContent).toContain('Inventory update failed');
  expect(screen.getByRole('region', { name: 'Pack details' })).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Remove Pack' })));
  expect(remove).toHaveBeenLastCalledWith({ action: 'dismiss_pack', id: 'alpha', expected_revision: state.revision });
  expect(screen.queryByRole('article', { name: 'Alpha pack' })).toBeNull();
  expect(screen.getByRole('article', { name: 'Beta pack' })).toBeTruthy();
  expect(useCardLibrary.getState().snapshot?.collection).toEqual(state.collection);
});

it('keeps loaded local packs in the regular inventory without duplicates and manages them inside', async () => {
  const state = packSnapshot();
  const id = state.packs.alpha.definition.plugin_id;
  const item = { ...installedContentPack, id, loaded: true };
  vi.mocked(worldApi.getInstalledPacks).mockResolvedValue({ restart_required: false, versions: [item] });
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  useWorldStore.setState({ legions: [] });
  await act(async () => { render(<CardLibrary />); });
  expect(screen.getAllByRole('article')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'View pack Alpha pack' }));
  expect(screen.getByRole('button', { name: 'View cards' })).toBeTruthy();
  const manage = vi.spyOn(worldApi, 'managePack').mockRejectedValueOnce(new Error('Pack is in use'));
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Uninstall Pack' })));
  expect(manage).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Confirm uninstall' })));
  expect(screen.getByRole('alert').textContent).toContain('Pack is in use');
  manage.mockResolvedValueOnce({ restart_required: true, versions: [{ ...item, selected: false }] });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Confirm uninstall' })));
  expect(manage).toHaveBeenLastCalledWith(encodeURIComponent(id), 'DELETE', undefined);
  expect(screen.getByText('Pack removed. Restart OAW to finish uninstalling.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Uninstall Pack' })).toBeNull();
  expect(screen.queryAllByRole('article')).toHaveLength(0);
});
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

it('scopes failures to their owner and repairs source environments from secondary management', async () => {
  const state = packSnapshot();
  const owner = state.packs.alpha.definition.plugin_id;
  state.packs.beta.definition.plugin_id = 'other.owner';
  state.plugins['other.owner'] = { ...state.plugins[owner] };
  state.available_pack_ids = ['beta'];
  const runtime = { id: 'distribution.alpha', owner, name: 'Alpha pack', version: '1.0.0',
    source: 'development' as const, enabled: true, loaded: false,
    environments: [], backend_requirements: [], frontend_dependencies: [], warnings: [] };
  const failure = { id: runtime.id, owner, source: 'source', state: 'unavailable' as const,
    phase: 'activation', error: 'Unique PDF dependency conflict', retry: 'repair_and_restart' as const };
  vi.mocked(worldApi.getInstalledPacks).mockResolvedValue({ restart_required: false, versions: [],
    inventory: [runtime, { ...runtime, id: 'distribution.beta', owner: 'other.owner', name: 'Beta pack' }], failures: [failure] });
  const repair = vi.spyOn(worldApi, 'managePack').mockResolvedValue({ restart_required: true, versions: [], inventory: [runtime] });
  useCardLibrary.setState({ snapshot: state, open: true, tab: 'packs', refresh: async () => {} });
  await act(async () => { render(<CardLibrary />); });
  const alpha = screen.getByRole('article', { name: 'Alpha pack' });
  expect(alpha.dataset.packIssue).toBe('error');
  expect(within(alpha).getByText('Environment failed')).toBeTruthy();
  expect(screen.queryByText(failure.error)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'View pack Beta pack' }));
  expect(screen.queryByText(failure.error)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Back to packs' }));
  fireEvent.click(screen.getByRole('button', { name: 'View cards in Alpha pack' }));
  expect(screen.getByRole('alert').textContent).toBe(failure.error);
  fireEvent.change(screen.getByLabelText('Source pack'), { target: { value: 'pack:beta' } });
  expect(screen.queryByText(failure.error)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /^Packs/ }));
  fireEvent.click(screen.getByRole('button', { name: 'View pack Alpha pack' }));
  expect(screen.getByRole('alert').textContent).toBe(failure.error);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Retry environment preparation' })));
  expect(repair).toHaveBeenCalledWith('distribution.alpha/restore', 'POST', undefined);
  fireEvent.click(screen.getByRole('button', { name: 'View cards' }));
  expect((screen.getByLabelText('Source pack') as HTMLSelectElement).value).toBe('pack:alpha');
});

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  useCardLibrary.setState({ snapshot: null, busy: false, open: false, tab: "packs", inspectedEntry: null, error: "", refresh: originalRefresh });
});

it("keeps the collected finish across library, detail and deck rerenders with normal legacy cards", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener() {}, removeEventListener() {} })));
  const state = snapshot();
  state.collection["card.1"].finish = "rainbow";
  state.decks[0].entries = [{ kind: "node", id: "card.1" }];
  useCardLibrary.setState({ snapshot: state, open: true, tab: "cards", refresh: async () => {} });
  useWorldStore.setState({ catalog: TEST_CATALOG, legions: [] });
  const { container } = render(<><CardLibrary /><ComponentPalette /></>);
  const stock = () => container.querySelector('[data-library-card="card.1"] .card-stock')!;
  const deck = () => container.querySelector('[data-palette-card="card.1"] .card-stock')!;
  expect(stock().getAttribute("data-finish")).toBe("rainbow");
  expect(stock().querySelector(".card-finish-layer")?.getAttribute("data-quality")).toBe("thumbnail");
  expect(deck().getAttribute("data-finish")).toBe("rainbow");
  const normal = container.querySelector('[data-library-card="card.2"] .card-stock')!;
  expect(normal.getAttribute("data-finish")).toBe("normal");
  expect(normal.querySelector(".card-finish-layer")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Inspect Tool 01" }));
  expect(screen.getByText("Rainbow", { selector: ".library-finish-metadata" })).toBeTruthy();
  expect(container.querySelector('.library-card-preview .card-finish-layer')?.getAttribute("data-quality")).toBe("showcase");
  act(() => useCardLibrary.setState({ snapshot: { ...structuredClone(state), revision: state.revision + 1 } }));
  expect(stock().getAttribute("data-finish")).toBe("rainbow");
  expect(deck().getAttribute("data-finish")).toBe("rainbow");
  expect(screen.getByText("Rainbow", { selector: ".library-finish-metadata" })).toBeTruthy();
});

it("shares preset covers, failed-cover fallback and replacement artwork across collection, detail and deck", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const state = packSnapshot();
  const artwork = '/api/plugins/xrd/assets/workbench-cover.svg';
  state.packs.alpha.definition.artwork_url = artwork;
  state.preset_pack_ids = { workbench: ['alpha'] };
  state.decks[0].entries = [{ kind: 'legion', id: 'workbench' }];
  openCards(state);
  act(() => useWorldStore.setState({ legions: [{ id: 'workbench', name: 'XRD workbench', preset: true,
    node_count: 2, edge_count: 1, bounds: { width: 600, height: 400 }, node_types: ['card.1'],
    plugin_ids: [TEST_CATALOG.plugins[0].id], compatible: true, issues: [], revision: 1 }] }));
  fireEvent.change(screen.getByLabelText('Source pack'), { target: { value: 'pack:alpha' } });
  fireEvent.click(screen.getByRole('button', { name: 'Inspect XRD workbench' }));
  render(<ComponentPalette />);
  const cover = within(screen.getByRole('complementary', { name: 'Card details' })).getByRole('img', { name: 'XRD workbench' });
  expect(cover.getAttribute('src')).toBe(artwork);
  expect(cover.closest('.library-detail-stage')).not.toBeNull();
  const covers = screen.getAllByRole('img', { name: 'XRD workbench' });
  expect(covers).toHaveLength(3);
  for (const image of covers) {
    expect(image.getAttribute('src')).toBe(artwork);
    expect(image.parentElement?.querySelector('.card-face')).toBeNull();
    fireEvent.error(image);
  }
  const faces = document.querySelectorAll('[data-library-card="workbench"] .card-face, .library-card-preview .card-face, [data-palette-card="workbench"] .card-face');
  expect(faces).toHaveLength(3);
  for (const face of faces) {
    expect(face.querySelector('.lucide-layers')).toBeTruthy();
    expect(face.innerHTML).toBe(faces[0].innerHTML);
  }
  const replacement = structuredClone(state);
  replacement.packs.alpha.definition.artwork_url = '/replacement-cover.svg';
  act(() => useCardLibrary.setState({ snapshot: replacement }));
  const replaced = screen.getAllByRole('img', { name: 'XRD workbench' });
  expect(replaced).toHaveLength(3);
  for (const image of replaced) expect(image.getAttribute('src')).toBe('/replacement-cover.svg');
});

it("keeps complete source groups together, including packs larger than the former page limit", () => {
  const state = snapshot();
  const ids = Object.keys(state.collection);
  for (const [id, name, cards] of [["alpha", "Alpha pack", ids.slice(0, 28)], ["beta", "Beta pack", ids.slice(28)]] as const) {
    state.packs[id] = { definition: { id, name, plugin_id: TEST_CATALOG.plugins[0].id, cards: [...cards], description: "Research tools" }, owned: true, opened: true, opened_at: null };
    for (const card of cards) state.collection[card].source_pack_ids = [id];
  }
  openCards(state);
  for (const [name, count] of [["Alpha pack", 28], ["Beta pack", 47]] as const) {
    const group = screen.getByRole("heading", { name }).closest("details")!;
    expect(group.open).toBe(false);
    group.open = true;
    expect(within(group).getByText(`${count} cards`)).toBeTruthy();
    expect(within(group).getAllByRole("button", { name: /^Inspect Tool/ })).toHaveLength(count);
  }
  expect(screen.queryByLabelText("Next cards")).toBeNull();
  fireEvent.change(screen.getByLabelText("Source pack"), { target: { value: "pack:beta" } });
  expect(screen.getAllByRole("button", { name: /^Inspect Tool/ })).toHaveLength(47);
  fireEvent.change(screen.getByLabelText("Search cards"), { target: { value: "Tool 75" } });
  expect(screen.getAllByRole("button", { name: /^Inspect Tool/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Inspect Tool 75" }));
  expect(screen.getByRole("complementary", { name: "Card details" }).textContent).toContain("Tool 75");
  fireEvent.change(screen.getByLabelText("Card category"), { target: { value: "Even" } });
  expect(screen.getByText("No matching cards")).toBeTruthy();
});

function packSnapshot() {
  const state = snapshot();
  state.card_definitions = Object.fromEntries(Object.entries(state.card_definitions).slice(0, 4));
  state.collection = Object.fromEntries(Object.entries(state.collection).slice(0, 4));
  const pluginId = TEST_CATALOG.plugins[0].id;
  state.plugins = { [pluginId]: { descriptor: TEST_CATALOG.plugins[0], installed: true, enabled: true } };
  for (const [id, name, cards] of [["alpha", "Alpha pack", ["card.1", "card.2"]], ["beta", "Beta pack", ["card.3", "card.4"]]] as const) {
    state.packs[id] = { definition: { id, name, plugin_id: pluginId, cards: [...cards], description: "Research tools" }, owned: true, opened: true, opened_at: null };
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

it("opens unavailable Legion diagnostics directly from its deck card", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const state = packSnapshot();
  state.decks[0].entries = [{ kind: "legion", id: "broken" }];
  useCardLibrary.setState({ snapshot: state, open: false, refresh: async () => {} });
  useWorldStore.setState({ catalog: TEST_CATALOG, legions: [{ id: "broken", name: "Broken formation",
    node_count: 1, edge_count: 0, bounds: { width: 100, height: 100 }, node_types: ["missing.card"],
    plugin_ids: ["missing.plugin"], compatible: false, issues: ["missing.plugin is disabled", "Template version is unsupported"], revision: 1 }] });
  render(<><ComponentPalette /><CardLibrary /></>);
  fireEvent.click(screen.getByLabelText("Broken formation unavailable"));
  const details = screen.getByRole("region", { name: "Dependency details" });
  expect(within(details).getByText("missing.plugin is disabled")).toBeTruthy();
  expect(within(details).getByText("Template version is unsupported")).toBeTruthy();
  expect(within(details).getByText("missing.card", { selector: "code" })).toBeTruthy();
});

it("groups by collected pack provenance, deduplicates shared cards and filters by every source", () => {
  const state = packSnapshot();
  state.collection["card.1"].source_pack_ids.push("beta");
  openCards(state);
  expect(screen.getByRole("heading", { name: "Alpha pack" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Beta pack" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Alpha pack" }).closest("details")?.hasAttribute("open")).toBe(false);
  for (const name of ["Alpha pack", "Beta pack"]) {
    const summary = screen.getByRole("heading", { name }).closest("summary")!;
    expect(summary.querySelector("small")).toBeNull();
    fireEvent.click(summary);
  }
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
  expect(inspect.getAttribute('aria-description')).toBe("Review code with the toolbox's shared conventions.");
  expect(screen.getByRole("button", { name: "Inspect Simulation toolbox · Skill" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add Review toolbox · Skill to deck" })).toBeNull();
  fireEvent.click(inspect);
  const detail = within(screen.getByRole("complementary", { name: "Card details" }));
  expect(detail.getByText("Review code with the toolbox's shared conventions.")).toBeTruthy();
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
  const inspect = screen.getByRole("button", { name: "Inspect Review toolbox" });
  expect(inspect.getAttribute('aria-description')).toBe(state.card_definitions["card.1"].description);
  fireEvent.click(inspect);
  expect(within(screen.getByRole("complementary", { name: "Card details" })).getByText(state.card_definitions["card.1"].description)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add Review toolbox to deck" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: /Show internal cards/ }));
  fireEvent.click(screen.getByRole("button", { name: "Inspect Review toolbox · Skill" }));
  const detail = within(screen.getByRole("complementary", { name: "Card details" }));
  expect(detail.getByText(/Install or enable its Pack/)).toBeTruthy();
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
  expect(controls.getByRole("button", { name: "Disable Pack" })).toBeTruthy();
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

it("adds to the visible deck and accepts library pointer drags on the hand and other tabs", async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal('PointerEvent', class extends MouseEvent { pointerId = 1; isPrimary = true; });
  let hit: Element;
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => hit });
  const state = packSnapshot();
  state.decks.push({ id: 'research', name: 'Research', icon: 'layers', entries: [] });
  const edit = vi.spyOn(worldApi, 'editCardLibrary').mockImplementation(async request => {
    const current = useCardLibrary.getState().snapshot!;
    return { ...current, revision: current.revision + 1,
      active_deck_id: request.action === 'move_entry' ? request.id! : current.active_deck_id,
      decks: current.decks.map(deck => deck.id === request.id ? { ...deck,
        entries: request.entry && !deck.entries.some(entry => entry.id === request.entry!.id)
          ? [...deck.entries, request.entry] : request.entries ?? deck.entries } : deck) };
  });
  openCards(state);
  render(<ComponentPalette />);
  expect(screen.queryByRole('complementary', { name: 'Deck destinations' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Source pack'), { target: { value: 'pack:alpha' } });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add Review toolbox to deck' })));
  expect(edit).toHaveBeenCalledWith(expect.objectContaining({ action: 'move_entry', id: 'test', entry: { kind: 'node', id: 'card.1' } }));
  const source = screen.getByRole('button', { name: 'Inspect Review toolbox' });
  source.setPointerCapture = vi.fn(); source.hasPointerCapture = () => false;
  const drag = async (target: Element) => {
    fireEvent.pointerDown(source, { button: 0, buttons: 1, clientX: 10, clientY: 10 });
    hit = target;
    fireEvent.pointerMove(window, { buttons: 1, clientX: 40, clientY: 40 });
    await act(async () => fireEvent.pointerUp(window, { button: 0, clientX: 40, clientY: 40 }));
  };
  await drag(screen.getByRole('tab', { name: /Research/ }));
  expect(useCardLibrary.getState().snapshot!.active_deck_id).toBe('research');
  expect(useCardLibrary.getState().snapshot!.decks.every(deck => deck.entries.length === 1)).toBe(true);
  await drag(screen.getByRole('tabpanel'));
  expect(useCardLibrary.getState().snapshot!.decks[1].entries).toHaveLength(1);
  const calls = edit.mock.calls.length;
  await drag(screen.getByRole('region', { name: 'Discard card' }));
  expect(edit).toHaveBeenCalledTimes(calls);
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(useCardLibrary.getState().open).toBe(false);
});
