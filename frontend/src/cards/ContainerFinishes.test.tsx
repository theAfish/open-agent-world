// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import type { ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { ContainerFrame } from './ContainerFrame';
import { EquipmentCardNode } from './Equipment';
import { ShadowCollectionNode } from './ShadowCollection';
import { useLocale } from '../i18n';

vi.mock('../effects/ShadowGasBoundary', () => ({ ShadowGasBoundary: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('reserves the header for dragging and reveals management controls without unmounting content', () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  Object.defineProperty(HTMLElement.prototype, 'showPopover', { configurable: true, value: vi.fn() });
  useLocale.setState({ locale: 'en' });
  const card: WorldCard = { id: 'compact-container', type: 'legion', name: 'Research', status: 'idle',
    config: {}, position: { x: 0, y: 0 }, size: { width: 800, height: 550 }, expanded: true };
  useWorldStore.setState({ cards: [card], catalog: { ...TEST_CATALOG, node_types: TEST_CATALOG.node_types.map(type =>
    type.id === card.type ? { ...type, icon_url: '/assets/custom-container.svg' } : type) }, edges: [], events: [] });
  const manage = vi.fn();
  const openWorkspace = vi.fn();
  const { container } = render(<ReactFlowProvider><ContainerFrame card={card} className="legion-container" label="Research"
    primaryActions={<button onClick={openWorkspace}>Workspace mode</button>}
    menuActions={<button onClick={manage}>Manage</button>}>
    <input aria-label="Working content" defaultValue="Draft" />
  </ContainerFrame></ReactFlowProvider>);
  try {
    const content = screen.getByRole('textbox');
    expect(screen.queryByText('Research')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Workspace mode' }));
    expect(openWorkspace).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(container.querySelector('.container-header')?.classList.contains('container-drag-region')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Card controls' }));
    expect(container.querySelector('.container-menu-identity .catalog-asset-icon')).not.toBeNull();
    expect(screen.getByText('Research')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Manage' }));
    expect(manage).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('button', { name: 'Manage' })).toBeNull();
    expect(screen.getByRole('textbox')).toBe(content);
    expect((content as HTMLInputElement).value).toBe('Draft');
  } finally {
    cleanup();
    delete (HTMLElement.prototype as Partial<HTMLElement>).showPopover;
  }
});

it('keeps container and collection chrome matte while preserving equipped card finishes', () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const base: WorldCard = { id: 'printed', type: 'legion', name: 'Printed container', status: 'idle',
    config: {}, position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false, finish: 'rainbow' };
  useWorldStore.setState({ cards: [base], catalog: TEST_CATALOG, edges: [], events: [] });
  const { container, rerender } = render(<ReactFlowProvider>
    <ContainerFrame card={base} className="test-container" label="Printed container" menuActions={null}>
      <input aria-label="Working content" />
    </ContainerFrame>
  </ReactFlowProvider>);
  expect(container.querySelector('.container-header .card-finish-layer')).toBeNull();
  expect(container.querySelector('.container-frame > .card-finish-layer')).toBeNull();
  const equipment = { ...base, type: 'text', name: 'Equipped text' };
  const props: ComponentProps<typeof EquipmentCardNode> = {
    id: equipment.id, type: 'equipment', data: { card: equipment, surfaceLevel: 'node', displaced: false },
    selected: false, dragging: false, selectable: true, deletable: true, draggable: true,
    isConnectable: true, zIndex: 0, positionAbsoluteX: 0, positionAbsoluteY: 0,
  };
  rerender(<ReactFlowProvider><EquipmentCardNode {...props} /></ReactFlowProvider>);
  expect(container.querySelector('.equipment-card .card-finish-layer')?.getAttribute('data-quality')).toBe('thumbnail');
  expect(container.querySelector('.equipment-card')?.getAttribute('data-finish')).toBe('rainbow');
  const shadow = { ...base, type: 'core.shadow-collection', config: { display_state: 'minimal' } };
  const shadowProps: ComponentProps<typeof ShadowCollectionNode> = {
    ...props, type: 'container', data: { ...props.data, card: shadow },
  };
  rerender(<ReactFlowProvider><ShadowCollectionNode {...shadowProps} /></ReactFlowProvider>);
  expect(container.querySelector('.shadow-collection')?.getAttribute('data-finish')).toBe('rainbow');
  expect(container.querySelector('.shadow-counts .card-finish-layer')).toBeNull();
  expect(container.querySelector('.shadow-silhouette .card-finish-layer')).toBeNull();
});
