import { afterEach, expect, it, vi } from 'vitest';
import { fetchWithRetry } from './fetchWithRetry';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it('recovers startup reads from a network failure followed by a Vite proxy failure', async () => {
  vi.useFakeTimers();
  const response = new Response('{"mode":"builder"}');
  const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockResolvedValueOnce(new Response(null, { status: 500, headers: { 'content-type': 'text/plain' } }))
    .mockResolvedValueOnce(response);
  vi.stubGlobal('fetch', fetchMock);
  const pending = fetchWithRetry('/api/deployment');
  await vi.advanceTimersByTimeAsync(1000);
  expect(await pending).toBe(response);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it.each([502, 503, 504])('bounds retries and preserves the final %i response', async status => {
  vi.useFakeTimers();
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(null, { status })));
  vi.stubGlobal('fetch', fetchMock);
  const pending = fetchWithRetry('/api/world');
  await vi.advanceTimersByTimeAsync(1000);
  expect((await pending).status).toBe(status);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it.each([401, 403, 409, 422, 500])('preserves application errors (%i) without retrying', async status => {
  const response = new Response('{"error":"detail"}', { status, headers: { 'content-type': 'application/json' } });
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetchMock);
  expect(await fetchWithRetry('/api/world')).toBe(response);
  expect(await response.json()).toEqual({ error: 'detail' });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('preserves a plain-text backend error instead of treating it as a proxy connection failure', async () => {
  const response = new Response('Internal Server Error', { status: 500 });
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetchMock);
  expect(await fetchWithRetry('/api/world')).toBe(response);
  expect(await response.text()).toBe('Internal Server Error');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it.each(['POST', 'PATCH', 'DELETE'])('never replays a %s mutation after a network failure', async method => {
  const error = new TypeError('Failed to fetch');
  const fetchMock = vi.fn().mockRejectedValue(error);
  vi.stubGlobal('fetch', fetchMock);
  await expect(fetchWithRetry('/api/nodes', { method })).rejects.toBe(error);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('cancels the backoff immediately when its caller aborts', async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
  vi.stubGlobal('fetch', fetchMock);
  const result = fetchWithRetry('/api/world', { signal: controller.signal }).catch(error => error);
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  expect(await result).toBe(controller.signal.reason);
  await vi.advanceTimersByTimeAsync(1000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('stops after three network failures', async () => {
  vi.useFakeTimers();
  const error = new TypeError('Failed to fetch');
  const fetchMock = vi.fn().mockRejectedValue(error);
  vi.stubGlobal('fetch', fetchMock);
  const result = fetchWithRetry('/api/world').catch(error => error);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await result).toBe(error);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
