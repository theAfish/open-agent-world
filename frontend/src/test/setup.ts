/**
 * Node 25 defines a hollow `localStorage`/`sessionStorage` global unless the
 * process is started with `--localstorage-file`. Vitest's jsdom environment
 * copies jsdom's window onto the Node global but skips keys that already exist,
 * so jsdom's own Storage never lands and every `localStorage.getItem` call
 * throws. Install a working in-memory Storage before any module reads one.
 */
class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(String(key)) ?? null; }
  setItem(key: string, value: string) { this.values.set(String(key), String(value)); }
  removeItem(key: string) { this.values.delete(String(key)); }
  clear() { this.values.clear(); }
  [name: string]: unknown;
}

for (const name of ["localStorage", "sessionStorage"] as const) {
  const provided = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  if (typeof provided?.getItem !== "function") {
    Object.defineProperty(globalThis, name, {
      value: new MemoryStorage(), configurable: true, writable: true });
  }
}
