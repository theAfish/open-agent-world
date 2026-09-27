// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CanvasTransition, useCanvasLoading } from '../../../plugins/xrd/frontend/CanvasTransition';
import { readFrameInfo } from '../../../plugins/xrd/frontend/frameInfo';

afterEach(() => { cleanup(); vi.useRealTimers(); });
function Indicator() { return <span data-testid="busy">{String(useCanvasLoading('load-test'))}</span>; }
it('waits for both canvases and ignores readiness from an obsolete selection', () => {
  vi.useFakeTimers();
  const ready = new Map<string, () => void>();
  const view = (identity: string) => <><Indicator/>{['spectrum', 'structure'].map(kind => <CanvasTransition key={kind} source="load-test" identity={identity}>{finish => { ready.set(`${identity}:${kind}`, finish); return <div>{kind}</div>; }}</CanvasTransition>)}</>;
  const mounted = render(view('a'));
  expect(screen.getByTestId('busy').textContent).toBe('true');
  act(() => ready.get('a:spectrum')!());
  expect(screen.getByTestId('busy').textContent).toBe('true');
  mounted.rerender(view('b'));
  act(() => { ready.get('a:structure')!(); ready.get('b:spectrum')!(); });
  expect(screen.getByTestId('busy').textContent).toBe('true');
  act(() => ready.get('b:structure')!());
  expect(screen.getByTestId('busy').textContent).toBe('true');
  act(() => vi.advanceTimersByTime(440));
  expect(screen.getByTestId('busy').textContent).toBe('false');
  mounted.rerender(view('a'));
  act(() => { ready.get('a:spectrum')!(); ready.get('a:structure')!(); vi.advanceTimersByTime(440); });
  mounted.rerender(view('b'));
  expect(screen.getByTestId('busy').textContent).toBe('true');
  act(() => vi.advanceTimersByTime(440));
  expect(screen.getByTestId('busy').textContent).toBe('true');
  mounted.rerender(view('c'));
  mounted.unmount();
  render(<Indicator/>);
  expect(screen.getByTestId('busy').textContent).toBe('false');
});

it('coalesces concurrent frame reads and retries a failed read without caching the error', async () => {
  let resolve!: (value: {session_id: string}) => void;
  const load = vi.fn(() => new Promise<{session_id: string}>(done => { resolve = done; }));
  const first = readFrameInfo('shared-read', load), second = readFrameInfo('shared-read', load);
  await Promise.resolve(); expect(load).toHaveBeenCalledOnce();
  resolve({ session_id: 'ok' });
  expect(await first).toEqual(await second);
  await expect(readFrameInfo('shared-read', () => Promise.reject(new Error('offline')))).rejects.toThrow('offline');
  await expect(readFrameInfo('shared-read', () => Promise.resolve({ session_id: 'retry' }))).resolves.toEqual({ session_id: 'retry' });
});
