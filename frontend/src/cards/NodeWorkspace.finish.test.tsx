// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { WorkspaceSurface } from './NodeWorkspace';

afterEach(cleanup);

it('preserves finish metadata while keeping the entire workspace matte', () => {
  const card: WorldCard = { id: 'printed-workspace', type: 'custom.workspace', name: 'Research',
    status: 'idle', config: {}, position: { x: 0, y: 0 }, size: { width: 600, height: 420 },
    expanded: true, finish: 'laser' };
  useWorldStore.setState({ cards: [card], catalog: TEST_CATALOG });
  const { container, rerender } = render(<WorkspaceSurface card={card} />);
  expect(container.querySelector('.card-finish-layer')).toBeNull();
  rerender(<WorkspaceSurface card={{ ...card }} />);
  expect(container.querySelector('.workspace-titlebar')?.getAttribute('data-finish')).toBe('laser');
  rerender(<WorkspaceSurface card={{ ...card, finish: undefined }} />);
  expect(container.querySelector('.workspace-titlebar')?.getAttribute('data-finish')).toBe('normal');
  expect(container.querySelector('.card-finish-layer')).toBeNull();
});
