// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createRef } from 'react';
import { Popover, Tooltip } from './Floating';

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(HTMLElement.prototype, 'showPopover', { configurable: true, value: vi.fn() });
});
afterEach(() => {
  cleanup(); vi.useRealTimers();
  delete (HTMLElement.prototype as Partial<HTMLElement>).showPopover;
});

it('delays hover, lets the pointer reach the explanation, and cancels pending hints on unmount', () => {
  const ref = createRef<HTMLButtonElement>(), enter = vi.fn();
  const view = render(<Tooltip content="Import a spectrum"><button ref={ref} onPointerEnter={enter}>Import</button></Tooltip>);
  const button = screen.getByRole('button');
  expect(ref.current).toBe(button);
  fireEvent.pointerEnter(button);
  act(() => vi.advanceTimersByTime(349));
  expect(screen.queryByRole('tooltip')).toBeNull();
  act(() => vi.advanceTimersByTime(1));
  const tooltip = screen.getByRole('tooltip');
  expect(enter).toHaveBeenCalledOnce();
  fireEvent.pointerLeave(button); fireEvent.pointerEnter(tooltip);
  act(() => vi.advanceTimersByTime(200));
  expect(screen.getByRole('tooltip')).toBe(tooltip);
  fireEvent.pointerLeave(tooltip);
  act(() => vi.advanceTimersByTime(120));
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.pointerEnter(button); view.unmount();
  act(() => vi.advanceTimersByTime(350));
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('associates keyboard hints without replacing descriptions or actions and consumes Escape', () => {
  const click = vi.fn(), outerEscape = vi.fn();
  render(<div onKeyDown={outerEscape}><p id="existing">Accepted formats</p>
    <Tooltip content="Import a spectrum"><button aria-describedby="existing" onClick={click}>Import</button></Tooltip>
  </div>);
  const button = screen.getByRole('button');
  fireEvent.focus(button);
  expect(button.getAttribute('aria-describedby')?.split(' ')).toEqual(['existing', screen.getByRole('tooltip').id]);
  fireEvent.keyDown(button, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(outerEscape).not.toHaveBeenCalled();
  expect(button.getAttribute('aria-describedby')).toBe('existing');
  fireEvent.focus(button); fireEvent.click(button);
  expect(click).toHaveBeenCalledOnce();
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('opens a nonmodal popover, focuses its content, and restores the trigger on close', () => {
  render(<Popover label="Options" trigger={<button>Settings</button>}>
    {close => <button onClick={close}>Done</button>}
  </Popover>);
  const trigger = screen.getByRole('button', { name: 'Settings' });
  fireEvent.click(trigger);
  expect(trigger.getAttribute('aria-controls')).toBe(screen.getByRole('dialog', { name: 'Options' }).id);
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Done' }));
  fireEvent.click(screen.getByRole('button', { name: 'Done' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
});

it('dismisses on outside pointer/focus while allowing Tab to leave without stealing focus', () => {
  render(<><Popover label="Options" trigger={<button>Settings</button>}><input aria-label="Value" /></Popover><button>Outside</button></>);
  const trigger = screen.getByRole('button', { name: 'Settings' }), outside = screen.getByRole('button', { name: 'Outside' });
  fireEvent.click(trigger); fireEvent.pointerDown(outside);
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(trigger);
  act(() => outside.focus());
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(outside);
});

it('dismisses only the topmost floating surface on Escape', () => {
  render(<Popover label="Options" trigger={<button>Settings</button>}>
    <Tooltip content="Value help"><button>Value</button></Tooltip>
  </Popover>);
  const trigger = screen.getByRole('button', { name: 'Settings' });
  fireEvent.click(trigger);
  expect(screen.getByRole('tooltip').textContent).toBe('Value help');
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
