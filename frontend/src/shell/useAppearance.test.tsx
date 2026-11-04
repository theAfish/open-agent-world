// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAppearance } from '../state/appearance';
import { useApplicationAppearance } from './useAppearance';

function Application() { useApplicationAppearance(); return <div data-testid="canvas" />; }
beforeEach(() => { useAppearance.setState({ textSize: 100, wheelShortcut: true }); });
afterEach(() => { cleanup(); localStorage.clear(); });

it('updates text scale and intercepts Ctrl + wheel before canvas and browser zoom', () => {
  const { getByTestId } = render(<Application />);
  const canvas = getByTestId('canvas');
  const canvasWheel = vi.fn();
  canvas.addEventListener('wheel', canvasWheel);
  fireEvent.keyDown(window, { key: 'Control', ctrlKey: true });
  const wheel = new WheelEvent('wheel', { deltaY: -100, ctrlKey: true, bubbles: true, cancelable: true });
  act(() => { canvas.dispatchEvent(wheel); });
  expect(wheel.defaultPrevented).toBe(true);
  expect(canvasWheel).not.toHaveBeenCalled();
  expect(document.documentElement.style.getPropertyValue('--text-scale')).toBe('1.1');
  fireEvent.wheel(canvas, { deltaY: 3, deltaMode: 1, ctrlKey: true });
  expect(useAppearance.getState().textSize).toBe(100);
  // Fine-resolution wheels accumulate instead of changing size for every pixel.
  fireEvent.wheel(canvas, { deltaY: -20, ctrlKey: true });
  expect(useAppearance.getState().textSize).toBe(100);
  fireEvent.wheel(canvas, { deltaY: -20, ctrlKey: true });
  expect(useAppearance.getState().textSize).toBe(110);
});

it('preserves normal scrolling, trackpad pinch and an explicitly disabled shortcut', () => {
  const { getByTestId } = render(<Application />);
  const canvas = getByTestId('canvas');
  const canvasWheel = vi.fn();
  canvas.addEventListener('wheel', canvasWheel);
  fireEvent.wheel(canvas, { deltaY: -100 });
  fireEvent.wheel(canvas, { deltaY: -100, ctrlKey: true });
  fireEvent.keyDown(window, { key: 'Control', ctrlKey: true });
  fireEvent.blur(window);
  fireEvent.wheel(canvas, { deltaY: -100, ctrlKey: true });
  fireEvent.keyDown(window, { key: 'Control', ctrlKey: true });
  act(() => useAppearance.getState().setWheelShortcut(false));
  fireEvent.wheel(canvas, { deltaY: -100, ctrlKey: true });
  expect(canvasWheel).toHaveBeenCalledTimes(4);
  expect(useAppearance.getState().textSize).toBe(100);
});

it('releases the shortcut and scale when the application unmounts', () => {
  const { unmount } = render(<Application />);
  fireEvent.keyDown(window, { key: 'Control', ctrlKey: true });
  unmount();
  fireEvent.wheel(window, { deltaY: -100, ctrlKey: true });
  expect(useAppearance.getState().textSize).toBe(100);
  expect(document.documentElement.style.getPropertyValue('--text-scale')).toBe('');
});
