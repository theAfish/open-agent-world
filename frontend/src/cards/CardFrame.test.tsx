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
import { CardContent, WorldCardNode } from './CardFrame';

vi.mock('./AgentCard', () => ({ AgentCardBody: () => <div>Agent settings</div> }));
vi.mock('./NodeWorkspace', () => ({ WorkspaceSurface: () => <div>Agent workspace</div> }));
vi.mock('../plugins/PluginSurface', () => ({ PluginSurface: ({children,card}: {children?: React.ReactNode;card:WorldCard}) => card.type === 'test.instrument' ? <div>Instrument preview</div> : children }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (HTMLElement.prototype as Partial<HTMLElement>).showPopover; useEquipmentPanel.setState({ openIds: [] }); });

it('renders a preview-only content instrument without card print or title bars and retains rename/delete and handles', () => {
  Object.defineProperty(HTMLElement.prototype,'showPopover',{configurable:true,value:vi.fn()});
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const card: WorldCard = {id:'instrument',type:'test.instrument',name:'Counter',status:'available',config:{},
    position:{x:0,y:0},size:{width:360,height:192},expanded:false};
  const definition = {...TEST_CATALOG.node_types[0], id:card.type, label:'Counter', traits:['ui.content-surface.v1','ui.ellipse.v1'],
    frontend:{preview:'instrument'}, presentation:{states:['preview' as const],initial:'preview' as const,open:'preview' as const}};
  const catalog = {...TEST_CATALOG,node_types:[...TEST_CATALOG.node_types,definition]};
  useWorldStore.setState({cards:[card],edges:[],events:[],catalog});
  useNodeSurfaceStore.getState().syncCards([card],catalog);
  useNodeSurfaceStore.setState({surfaceLevels:{[card.id]:'preview'},connectingNodeId:undefined,dragging:false});
  const props = {id:card.id,data:{card},selected:false,dragging:false} as ComponentProps<typeof WorldCardNode>;
  const {container} = render(<ReactFlowProvider><WorldCardNode {...props}/></ReactFlowProvider>);
  expect(container.querySelector('.world-card')?.getAttribute('data-content-surface')).toBe('true');
  expect(container.querySelector('.world-card')?.getAttribute('data-card-shape')).toBe('ellipse');
  expect((container.querySelector('.world-card') as HTMLElement).style.borderRadius).toBe('50%');
  expect(container.querySelector('.node-surface-header, .world-card-print, .card-finish-layer')).toBeNull();
  expect(screen.getByText('Instrument preview')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Card details'));
  expect(screen.getByRole('button',{name:'Remove Counter'})).toBeTruthy();
  expect(container.querySelectorAll('.semantic-handle')).toHaveLength(4);
  const panel = screen.getByRole('dialog',{name:'Card details'});
  fireEvent.pointerDown(panel);
  expect(screen.getByRole('dialog',{name:'Card details'})).toBe(panel);
  // A canvas/background press need not move focus to dismiss the menu.
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('dialog',{name:'Card details'})).toBeNull();
});

it('lets a card opt out of expanded title/footer bars while retaining its collectible face and actions', () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const card: WorldCard = { id: 'content', type: 'agent', name: 'Content first', status: 'idle', config: {}, finish: 'starlight',
    position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false };
  const catalog = { ...TEST_CATALOG, node_types: TEST_CATALOG.node_types.map(item => item.id === 'agent'
    ? { ...item, traits: [...item.traits, 'ui.content-surface.v1'] } : item) };
  useWorldStore.setState({ cards: [card], edges: [], events: [], catalog });
  useNodeSurfaceStore.getState().syncCards([card], catalog);
  useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: 'inspector' }, baseLevels: { [card.id]: 'preview' } });
  const props = { id: card.id, data: { card }, selected: false, dragging: false } as ComponentProps<typeof WorldCardNode>;
  const { container } = render(<ReactFlowProvider><WorldCardNode {...props} /></ReactFlowProvider>);
  expect(container.querySelector('.node-surface-header, .node-inspector-footer')).toBeNull();
  expect(container.querySelector('.content-surface-grip.node-drag-region')).not.toBeNull();
  expect(screen.getByRole('button', { name: 'Open workspace' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Close Content first inspector' }));
  expect(container.querySelector('.content-surface-controls')).toBeNull();
  expect(container.querySelector('.node-surface-header')).not.toBeNull();
  expect(container.querySelector('.world-card')?.getAttribute('data-finish')).toBe('starlight');
});

it('shows a missing placeholder instead of dispatching a same-named installed implementation', () => {
  const card: WorldCard = { id: 'lost', type: 'agent', name: 'Old agent', status: 'idle', config: {},
    position: { x: 0, y: 0 }, size: { width: 300, height: 190 }, expanded: false,
    missing_plugin: { plugin_id: 'removed.pack', reason: 'owner_mismatch' } };
  useWorldStore.setState({ cards: [card], catalog: TEST_CATALOG });
  render(<CardContent card={card} level="inspector" />);
  expect(screen.getByText('MISSING')).toBeTruthy();
  expect(screen.getByText('removed.pack')).toBeTruthy();
  expect(screen.queryByText('Agent settings')).toBeNull();
});

it('keeps one working backpack across surfaces and mounts inspector actions only while open', () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const card: WorldCard = { id: 'owner', type: 'agent', name: 'Equipment owner', status: 'idle', config: {}, finish: 'starlight',
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
    expect(surface.getAttribute('data-finish')).toBe('starlight');
    expect(surface.querySelector(':scope > .card-finish-layer')?.getAttribute('data-quality')).toBe(level === 'node' ? 'thumbnail' : 'standard');
    expect(surface.querySelector(':scope > .card-finish-layer > .card-material-canvas')).not.toBeNull();
  }
  fireEvent.click(backpack()[0]);
  expect(useEquipmentPanel.getState().openIds).toEqual([card.id]);
  act(() => useNodeSurfaceStore.getState().openInspector(card.id));
  expect(surface.querySelector(':scope > .card-finish-layer')).toBeNull();
  expect(surface.querySelector('.card-header .card-finish-layer')).toBeNull();
  expect(screen.getByText('Finish: Starlight')).toBeTruthy();
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
