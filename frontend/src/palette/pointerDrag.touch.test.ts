// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { startPalettePointerDrag } from './pointerDrag';

let source: HTMLButtonElement;
let cancel: (() => void) | undefined;
const start = vi.fn(), end = vi.fn(), cancelClick = vi.fn();
beforeEach(() => {
  start.mockClear(); end.mockClear(); cancelClick.mockClear();
  source = document.createElement('button');
  source.innerHTML = '<span data-deck-visual>Card</span>';
  source.setPointerCapture = vi.fn(); source.hasPointerCapture = () => false;
  document.body.append(source);
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => source });
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
});
afterEach(() => { cancel?.(); source.remove(); vi.unstubAllGlobals(); });
function begin() {
  cancel = startPalettePointerDrag({ pointerType: 'touch', pointerId: 1, isPrimary: true, button: 0, clientX: 30, clientY: 100 } as PointerEvent,
    source, { entry: { kind: 'node', id: 'text' } }, { start, end, cancelClick });
}
function move(clientX: number, clientY: number) {
  const event = Object.assign(new MouseEvent('pointermove', { bubbles: true, cancelable: true, buttons: 1, clientX, clientY }), { pointerId: 1 });
  window.dispatchEvent(event);
  return event;
}

it('leaves horizontal swipes to native scrolling without a preview or a later accidental placement', () => {
  begin();
  expect(source.setPointerCapture).not.toHaveBeenCalled();
  const event = move(80, 101);
  expect(event.defaultPrevented).toBe(false);
  expect(start).not.toHaveBeenCalled();
  expect(cancelClick).toHaveBeenCalledOnce();
  expect(document.querySelector('.palette-drag-preview')).toBeNull();
});

it('starts a preview only on an upward pull and cancels it when a second finger joins', () => {
  begin();
  const event = move(32, 75);
  expect(event.defaultPrevented).toBe(true);
  expect(start).toHaveBeenCalledOnce();
  expect(source.setPointerCapture).toHaveBeenCalledWith(1);
  expect(document.querySelector('.palette-drag-preview')).not.toBeNull();
  window.dispatchEvent(Object.assign(new Event('pointerdown'), { pointerType: 'touch', pointerId: 2 }));
  expect(document.querySelector('.palette-drag-preview')).toBeNull();
  expect(end).toHaveBeenCalledOnce();
});
