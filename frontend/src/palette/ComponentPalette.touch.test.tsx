// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ComponentPalette } from './ComponentPalette';
import { useCardLibrary, type LibrarySnapshot } from '../state/cardLibrary';
import { useWorldStore } from '../state/worldStore';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useInteractionEnvironment } from '../interaction/environment';
import { useLocale } from '../i18n';

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  useLocale.setState({ locale: 'en' });
  useInteractionEnvironment.setState({ touch: true, input: 'touch' });
  const definition = TEST_CATALOG.node_types.find(item => item.id === 'text')!;
  const snapshot: LibrarySnapshot = { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {},
    card_definitions: { text: definition }, collection: {}, available_card_ids: ['text'], available_pack_ids: [],
    active_deck_id: 'deck', decks: [{ id: 'deck', name: 'Deck', icon: 'folder', entries: [{ kind: 'node', id: 'text' }] }] };
  useCardLibrary.setState({ snapshot, busy: false, open: false, error: '', refresh: vi.fn().mockResolvedValue(undefined), edit: vi.fn().mockResolvedValue(null) });
  useWorldStore.setState({ catalog: TEST_CATALOG, legions: [], createCard: vi.fn().mockResolvedValue(undefined) });
});
afterEach(() => { cleanup(); useInteractionEnvironment.setState({ touch: false, input: 'mouse' }); vi.unstubAllGlobals(); });

it('opens and collapses with explicit touch controls, and tapping a card places it once then clears the tray', () => {
  const screen = render(<ComponentPalette />);
  const stage = screen.getByRole('tabpanel', { hidden: true });
  expect(stage.getAttribute('aria-hidden')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Expand card deck' }));
  expect(stage.getAttribute('aria-hidden')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Collapse card deck' }));
  expect(stage.getAttribute('aria-hidden')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: 'Expand card deck' }));
  fireEvent.click(screen.getByRole('button', { name: 'Place text' }));
  expect(useWorldStore.getState().createCard).toHaveBeenCalledOnce();
  expect(useWorldStore.getState().createCard).toHaveBeenCalledWith('text');
  expect(stage.getAttribute('aria-hidden')).toBe('true');
});

it('opens a selected deck without hover and dismisses it on an outside press', () => {
  const screen = render(<ComponentPalette />);
  fireEvent.click(screen.getByRole('tab', { name: /Deck/ }));
  expect(screen.getByRole('button', { name: 'Collapse card deck' }).getAttribute('aria-expanded')).toBe('true');
  fireEvent.pointerDown(document.body);
  expect(screen.getByRole('button', { name: 'Expand card deck' }).getAttribute('aria-expanded')).toBe('false');
});

it('keeps placement disabled while a deck change is pending, then accepts the next tap', () => {
  const screen = render(<ComponentPalette />);
  fireEvent.click(screen.getByRole('button', { name: 'Expand card deck' }));
  const card = screen.getByRole('button', { name: 'Place text' }) as HTMLButtonElement;
  act(() => useCardLibrary.setState({ busy: true }));
  expect(card.disabled).toBe(true);
  fireEvent.click(card);
  expect(useWorldStore.getState().createCard).not.toHaveBeenCalled();
  act(() => useCardLibrary.setState({ busy: false }));
  fireEvent.click(card);
  expect(useWorldStore.getState().createCard).toHaveBeenCalledOnce();
});
