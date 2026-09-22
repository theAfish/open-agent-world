// @vitest-environment jsdom
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RuntimeConnection } from './RuntimeConnection';
import { useWorldStore } from '../state/worldStore';
import { useCardLibrary } from '../state/cardLibrary';

class Socket extends EventTarget {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  send = vi.fn();
  constructor() { super(); Socket.instances.push(this); }
  close() { this.dispatchEvent(new Event('close')); }
  message(sequence: number, type = 'card_deleted') {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({
      id: `event-${sequence}`, stream_id: 'stream', sequence, type, node_id: `node-${sequence}`, timestamp: 'now', payload: {},
    }) }));
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  Socket.instances = [];
  vi.stubGlobal('WebSocket', Socket);
  vi.spyOn(useWorldStore.getState(), 'refreshWorld').mockResolvedValue();
  vi.spyOn(useCardLibrary.getState(), 'refresh').mockResolvedValue();
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('coalesces separate socket messages and heartbeat watermarks in stream order', () => {
  const ingest = vi.spyOn(useWorldStore.getState(), 'ingestEvents').mockImplementation(() => {});
  const view = render(<RuntimeConnection />);
  const socket = Socket.instances[0];
  act(() => { for (let i = 1; i <= 100; i++) socket.message(i); socket.message(100, 'connection_ready'); });
  expect(ingest).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(16));
  expect(ingest).toHaveBeenCalledTimes(1);
  expect(ingest.mock.calls[0][0].map(event => event.sequence)).toEqual([...Array.from({ length: 100 }, (_, i) => i + 1), 100]);
  view.unmount();
});

it('bounds queued work and flushes the remainder on close and unmount', () => {
  const ingest = vi.spyOn(useWorldStore.getState(), 'ingestEvents').mockImplementation(() => {});
  const view = render(<RuntimeConnection />);
  const socket = Socket.instances[0];
  act(() => { for (let i = 1; i <= 520; i++) socket.message(i); });
  expect(ingest).toHaveBeenCalledTimes(1);
  expect(ingest.mock.calls[0][0]).toHaveLength(512);
  act(() => socket.close());
  expect(ingest.mock.calls[1][0]).toHaveLength(8);
  act(() => vi.advanceTimersByTime(2000));
  const reconnected = Socket.instances[1];
  act(() => reconnected.message(521));
  view.unmount();
  expect(ingest.mock.calls[2][0]).toHaveLength(1);
  act(() => reconnected.message(522));
  act(() => vi.advanceTimersByTime(60_000));
  expect(ingest).toHaveBeenCalledTimes(3);
  expect(Socket.instances).toHaveLength(2);
});
