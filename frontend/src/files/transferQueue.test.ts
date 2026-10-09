import { describe, expect, it, vi } from 'vitest';
import { TransferQueue, type TransferOptions } from './transferQueue';

describe('file transfer queue', () => {
  it('reports byte progress, continues after failure and retries only the requested item', async () => {
    const queue = new TransferQueue();
    const failed = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);
    const done = vi.fn(async (options: TransferOptions) => options.onProgress?.({ loaded: 4, total: 4 }));
    await queue.add([{ name: 'one', size: 8, direction: 'upload', run: failed }, { name: 'two', size: 4, direction: 'download', run: done }]);
    expect(queue.snapshot().map(item => item.status)).toEqual(['failed', 'succeeded']);
    expect(queue.snapshot()[1].loaded).toBe(4);
    queue.retry(queue.snapshot()[0].id);
    await vi.waitFor(() => expect(queue.snapshot()[0].status).toBe('succeeded'));
    expect(failed).toHaveBeenCalledTimes(2); expect(done).toHaveBeenCalledTimes(1);
  });
  it('cancels active and queued work without starting the queued request', async () => {
    const queue = new TransferQueue();
    const waiting = vi.fn();
    const active = vi.fn((options: TransferOptions) => new Promise<void>((_, reject) => {
      options.signal!.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')));
    }));
    const settled = queue.add([{ name: 'active', size: 8, direction: 'upload', run: active }, { name: 'queued', size: 4, direction: 'upload', run: waiting }]);
    queue.cancel(queue.snapshot()[1].id); queue.cancel(queue.snapshot()[0].id);
    await settled;
    expect(waiting).not.toHaveBeenCalled();
    expect(queue.snapshot().every(item => item.status === 'cancelled')).toBe(true);
    queue.dispose(); queue.retry(1);
    expect(active).toHaveBeenCalledTimes(1);
  });
});
