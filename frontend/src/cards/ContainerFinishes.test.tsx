// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import type { ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { ContainerFrame } from './ContainerFrame';
import { EquipmentCardNode } from './Equipment';
import { ShadowCollectionNode } from './ShadowCollection';

vi.mock('../effects/ShadowGasBoundary', () => ({ ShadowGasBoundary: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('preserves finishes on container chrome, equipment and custom shadow collections', () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const base: WorldCard = { id: 'printed', type: 'legion', name: 'Printed container', status: 'idle',
    config: {}, position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false, finish: 'rainbow' };
  useWorldStore.setState({ cards: [base], catalog: TEST_CATALOG, edges: [], events: [] });
  const { container, rerender } = render(<ReactFlowProvider>
    <ContainerFrame card={base} className="test-container" label="Printed container" header={<strong>Container title</strong>}>
      <input aria-label="Working content" />
    </ContainerFrame>
  </ReactFlowProvider>);
  expect(container.querySelector('.container-header .card-finish-layer')?.getAttribute('data-finish')).toBe('rainbow');
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
  expect(container.querySelector('.shadow-counts .card-finish-layer')?.getAttribute('data-quality')).toBe('thumbnail');
  expect(container.querySelector('.shadow-silhouette .card-finish-layer')).toBeNull();
});
