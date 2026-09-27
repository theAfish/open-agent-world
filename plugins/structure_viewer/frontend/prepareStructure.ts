import type { AnyStructure } from 'matterviz/structure';
let worker: Worker | undefined;
let sequence = 0;
const pending = new Map<number, { resolve(value: AnyStructure): void; reject(error: Error): void }>();
const cache = new Map<string, Promise<AnyStructure>>();
function parser() {
  if (worker) return worker;
  worker = new Worker(new URL('./parse.worker.ts', import.meta.url), { type: 'module' });
  worker.onmessage = (event: MessageEvent<{ id: number; structure: AnyStructure; error?: string; milliseconds: number }>) => {
    const request = pending.get(event.data.id); if (!request) return;
    pending.delete(event.data.id);
    if (event.data.error) request.reject(new Error(event.data.error));
    else { console.debug('[XRD CIF parse] ' + JSON.stringify({ milliseconds: event.data.milliseconds, atoms: event.data.structure.sites.length })); request.resolve(event.data.structure); }
  };
  worker.onerror = () => { pending.forEach(p => p.reject(new Error('结构解析线程失败，请重试。'))); pending.clear(); cache.clear(); worker?.terminate(); worker = undefined; };
  return worker;
}
export async function prepareStructure(file: { name: string; data: string }) {
  // Content identity, not the COD number: fitted CIFs may share a filename.
  const key = `${file.name}\0${file.data}`;
  let value = cache.get(key);
  if (value) { cache.delete(key); cache.set(key, value); }
  else {
    const active = parser(); const id = ++sequence;
    value = new Promise<AnyStructure>((resolve, reject) => { pending.set(id, { resolve, reject }); active.postMessage({ id, ...file }); });
    cache.set(key, value);
    value.catch(() => { if (cache.get(key) === value) cache.delete(key); });
    if (cache.size > 24) cache.delete(cache.keys().next().value!);
  }
  // Viewer editing must never mutate a cached scientific input.
  return structuredClone(await value);
}
