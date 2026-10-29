// @vitest-environment jsdom
import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { LegionWorkspace, WorkspaceWindow } from './LegionWorkspace';
import { WorkspaceSection } from '../workspace/WorkspaceSection';
import { useWorldStore } from '../state/worldStore';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { useLocale } from '../i18n';
import type { WorldCard } from '../types/world';
import { useWorkspaceNavigation, type WorkspaceNavigation } from '../workspace/WorkspaceNavigation';

let navigation: WorkspaceNavigation | undefined;

vi.mock('../cards/CardFrame', () => ({ CardContent: () => null }));
vi.mock('../cards/CardFinishLayer', () => ({ CardFinishLayer: () => null }));
vi.mock('../components/CatalogIcon', () => ({ CatalogIcon: () => null }));
vi.mock('../shell/PreferenceButtons', () => ({ AppearanceButtons: () => null, SettingsButton: () => null }));
vi.mock('../deployment/PublishApplication', () => ({ PublishApplication: () => null }));
vi.mock('../cards/NodeWorkspace', () => ({ WorkspaceContent: ({ card }: { card: WorldCard }) => <ConditionalCard card={card} /> }));

function ConditionalCard({ card }: { card: WorldCard }) {
  navigation = useWorkspaceNavigation();
  const [visible, setVisible] = useState(!card.config.hide_graph);
  const [width, setWidth] = useState(420);
  if (card.id !== 'owner') return <p>Neighbor content</p>;
  return <>
    <button onClick={() => setVisible(value => !value)}>Toggle graph</button>
    <button onClick={() => setWidth(520)}>Change declared size</button>
    {visible && <WorkspaceSection id="graph" title="Graph" layout={{ min_width: width, min_height: 260 }}><p>Graph content</p><input aria-label="Retained draft" defaultValue="" /></WorkspaceSection>}
  </>;
}

const initialStore = useWorldStore.getState();
afterEach(() => { cleanup(); useWorldStore.setState(initialStore, true); useLegionWorkspace.setState({ activeId: undefined }); vi.restoreAllMocks(); });

test('conditional sections retain geometry while missing, accept explicit new hints and forget removed owners', () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  useLocale.setState({ locale: 'en' });
  const makeCard = (id: string): WorldCard => ({ id, type: 'sandbox', name: id, parent_id: 'team',
    position: { x: 0, y: 0 }, size: { width: 300, height: 200 }, expanded: false, status: 'available', config: {} });
  const owner = makeCard('owner'), neighbor = makeCard('neighbor');
  const layout = { version: 2, hidden_sections: [], root: { kind: 'split', axis: 'horizontal', ratio: .35,
    first: { kind: 'pane', view: { card_id: 'owner' } },
    second: { kind: 'split', axis: 'horizontal', ratio: .6,
      first: { kind: 'pane', view: { card_id: 'owner', section_id: 'graph' } },
      second: { kind: 'pane', view: { card_id: 'neighbor' } } } } };
  const team: WorldCard = { ...makeCard('team'), type: 'legion', parent_id: null, config: { workspace_layout: layout } };
  const updateCard = vi.fn();
  useWorldStore.setState({ cards: [team, owner, neighbor], catalog: { plugins: [], packs: [], relationships: [], node_types: [] }, updateCard });
  const view = render(<WorkspaceWindow card={team} />);
  const root = view.container.querySelector<HTMLElement>('.legion-layout-root')!;
  expect(root.style.minWidth).toBe('908px');
  const tracks = view.container.querySelectorAll<HTMLElement>('.legion-layout-split')[1].style.gridTemplateColumns;
  fireEvent.click(screen.getByRole('button', { name: 'Toggle graph' }));
  expect(screen.getByText('This section is currently unavailable. Its layout is preserved.')).toBeTruthy();
  expect(root.style.minWidth).toBe('908px');
  expect(view.container.querySelectorAll<HTMLElement>('.legion-layout-split')[1].style.gridTemplateColumns).toBe(tracks);
  fireEvent.click(screen.getByRole('button', { name: 'Toggle graph' }));
  expect(root.style.minWidth).toBe('908px');
  fireEvent.click(screen.getByRole('button', { name: 'Change declared size' }));
  expect(root.style.minWidth).toBe('1008px');
  expect(useWorldStore.getState().cards[0].config.workspace_layout).toBe(layout);
  expect(updateCard).not.toHaveBeenCalled();
  act(() => useWorldStore.setState({ cards: [team, neighbor] }));
  act(() => useWorldStore.setState({ cards: [team, { ...owner, config: { hide_graph: true } }, neighbor] }));
  expect(root.style.minWidth).toBe('728px');
});

test('operation activation reveals existing tabs without saving geometry or resetting their owner', () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  useLocale.setState({ locale: 'en' });
  const makeCard = (id: string): WorldCard => ({ id, type: 'sandbox', name: id, parent_id: 'team',
    position: { x: 0, y: 0 }, size: { width: 300, height: 200 }, expanded: false, status: 'available', config: {} });
  const layout = { version: 2, hidden_sections: [], root: { kind: 'split', axis: 'horizontal', ratio: .35,
    first: { kind: 'pane', view: { card_id: 'owner' } }, second: { kind: 'tabs',
      views: [{ card_id: 'neighbor' }, { card_id: 'owner', section_id: 'graph' }], active_view: { card_id: 'neighbor' } } } };
  const team: WorldCard = { ...makeCard('team'), type: 'legion', parent_id: null, config: { workspace_layout: layout } };
  const updateCard = vi.fn();
  useWorldStore.setState({ cards: [team, makeCard('owner'), makeCard('neighbor'), makeCard('drawer')], catalog: { plugins: [], packs: [], relationships: [], node_types: [] }, updateCard });
  render(<WorkspaceWindow card={team} />);
  act(() => expect(navigation?.activate({ card_id: 'owner', section_id: 'graph' })).toBe(true));
  expect(screen.getByRole('tab', { name: 'owner · Graph' }).getAttribute('aria-selected')).toBe('true');
  fireEvent.change(screen.getByRole('textbox', { name: 'Retained draft' }), { target: { value: 'kept' } });
  fireEvent.click(screen.getByRole('tab', { name: 'neighbor' }));
  act(() => navigation?.activate({ card_id: 'owner', section_id: 'graph' }));
  expect((screen.getByRole('textbox', { name: 'Retained draft' }) as HTMLInputElement).value).toBe('kept');
  expect(updateCard).not.toHaveBeenCalled();
  expect(useWorldStore.getState().cards[0].config.workspace_layout).toBe(layout);
  act(() => expect(navigation?.activate({ card_id: 'outside' })).toBe(false));
  act(() => expect(navigation?.activate({ card_id: 'owner', section_id: 'unknown' })).toBe(false));
  act(() => expect(navigation?.activate({ card_id: 'drawer' })).toBe(true));
  expect(screen.getByRole('button', { name: 'drawer' }).getAttribute('aria-expanded')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Edit layout' }));
  act(() => expect(navigation?.activate({ card_id: 'owner', section_id: 'graph' })).toBe(false));
});

test('switching protects unsaved layouts and only switches after a successful save', async () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  useLocale.setState({ locale: 'en' });
  const member = { id: 'neighbor', name: 'Neighbor', type: 'sandbox', parent_id: 'team', config: {} } as WorldCard;
  const team: WorldCard = { id: 'team', name: 'Team', type: 'legion', position: { x: 0, y: 0 }, size: { width: 800, height: 550 }, expanded: false, status: 'available', config: { workspace_layout: { version: 2, root: { kind: 'pane', view: { card_id: member.id } } } } };
  const other = { ...team, id: 'operations', name: 'Operations', config: {} };
  const updateCard = vi.fn().mockRejectedValueOnce(new Error('Save failed')).mockImplementation(async (id, patch) => {
    useWorldStore.setState(state => ({ cards: state.cards.map(card => card.id === id ? { ...card, config: { ...card.config, ...patch.config } } : card) }));
  });
  useWorldStore.setState({ cards: [team, member, other], catalog: { plugins: [], packs: [], relationships: [], node_types: [] }, updateCard });
  useLegionWorkspace.setState({ activeId: team.id });
  render(<LegionWorkspace />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit layout' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove Neighbor from layout' }));
  fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: 'Operations' }));
  expect(useLegionWorkspace.getState().activeId).toBe('team');
  fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: 'Operations' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save and switch' }));
  await screen.findByText('Save failed');
  expect(useLegionWorkspace.getState().activeId).toBe('team');
  fireEvent.click(screen.getByRole('button', { name: 'Save and switch' }));
  await waitFor(() => expect(useLegionWorkspace.getState().activeId).toBe('operations'));
  fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'Home' });
  fireEvent.click(screen.getByRole('menuitemradio', { name: 'Team' }));
  expect(useLegionWorkspace.getState().activeId).toBe('team');
});
