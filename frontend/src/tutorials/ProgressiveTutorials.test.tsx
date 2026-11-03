// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useLocale } from '../i18n';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { buildCardDraft } from '../state/helpers';
import { useWorldStore } from '../state/worldStore';
import { useTutorialStore } from '../onboarding/controller';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useCardLibrary, type LibrarySnapshot } from '../state/cardLibrary';
import { ProgressiveTutorials, TutorialMarkdown } from './ProgressiveTutorials';
import { useProgressiveTutorials } from './store';
import { tutorialOwner } from './catalog';
import { useInteractionEnvironment } from '../interaction/environment';

const definition = { id: 'intro', title: 'Text tutorial', summary: 'Write and share', document: '## Reference\nRead me offline.',
  steps: [{ id: 'edit', title: 'Write a note', body: 'Open the text card.' }, { id: 'share', title: 'Share the note', body: 'Connect an Agent.' }] };
const catalog = { ...TEST_CATALOG, node_types: TEST_CATALOG.node_types.map(card => ({ ...card, tutorials: card.id === 'text' ? [definition] : [] })),
  packs: [{ id: 'example.pack', plugin_id: 'example', name: 'Example Pack', description: '', cards: ['text'], tutorials: [{ ...definition, id: 'pack', title: 'Pack tutorial' }] }] };
const card = { ...buildCardDraft('text', { x: 0, y: 0 }), id: 'text-1' };
beforeEach(() => {
  useLocale.setState({ locale: 'en' });
  useProgressiveTutorials.setState({ enabled: true, progress: {}, entries: [], encountered: [], current: undefined, libraryOpen: false, view: 'hint' });
  useTutorialStore.setState({ view: 'hidden', quickStart: undefined });
  useWorldStore.setState({ catalog, cards: [], selectedCardIds: [], undoStack: [] });
  useNodeSurfaceStore.setState({ surfaceLevels: {}, dragging: false, connectingNodeId: undefined });
  useCardLibrary.setState({ snapshot: null, inspectedEntry: null });
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  useInteractionEnvironment.setState({ compact: false, height: 0, top: 0 });
});

it('keeps compact automatic hints above the live Deck and defers them when the gap is too small', () => {
  useInteractionEnvironment.setState({ compact: true, height: 800, top: 0 });
  vi.stubGlobal('innerHeight', 800);
  let reposition = () => {};
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { reposition = callback; }
    observe() {} disconnect() {}
  });
  render(<><aside className="component-palette" data-testid="deck" /><aside className="map-tools" data-testid="tools" /><ProgressiveTutorials /></>);
  let top = 600;
  vi.spyOn(screen.getByTestId('deck'), 'getBoundingClientRect').mockImplementation(() => ({ top } as DOMRect));
  vi.spyOn(screen.getByTestId('tools'), 'getBoundingClientRect').mockReturnValue({ bottom: 210 } as DOMRect);
  act(() => useProgressiveTutorials.getState().encounter([tutorialOwner('card', 'text')]));
  const panel = document.querySelector<HTMLElement>('.progressive-tutorial')!;
  expect(panel.style.bottom).toBe('208px');
  expect(panel.hidden).toBe(false);
  top = 370;
  act(reposition);
  expect(panel.hidden).toBe(true);
  expect(useProgressiveTutorials.getState().current).toBeTruthy();
  top = 740;
  act(reposition);
  expect(panel.hidden).toBe(false);
  expect(panel.style.bottom).toBe('68px');
});

it('does not prompt on background loading; an intentional encounter can be read and dismissed', () => {
  render(<ProgressiveTutorials />);
  act(() => useWorldStore.setState({ cards: [card] }));
  expect(screen.queryByText('Text tutorial')).toBeNull();
  act(() => useWorldStore.getState().selectCards([card.id]));
  fireEvent.click(screen.getByText('View tutorial'));
  expect(screen.getByText('Write a note')).toBeTruthy();
  fireEvent.click(screen.getByText('Continue'));
  expect(screen.getByText('Share the note')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Dismiss this tutorial'));
  expect(screen.queryByText('Text tutorial')).toBeNull();
  expect(screen.getByText('Pack tutorial')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Dismiss this tutorial'));
  act(() => { useWorldStore.getState().selectCards([]); useWorldStore.getState().selectCards([card.id]); });
  expect(screen.queryByText('View tutorial')).toBeNull();
});

it('offers successfully placed cards and ignores encounters during first-use onboarding', () => {
  render(<ProgressiveTutorials />);
  act(() => useTutorialStore.setState({ view: 'active' }));
  act(() => useWorldStore.setState({ cards: [card], undoStack: [{ id: 1, label: 'Place', kind: 'card-created', cards: [card] }] }));
  expect(useProgressiveTutorials.getState().current).toBeUndefined();
  act(() => useTutorialStore.setState({ view: 'hidden' }));
  act(() => useWorldStore.setState({ undoStack: [{ id: 2, label: 'Place', kind: 'card-created', cards: [card] }] }));
  expect(screen.getByText('Text tutorial')).toBeTruthy();
});

it('offers packs only after opening, not from loading the collection', () => {
  render(<ProgressiveTutorials />);
  const snapshot = { packs: { 'example.pack': { opened: false } } } as unknown as LibrarySnapshot;
  act(() => useCardLibrary.setState({ snapshot }));
  expect(useProgressiveTutorials.getState().current).toBeUndefined();
  act(() => useCardLibrary.setState({ snapshot: { ...snapshot, packs: { 'example.pack': { ...snapshot.packs['example.pack'], opened: true } } } }));
  expect(screen.getByText('Pack tutorial')).toBeTruthy();
});

it('can disable hints, search all available content, and open documentation offline', () => {
  render(<ProgressiveTutorials />);
  act(() => useProgressiveTutorials.getState().showLibrary());
  fireEvent.click(screen.getByRole('checkbox'));
  expect(useProgressiveTutorials.getState().enabled).toBe(false);
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Text tutorial' } });
  expect(screen.queryByText('Pack tutorial')).toBeNull();
  fireEvent.click(screen.getByText('Read documentation'));
  expect(screen.getByRole('heading', { name: 'Reference' })).toBeTruthy();
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('temporarily hides hints during gestures and removes content when the plugin disappears', () => {
  render(<ProgressiveTutorials />);
  act(() => useProgressiveTutorials.getState().encounter([tutorialOwner('card', 'text')]));
  act(() => useNodeSurfaceStore.setState({ dragging: true }));
  expect(screen.queryByText('Text tutorial')).toBeNull();
  act(() => useNodeSurfaceStore.setState({ dragging: false }));
  expect(screen.getByText('Text tutorial')).toBeTruthy();
  act(() => useWorldStore.setState({ catalog: { ...catalog, node_types: [], packs: [] } }));
  expect(screen.queryByText('Text tutorial')).toBeNull();
});

it('renders Markdown without executable HTML, unsafe links or remote image requests', () => {
  const { container } = render(<TutorialMarkdown>{'<script>alert(1)</script>\n\n[unsafe](javascript:alert) ![image](https://example.com/track) [docs](https://example.com/docs)'}</TutorialMarkdown>);
  expect(container.querySelector('script, img')).toBeNull();
  expect(screen.getAllByRole('link')).toHaveLength(1);
  expect(screen.getByRole('link').getAttribute('rel')).toBe('noopener noreferrer');
});


it('ignores initial surface hydration but observes deliberate expansion', () => {
  render(<ProgressiveTutorials />);
  act(() => useWorldStore.setState({ cards: [card] }));
  act(() => useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: 'preview' } }));
  expect(screen.queryByText('Text tutorial')).toBeNull();
  act(() => useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: 'inspector' } }));
  expect(screen.getByText('Text tutorial')).toBeTruthy();
});
