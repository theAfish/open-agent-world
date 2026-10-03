// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PackSurface } from './PackSurface';

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
const surface = (name = 'Tools') => <PackSurface definition={{ id: 'tools', plugin_id: 'test', name, description: 'Useful tools', cards: [] }}
  edition="Example" cards={[]} count={0} opened={false} label="Open Tools" sealLabel="OPEN" onClick={() => {}} />;
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
});

it('uses the accessible fallback when GPU initialization fails even with a cached preview', async () => {
  mocks.restore.mockReturnValue(true);
  mocks.attach.mockImplementationOnce(() => { throw new Error('GPU unavailable'); });
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const view = render(surface());
  await visible(true);
  expect(view.container.querySelector('article')?.dataset.renderer).toBe('fallback');
  warning.mockRestore();
});
