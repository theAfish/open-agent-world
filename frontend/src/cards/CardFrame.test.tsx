// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import type { ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useEquipmentPanel } from '../state/equipment';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { WorldCardNode } from './CardFrame';

vi.mock('./AgentCard', () => ({ AgentCardBody: () => <div>Agent settings</div> }));
vi.mock('./NodeWorkspace', () => ({ WorkspaceSurface: () => <div>Agent workspace</div> }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useEquipmentPanel.setState({ openIds: [] }); });

it('keeps one working backpack across surfaces and mounts inspector actions only while open', () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const card: WorldCard = { id: 'owner', type: 'agent', name: 'Equipment owner', status: 'idle', config: {},
    position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false };
  useWorldStore.setState({ cards: [card], edges: [], events: [], catalog: TEST_CATALOG });
  useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: 'node' }, baseLevels: { [card.id]: 'preview' },
    connectingNodeId: undefined, dragging: false });
  useNodeSurfaceStore.getState().syncCards([card], TEST_CATALOG);
  useEquipmentPanel.setState({ openIds: [] });
  const props = { id: card.id, data: { card }, selected: false, dragging: false } as ComponentProps<typeof WorldCardNode>;
  const { container } = render(<ReactFlowProvider><WorldCardNode {...props} /></ReactFlowProvider>);
  const backpack = () => screen.getAllByRole('button', { name: 'Equipment for Equipment owner' });
  const surface = container.querySelector('.world-card')!;

  for (const level of ['node', 'preview'] as const) {
    act(() => useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: level } }));
    expect(surface.querySelector('.node-inspector-footer')).toBeNull();
    expect(surface.querySelector('.node-surface-close')).toBeNull();
    expect(backpack()).toHaveLength(1);
    expect(surface.querySelector(':scope > .equipment-toggle')).toBe(backpack()[0]);
  }
  fireEvent.click(backpack()[0]);
  expect(useEquipmentPanel.getState().openIds).toEqual([card.id]);
  act(() => useNodeSurfaceStore.getState().openInspector(card.id));
  expect(backpack()).toHaveLength(1);
  expect(surface.querySelector('.node-inspector-footer .equipment-toggle')).toBe(backpack()[0]);
  expect(backpack()[0].getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByRole('button', { name: 'Open workspace' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Close Equipment owner inspector' }));
  expect(surface.querySelector('.node-inspector-footer')).toBeNull();
  expect(surface.querySelector('.node-surface-close')).toBeNull();
  expect(backpack()).toHaveLength(1);
  expect(backpack()[0].getAttribute('aria-expanded')).toBe('true');
  act(() => useNodeSurfaceStore.getState().openWorkspace(card.id));
  expect(screen.getByText('Agent workspace')).toBeTruthy();
  expect(surface.querySelector('.node-inspector-footer')).toBeNull();
  expect(backpack()).toHaveLength(1);
});
