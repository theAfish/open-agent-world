// @vitest-environment jsdom
import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { VisualObserver } from './VisualObserver';
import { capturePluginVisual } from '../plugins/visualCapture';
import { captureObservation } from './visualObservation';

vi.mock('../api/client', () => ({ runtimeWebSocketUrl: () => 'ws://localhost/ws/events' }));
vi.mock('../plugins/visualCapture', () => ({ capturePluginVisual: vi.fn() }));
vi.mock('./visualObservation', () => ({ captureObservation: vi.fn() }));

class ObserverSocket {
  static OPEN = 1;
  static latest: ObserverSocket;
  readyState = 1;
  onmessage?: (event: MessageEvent) => void;
  onclose?: () => void;
  onerror?: () => void;
  send = vi.fn();
  constructor() { ObserverSocket.latest = this; }
  close() { this.readyState = 3; this.onclose?.(); }
}

beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('WebSocket', ObserverSocket); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function capture(plugin: boolean) {
  render(<VisualObserver/>);
  const socket = ObserverSocket.latest;
  socket.onmessage?.(new MessageEvent('message', { data: JSON.stringify(plugin ? {
    kind: 'plugin_capture', request_id: 'capture-1', node_id: 'structure-1', capture_kind: 'atomsculptor.structure-viewport',
    document_revision: 3, max_image_dimension: 1280,
  } : { request_id: 'capture-1' }) }));
  await waitFor(() => expect(socket.send).toHaveBeenCalledOnce());
  return JSON.parse(socket.send.mock.calls[0][0]) as { request_id: string; error: string };
}

it('returns the actionable plugin renderer error to the requesting backend', async () => {
  vi.mocked(capturePluginVisual).mockRejectedValue(new Error('The structure viewport is not ready. Add visible atoms before observing.'));
  expect(await capture(true)).toEqual({ request_id: 'capture-1', error: 'The structure viewport is not ready. Add visible atoms before observing.' });
  expect(captureObservation).not.toHaveBeenCalled();
});

it('bounds plugin errors and strips controls without forwarding a stack', async () => {
  const error = new Error('Viewport\u0000\r\n\u202e ' + 'x'.repeat(900));
  error.stack = 'Internal private stack';
  vi.mocked(capturePluginVisual).mockRejectedValue(error);
  const result = await capture(true);
  expect(result.error).toHaveLength(512);
  expect(result.error).toMatch(/^Viewport x+$/);
  expect(result.error).not.toContain('Internal private stack');
});

it('keeps ordinary canvas failures generic', async () => {
  vi.mocked(captureObservation).mockRejectedValue(new Error('Canvas private diagnostic'));
  expect((await capture(false)).error).toBe('Unable to capture this canvas. Keep OAW open and retry.');
  expect(capturePluginVisual).not.toHaveBeenCalled();
});

it('does not stringify arbitrary plugin rejection objects', async () => {
  vi.mocked(capturePluginVisual).mockRejectedValue({ message: 'Unexpected object', secret: 'private' });
  expect((await capture(true)).error).toBe('Unable to capture this canvas. Keep OAW open and retry.');
});
