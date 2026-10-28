// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PackSurface } from './PackSurface';
import type { PackRenderOptions } from './pack3d/types';

vi.mock('./pack3d/artwork', () => ({ paintPackCover: vi.fn() }));

const mocks = vi.hoisted(() => ({ attach: vi.fn(), restore: vi.fn() }));
vi.mock('./pack3d/renderer', () => ({ attachPackRenderer: mocks.attach }));
vi.mock('./pack3d/preview', async original => ({ ...await original<object>(), restorePackPreview: mocks.restore }));

const observations: { notify(visible: boolean): void; disconnect: ReturnType<typeof vi.fn> }[] = [];
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('WebGL2RenderingContext', class {});
  vi.stubGlobal('IntersectionObserver', class {
    disconnect = vi.fn();
    constructor(callback: IntersectionObserverCallback) {
      observations.push({ notify: visible => callback([{ isIntersecting: visible } as IntersectionObserverEntry], this as unknown as IntersectionObserver), disconnect: this.disconnect });
    }
    observe() {}
  });
  mocks.attach.mockImplementation(() => ({ update: vi.fn(), destroy: vi.fn(), hover: vi.fn() }));
  mocks.restore.mockReturnValue(false);
});
afterEach(() => { cleanup(); observations.length = 0; vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
const surface = (name = 'Tools', metadata: Pick<PackRenderOptions, 'version' | 'notice'> = {}) => <PackSurface {...metadata} definition={{ id: 'tools', plugin_id: 'test', name, description: 'Useful tools', cards: [] }}
  edition="Example" cards={[]} count={0} opened={false} label="Open Tools" onClick={() => {}} />;
async function visible(value: boolean) { await act(async () => { observations[0].notify(value); }); }

it('starts GPU work only when visible, retains it during brief scrolling, and releases it while hidden', async () => {
  const view = render(surface());
  expect(mocks.attach).not.toHaveBeenCalled();
  await visible(true);
  expect(mocks.attach).toHaveBeenCalledTimes(1);
  const handle = mocks.attach.mock.results[0].value;
  await visible(false);
  act(() => vi.advanceTimersByTime(1000));
  await visible(true);
  act(() => vi.advanceTimersByTime(2000));
  expect(handle.destroy).not.toHaveBeenCalled();
  await visible(false);
  act(() => vi.advanceTimersByTime(1500));
  expect(handle.destroy).toHaveBeenCalledTimes(1);
  await visible(true);
  expect(mocks.attach).toHaveBeenCalledTimes(2);
  view.unmount();
  expect(mocks.attach.mock.results[1].value.destroy).toHaveBeenCalledTimes(1);
  expect(observations[0].disconnect).toHaveBeenCalled();
});

it('shows a cached 3D frame before GPU initialization, but never reuses it for changed metadata', () => {
  mocks.restore.mockReturnValueOnce(true);
  const view = render(surface());
  expect(view.container.querySelector('article')?.dataset.renderer).toBe('webgl');
  expect(mocks.attach).not.toHaveBeenCalled();
  view.rerender(surface('Updated tools'));
  expect(view.container.querySelector('article')?.dataset.renderer).toBe('loading');
  expect(mocks.restore).toHaveBeenCalledTimes(2);
  expect(mocks.restore.mock.calls[0][0]).not.toBe(mocks.restore.mock.calls[1][0]);
  view.rerender(surface('Updated tools', { version: '2.0.0', notice: { kind: 'warning', label: 'Pack uninstalled' } }));
  view.rerender(surface('Updated tools', { version: '2.0.1', notice: { kind: 'warning', label: 'Pack uninstalled' } }));
  view.rerender(surface('Updated tools', { version: '2.0.1', notice: { kind: 'restart', label: 'Restart required' } }));
  view.rerender(surface('Updated tools', { version: '2.0.1' }));
  expect(new Set(mocks.restore.mock.calls.map(([key]) => key)).size).toBe(6);
});

it('uses the accessible fallback when GPU initialization fails even with a cached preview', async () => {
  mocks.restore.mockReturnValue(true);
  mocks.attach.mockImplementationOnce(() => { throw new Error('GPU unavailable'); });
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const view = render(surface());
  await visible(true);
  expect(view.container.querySelector('article')?.dataset.renderer).toBe('fallback');
  expect(view.container.querySelector('.pack-cover')).toBeTruthy();
  expect(view.container.querySelector('.pack-fallback, .pack-object')).toBeNull();
  warning.mockRestore();
});

it('keeps the same catalog card and material when a WebGL reveal loses its context', async () => {
  const definition = { id: 'example.photo', traits: [], card_face: { variant: 'image' as const, tone: 'rose' as const, image_url: '/photo.png' } };
  const { container } = render(<PackSurface definition={{ id: 'photos', plugin_id: 'example', name: 'Photos', description: '', cards: [definition.id] }}
    edition="Example" cards={[{ id: definition.id, definition, label: 'A very long photo title', icon: <svg />, finish: 'laser' }]}
    phase="revealing" finishVisible count={1} opened label="Photos" onClick={() => {}} />);
  await visible(true);
  const ready = mocks.attach.mock.calls[0][2];
  act(() => ready(true));
  const card = container.querySelector('.pack-reveal-card .card-stock')!;
  expect(card.classList.contains('card-stock--compact')).toBe(true);
  expect(card.getAttribute('data-finish')).toBe('laser');
  expect(card.querySelector('.card-face')?.getAttribute('data-tone')).toBe('rose');
  expect(card.querySelector('.card-face-image')?.getAttribute('src')).toBe('/photo.png');
  expect(card.querySelector('.card-face-copy strong')?.textContent).toBe('A very long photo title');
  expect(card.querySelector('.card-finish-layer')?.getAttribute('data-reveal')).toBe('true');
  const projected = card.parentElement!;
  projected.style.transform = 'matrix(1,0,0,1,100,100)';
  projected.style.opacity = '.5';
  act(() => ready(false));
  expect(container.querySelector('article')?.dataset.renderer).toBe('fallback');
  expect(container.querySelector('.pack-reveal-card .card-stock')).toBe(card);
  expect(projected.style.transform).toBe('');
  expect(projected.style.opacity).toBe('');
  act(() => ready(true));
  expect(container.querySelector('.pack-reveal-card .card-stock')).toBe(card);
});
