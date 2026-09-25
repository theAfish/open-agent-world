// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });
it('coalesces identical CIF bytes, clones cached structures, and reparses changed fitted bytes', async () => {
  const messages: { id: number; name: string; data: string }[] = [];
  let worker: FakeWorker;
  class FakeWorker {
    onmessage?: (event: unknown) => void;
    constructor() { worker = this; }
    postMessage(message: typeof messages[number]) { messages.push(message); }
  }
  vi.stubGlobal('Worker', FakeWorker);
  const { prepareStructure } = await import('../../../plugins/structure_viewer/frontend/prepareStructure');
  const file = { name: 'same.cif', data: 'first' };
  const a = prepareStructure(file), b = prepareStructure(file);
  expect(messages).toHaveLength(1);
  worker!.onmessage!({ data: { id: messages[0].id, structure: { sites: [{ label: 'original' }] }, milliseconds: 1 } });
  const first = await a, second = await b;
  expect(first).toEqual(second); expect(first).not.toBe(second); expect(first.sites).not.toBe(second.sites);
  await prepareStructure(file); expect(messages).toHaveLength(1);
  const changed = prepareStructure({ ...file, data: 'fitted' });
  expect(messages).toHaveLength(2);
  worker!.onmessage!({ data: { id: messages[1].id, error: 'bad cell' } });
  await expect(changed).rejects.toThrow('bad cell');
  const retry = prepareStructure({ ...file, data: 'fitted' });
  expect(messages).toHaveLength(3);
  worker!.onmessage!({ data: { id: messages[2].id, structure: { sites: [] }, milliseconds: 1 } });
  await retry;
});
