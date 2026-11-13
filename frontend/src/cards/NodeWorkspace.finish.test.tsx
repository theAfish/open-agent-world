// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { WorkspaceSurface } from './NodeWorkspace';
import { AgentCardBody } from './AgentCard';
import { worldApi } from '../api/client';
import { useLocale } from '../i18n';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('preserves finish metadata while keeping the entire workspace matte', () => {
  const card: WorldCard = { id: 'printed-workspace', type: 'custom.workspace', name: 'Research',
    status: 'idle', config: {}, position: { x: 0, y: 0 }, size: { width: 600, height: 420 },
    expanded: true, finish: 'laser' };
  useWorldStore.setState({ cards: [card], catalog: { ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types,
    { ...TEST_CATALOG.node_types[0], id: card.type, label: 'Custom workspace', container: null,
      presentation: { states: ['workspace'], initial: 'workspace', open: 'workspace' } },
  ] } });
  const { container, rerender } = render(<WorkspaceSurface card={card} />);
  const titlebar = container.querySelector('.workspace-titlebar')!;
  expect(titlebar.querySelector('strong')?.textContent).toBe('Research');
  expect(titlebar.textContent).not.toContain('Custom workspace');
  expect(titlebar.querySelector('.card-name-edit')).not.toBeNull();
  expect(titlebar.querySelector('.workspace-window-actions')?.children).toHaveLength(1);
  expect(container.querySelector('.card-finish-layer')).toBeNull();
  rerender(<WorkspaceSurface card={{ ...card }} />);
  expect(container.querySelector('.workspace-titlebar')?.getAttribute('data-finish')).toBe('laser');
  rerender(<WorkspaceSurface card={{ ...card, finish: undefined }} />);
  expect(container.querySelector('.workspace-titlebar')?.getAttribute('data-finish')).toBe('normal');
  expect(container.querySelector('.card-finish-layer')).toBeNull();
});

it('unmounts an open Run history when its enclosing activity panel closes', async () => {
  vi.useFakeTimers();
  useLocale.setState({ locale: 'en' });
  const card: WorldCard = { id: 'agent-history', type: 'agent', name: 'Agent', status: 'running',
    config: {}, position: { x: 0, y: 0 }, size: { width: 600, height: 420 }, expanded: true, ephemeral: true };
  useWorldStore.setState({ cards: [card], catalog: TEST_CATALOG });
  const load = vi.spyOn(worldApi, 'agentRuns').mockResolvedValue([]);
  const view = render(<AgentCardBody card={card} level="workspace" />);
  const outer = view.getByText('Runtime activity').closest('details')!;
  expect(outer.querySelector('details')).toBeNull();
  await act(async () => { outer.open = true; fireEvent(outer, new Event('toggle')); });
  const inner = outer.querySelector('details')!;
  await act(async () => { inner.open = true; fireEvent(inner, new Event('toggle')); });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => { outer.open = false; fireEvent(outer, new Event('toggle')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(load).toHaveBeenCalledTimes(1);
});
