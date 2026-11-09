// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DEFAULT_BACKGROUND_ID, DEFAULT_PALETTE_ID, registerSkinMod } from '../appearance/registry';
import { useAppearance } from '../state/appearance';
import { useWorldStore } from '../state/worldStore';
import { useApplicationAppearance } from './useAppearance';

function Application() { useApplicationAppearance(); return <div data-testid="canvas" />; }
beforeEach(() => {
  useAppearance.setState({ textSize: 100, wheelShortcut: true, paletteId: DEFAULT_PALETTE_ID, backgroundId: DEFAULT_BACKGROUND_ID, skinMods: [] });
  useWorldStore.setState({ theme: 'light' });
});
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

it('applies the current palette variant and restores previous overrides on switching or unmount', () => {
  const dispose = registerSkinMod({ schemaVersion: 1, id: 'test-colors', name: 'Test colors', palettes: [{
    id: 'ink', name: 'Ink', light: { '--accent': '#123456', '--canvas': '#ddeeff' }, dark: { '--accent': '#abcdef' },
  }], backgrounds: [] });
  const root = document.documentElement;
  root.style.setProperty('--accent', '#765432');
  try {
    useAppearance.setState({ paletteId: 'test-colors/ink' });
    const { unmount } = render(<Application />);
    expect(root.style.getPropertyValue('--accent')).toBe('#123456');
    expect(root.style.getPropertyValue('--canvas')).toBe('#ddeeff');
    act(() => useWorldStore.setState({ theme: 'dark' }));
    expect(root.style.getPropertyValue('--accent')).toBe('#abcdef');
    expect(root.style.getPropertyValue('--canvas')).toBe('');
    act(() => useAppearance.getState().setPaletteId(DEFAULT_PALETTE_ID));
    expect(root.style.getPropertyValue('--accent')).toBe('#765432');
    act(() => useAppearance.getState().setPaletteId('test-colors/ink'));
    unmount();
    expect(root.style.getPropertyValue('--accent')).toBe('#765432');
    expect(root.dataset.palette).toBeUndefined();
  } finally {
    dispose();
    root.style.removeProperty('--accent');
  }
});

it('resolves late and removed registrations without discarding the preferred skin IDs', () => {
  useAppearance.setState({ paletteId: 'late/ink', backgroundId: 'late/checks' });
  render(<Application />);
  const root = document.documentElement;
  expect(root.dataset.palette).toBe(DEFAULT_PALETTE_ID);
  let dispose = () => {};
  act(() => { dispose = registerSkinMod({ schemaVersion: 1, id: 'late', name: 'Late', palettes: [{
    id: 'ink', name: 'Ink', light: { '--accent': '#246810' }, dark: {},
  }], backgrounds: [{ id: 'checks', name: 'Checks', kind: 'grid', spacing: 32 }] }); });
  try {
    expect(root.dataset.palette).toBe('late/ink');
    expect(root.dataset.background).toBe('late/checks');
    expect(root.style.getPropertyValue('--accent')).toBe('#246810');
    act(dispose);
    expect(root.dataset.palette).toBe(DEFAULT_PALETTE_ID);
    expect(root.dataset.background).toBe(DEFAULT_BACKGROUND_ID);
    expect(root.style.getPropertyValue('--accent')).toBe('');
    expect(useAppearance.getState()).toMatchObject({ paletteId: 'late/ink', backgroundId: 'late/checks' });
  } finally { dispose(); }
});
