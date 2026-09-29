// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { CardStaticPreview } from './CardStaticPreview';
import { buildCardDraft } from '../state/helpers';
import { surfaceDraftKey, useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { WorldCard } from '../types/world';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useNodeSurfaceStore.setState({ drafts: {} }); });

it('shows real bounded text and image content without fetching documents or mounting editors', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const card: WorldCard = { id: 'document', ...buildCardDraft('text', { x: 0, y: 0 }) };
  card.config = { content: `Saved content ${'long '.repeat(1000)}`, filename: 'notes.md' };
  const view = render(<CardStaticPreview card={card} workspace={false} />);
  expect(view.container.textContent).toContain('Saved content');
  expect(view.container.textContent!.length).toBeLessThan(450);
  expect(view.container.querySelector('textarea,input,button,[contenteditable]')).toBeNull();
  view.rerender(<CardStaticPreview card={{ ...card, type: 'image', config: { preview_url: '/thumbnail.png', filename: 'image.png' } }} workspace={false} />);
  expect(view.container.querySelector('img')?.getAttribute('src')).toBe('/thumbnail.png');
  expect(fetch).not.toHaveBeenCalled();
});

it('reflects sandbox panes, draft commands and the settings tab using only existing model data', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const card: WorldCard = { id: 'sandbox-proxy', ...buildCardDraft('sandbox', { x: 0, y: 0 }) };
  card.config = { runtime: 'local', workspace_path: '/work', workspace_access: 'read_write', output: ['Completed relaxation.'] };
  const selectionKey = surfaceDraftKey(card.id, 'sandbox-selection', 'local', '/work', 'read_write', false);
  useNodeSurfaceStore.setState({ drafts: { [`sandbox:${card.id}`]: 'python analyze.py', [selectionKey]: JSON.stringify({ label: 'result.txt', path: '/result.txt' }) } });
  const view = render(<CardStaticPreview card={card} workspace />);
  const projection = () => decodeURIComponent(view.container.querySelector('img')!.src.split(',')[1]);
  expect(view.container.querySelectorAll('*')).toHaveLength(1);
  expect(projection()).toContain('Completed relaxation.');
  expect(projection()).toContain('python analyze.py');
  expect(projection()).toContain('result.txt');
  expect(view.container.querySelector('input,textarea,select,button,.sandbox-workspace,.sandbox-settings-page')).toBeNull();
  const snapshotKey = surfaceDraftKey(card.id, 'sandbox-static-preview', 'local', '/work', 'read_write', false);
  useNodeSurfaceStore.getState().setDraft(snapshotKey, JSON.stringify({ path: '/result.txt', text: 'Energy: -12.5 eV' }));
  view.rerender(<CardStaticPreview card={card} workspace />);
  expect(projection()).toContain('Energy: -12.5 eV');
  useNodeSurfaceStore.getState().setDraft(snapshotKey, JSON.stringify({ path: '/other.txt', text: 'Stale file' }));
  view.rerender(<CardStaticPreview card={card} workspace />);
  expect(projection()).not.toContain('Stale file');
  useNodeSurfaceStore.getState().setDraft(`sandbox-tab:${card.id}`, 'settings');
  view.rerender(<CardStaticPreview card={card} workspace />);
  expect(projection()).toContain('/work');
  expect(projection()).toContain('Workspace access');
  expect(projection()).not.toContain('Completed relaxation.');
  expect(fetch).not.toHaveBeenCalled();
});
