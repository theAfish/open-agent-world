// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useTouchHold } from './useTouchHold';
import { TOUCH_HOLD_DELAY } from './feedback';

const callbacks = { onHold: vi.fn(), onMove: vi.fn(), onRelease: vi.fn(), onCancel: vi.fn(), click: vi.fn() };
function Surface({ disabled = false, allowMouse = false }: { disabled?: boolean; allowMouse?: boolean }) {
  const hold = useTouchHold({ ...callbacks, disabled, allowMouse });
  return <button {...hold.bind} data-touch-hold={hold.phase} onClick={callbacks.click}>Card</button>;
}
function pointer(target: EventTarget, type: string, init: { pointerId?: number; pointerType?: string; isPrimary?: boolean; clientX?: number; clientY?: number } = {}) {
  const event = Object.assign(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: init.clientX ?? 10, clientY: init.clientY ?? 10 }),
    { pointerId: init.pointerId ?? 1, pointerType: init.pointerType ?? 'touch', isPrimary: init.isPrimary ?? true });
  act(() => { target.dispatchEvent(event); });
  return event;
}
const hold = () => act(() => { vi.advanceTimersByTime(TOUCH_HOLD_DELAY); });
beforeEach(() => {
  vi.useFakeTimers();
  Object.values(callbacks).forEach(callback => callback.mockClear());
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('recognizes a hold once, continues the same pointer as a drag, and consumes its click', () => {
  render(<Surface />);
  const card = screen.getByRole('button');
  pointer(card, 'pointerdown'); hold();
  expect(card.getAttribute('data-touch-hold')).toBe('active');
  pointer(window, 'pointermove', { clientX: 100 });
  pointer(window, 'pointerup', { clientX: 130 });
  expect(callbacks.onHold).toHaveBeenCalledTimes(1);
  expect(callbacks.onMove).toHaveBeenCalledWith(expect.objectContaining({ clientX: 100, pointerId: 1 }));
  expect(callbacks.onRelease).toHaveBeenCalledWith(expect.objectContaining({ clientX: 130, pointerId: 1 }));
  fireEvent.click(card, { detail: 1 });
  expect(callbacks.click).not.toHaveBeenCalled();
  fireEvent.click(card, { detail: 0 });
  expect(callbacks.click).toHaveBeenCalledTimes(1);
});

it('accepts a mouse hold only when a card grip explicitly opts in', () => {
  render(<Surface allowMouse />);
  pointer(screen.getByRole('button'), 'pointerdown', { pointerType: 'mouse' }); hold();
  pointer(window, 'pointermove', { pointerType: 'mouse', clientX: 80 });
  pointer(window, 'pointerup', { pointerType: 'mouse', clientX: 80 });
  expect(callbacks.onHold).toHaveBeenCalledOnce();
  expect(callbacks.onRelease).toHaveBeenCalledWith(expect.objectContaining({ clientX: 80 }));
});

it('keeps a quick tap as an ordinary click and cancels a hold when a scroll starts', () => {
  render(<Surface />);
  const card = screen.getByRole('button');
  pointer(card, 'pointerdown'); pointer(window, 'pointerup');
  fireEvent.click(card, { detail: 1 });
  expect(callbacks.click).toHaveBeenCalledTimes(1);
  pointer(card, 'pointerdown');
  const move = pointer(window, 'pointermove', { clientX: 30 });
  hold();
  expect(move.defaultPrevented).toBe(false);
  expect(callbacks.onHold).not.toHaveBeenCalled();
  expect(card.getAttribute('data-touch-hold')).toBe('idle');
});

it('cancels on a second finger and does not restart the first hold', () => {
  render(<Surface />);
  const card = screen.getByRole('button');
  pointer(card, 'pointerdown');
  pointer(card, 'pointerdown', { pointerId: 2, isPrimary: false });
  hold();
  expect(callbacks.onHold).not.toHaveBeenCalled();
  pointer(card, 'pointerdown'); hold();
  pointer(document, 'pointerdown', { pointerId: 2, isPrimary: false });
  pointer(window, 'pointerup');
  expect(callbacks.onCancel).toHaveBeenCalledTimes(1);
  expect(callbacks.onRelease).not.toHaveBeenCalled();
});

it.each(['pointercancel', 'lostpointercapture', 'blur'])('cancels an active gesture on %s', type => {
  render(<Surface />);
  const card = screen.getByRole('button');
  pointer(card, 'pointerdown'); hold();
  if (type === 'blur') act(() => window.dispatchEvent(new Event(type)));
  else pointer(type === 'lostpointercapture' ? card : window, type);
  expect(callbacks.onCancel).toHaveBeenCalledTimes(1);
  expect(card.getAttribute('data-touch-hold')).toBe('idle');
});

it('cancels pending timers when disabled and active gestures when unmounted', () => {
  const view = render(<Surface />);
  pointer(screen.getByRole('button'), 'pointerdown');
  view.rerender(<Surface disabled />); hold();
  expect(callbacks.onHold).not.toHaveBeenCalled();
  view.rerender(<Surface />);
  pointer(screen.getByRole('button'), 'pointerdown'); hold();
  view.unmount();
  expect(callbacks.onCancel).toHaveBeenCalledTimes(1);
});

it.each(['mouse', 'pen'])('leaves %s interactions to their existing handlers', pointerType => {
  render(<Surface />);
  pointer(screen.getByRole('button'), 'pointerdown', { pointerType }); hold();
  expect(callbacks.onHold).not.toHaveBeenCalled();
});

it('allows a fresh mouse click if the preceding touch hold produced no compatibility click', () => {
  render(<Surface />);
  const card = screen.getByRole('button');
  pointer(card, 'pointerdown'); hold(); pointer(window, 'pointerup');
  pointer(card, 'pointerdown', { pointerType: 'mouse' });
  pointer(window, 'pointerup', { pointerType: 'mouse' });
  fireEvent.click(card, { detail: 1 });
  expect(callbacks.click).toHaveBeenCalledTimes(1);
});

it('does not consume the next control tap after a hold was cancelled without a click', () => {
  render(<Surface />);
  const card = screen.getByRole('button');
  pointer(card, 'pointerdown'); hold(); pointer(window, 'pointercancel');
  pointer(document, 'pointerdown', { pointerId: 3 });
  fireEvent.click(card, { detail: 1 });
  expect(callbacks.click).toHaveBeenCalledTimes(1);
});
