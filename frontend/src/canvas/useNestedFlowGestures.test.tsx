// @vitest-environment jsdom
import { useRef } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Node } from '@xyflow/react';
import { useNestedFlowGestures } from './useNestedFlowGestures';

const { state, flow } = vi.hoisted(() => {
  const state = { viewport: { x: 10, y: 20, zoom: 1 }, nodes: [] as Node[] };
  return { state, flow: {
    getViewport: () => state.viewport,
    setViewport: vi.fn((next: typeof state.viewport) => { state.viewport = next; }),
    getNode: (id: string) => state.nodes.find(node => node.id === id),
    getNodes: () => state.nodes,
    setNodes: vi.fn((update: (nodes: Node[]) => Node[]) => { state.nodes = update(state.nodes); }),
  } };
});
vi.mock('@xyflow/react', () => ({ useReactFlow: () => flow }));
const finish = vi.fn(), click = vi.fn();

function Map() {
  const root = useRef<HTMLDivElement>(null);
  const selection = useNestedFlowGestures(root, finish);
  return <div ref={root} data-testid="map" data-selection={selection ? 'active' : 'none'} onClick={click}>
    <div className="react-flow__pane" data-testid="pane"><div className="react-flow__node" data-id="one" data-testid="node">Node</div></div>
    <input aria-label="Editable" />
  </div>;
}
function pointer(target: EventTarget, type: string, x: number, y: number, pointerId = 1, touch = true, shiftKey = false) {
  const event = Object.assign(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y, shiftKey }),
    { pointerId, pointerType: touch ? 'touch' : 'mouse' });
  act(() => { target.dispatchEvent(event); });
  return event;
}
beforeEach(() => {
  state.viewport = { x: 10, y: 20, zoom: 1 };
  state.nodes = [
    { id: 'one', position: { x: 50, y: 50 }, data: {}, measured: { width: 100, height: 100 } },
    { id: 'two', position: { x: 260, y: 260 }, data: {}, selected: true, measured: { width: 100, height: 100 } },
  ];
  vi.clearAllMocks();
  render(<Map />);
  const root = screen.getByTestId('map');
  Object.defineProperties(root, { clientWidth: { configurable: true, value: 400 }, clientHeight: { configurable: true, value: 300 } });
  vi.spyOn(root, 'getBoundingClientRect').mockReturnValue({ left: 10, top: 20, width: 200, height: 150 } as DOMRect);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('pans a nested map in its own coordinates under a scaled ancestor', () => {
  pointer(screen.getByTestId('pane'), 'pointerdown', 20, 30);
  const move = pointer(window, 'pointermove', 40, 50);
  expect(state.viewport).toEqual({ x: 50, y: 60, zoom: 1 });
  expect(move.defaultPrevented).toBe(true);
  pointer(window, 'pointerup', 40, 50);
  expect(finish).not.toHaveBeenCalled();
});

it('keeps the same world point under the moving pinch midpoint and rebases the remaining finger as pan', () => {
  const pane = screen.getByTestId('pane');
  pointer(pane, 'pointerdown', 60, 60);
  pointer(pane, 'pointerdown', 160, 60, 2);
  pointer(window, 'pointermove', 260, 60, 2);
  expect(state.viewport).toEqual({ x: -80, y: -40, zoom: 2 });
  pointer(window, 'pointerup', 260, 60, 2);
  pointer(window, 'pointermove', 70, 65);
  expect(state.viewport).toEqual({ x: -60, y: -30, zoom: 2 });
  pointer(window, 'pointerup', 70, 65);
  fireEvent.click(pane, { detail: 1 });
  expect(click).not.toHaveBeenCalled();
  fireEvent.click(pane, { detail: 0 });
  expect(click).toHaveBeenCalledTimes(1);
});

it.each(['pointercancel', 'blur'])('rolls back an unfinished node move on %s without persisting it', type => {
  pointer(screen.getByTestId('node'), 'pointerdown', 60, 60);
  pointer(window, 'pointermove', 80, 60);
  expect(state.nodes[0].position.x).toBe(90);
  if (type === 'blur') act(() => { window.dispatchEvent(new Event('blur')); });
  else pointer(window, 'pointercancel', 80, 60);
  expect(state.nodes[0]).toMatchObject({ position: { x: 50, y: 50 }, dragging: false, selected: false });
  expect(state.nodes[1].selected).toBe(true);
  expect(finish).not.toHaveBeenCalled();
});

it('changes a partial node move into pinch navigation when another finger joins', () => {
  pointer(screen.getByTestId('node'), 'pointerdown', 60, 60);
  pointer(window, 'pointermove', 80, 60);
  pointer(screen.getByTestId('pane'), 'pointerdown', 160, 60, 2);
  expect(state.nodes[0].position).toEqual({ x: 50, y: 50 });
  pointer(window, 'pointermove', 260, 60, 2);
  expect(state.viewport.zoom).toBe(2);
  pointer(window, 'pointercancel', 260, 60, 2);
  const viewport = state.viewport;
  pointer(window, 'pointermove', 100, 80);
  expect(state.viewport).toBe(viewport);
  expect(finish).not.toHaveBeenCalled();
});

it('keeps mouse marquee selection and editable scrolling separate from camera gestures', () => {
  pointer(screen.getByTestId('pane'), 'pointerdown', 10, 20, 1, false, true);
  pointer(window, 'pointermove', 160, 170, 1, false, true);
  expect(state.nodes.map(node => node.selected)).toEqual([true, false]);
  pointer(window, 'pointerup', 160, 170, 1, false, true);
  const field = screen.getByRole('textbox', { name: 'Editable' });
  expect(pointer(field, 'pointerdown', 60, 60).defaultPrevented).toBe(false);
  const wheel = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 20 });
  field.dispatchEvent(wheel);
  expect(wheel.defaultPrevented).toBe(false);
  expect(flow.setViewport).not.toHaveBeenCalled();
});

it('persists a completed node drag once while clearing its dragging state', () => {
  pointer(screen.getByTestId('node'), 'pointerdown', 60, 60, 1, false);
  pointer(window, 'pointermove', 80, 60, 1, false);
  pointer(window, 'pointerup', 80, 60, 1, false);
  expect(finish).toHaveBeenCalledTimes(1);
  expect(finish).toHaveBeenCalledWith([expect.objectContaining({ id: 'one', position: { x: 90, y: 50 }, dragging: false })]);
});
