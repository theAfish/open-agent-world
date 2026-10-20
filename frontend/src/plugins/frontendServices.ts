import { useWorldStore } from '../state/worldStore';

/** Explicit Pack-to-Pack frontend extension lookup, independent of source paths.
 * The backend's installed dependency/owner contract determines availability.
 */
export async function loadPackFrontendService<T>(owner: string, service: string): Promise<T> {
  const { loadFrontendPlugin } = await import('./registry');
  const catalog = useWorldStore.getState().catalog;
  if (!catalog?.plugins.some(plugin => plugin.id === owner)) throw new Error(`Required Pack ${owner} is not loaded`);
  const runtime = catalog.frontend_modules?.[owner];
  const plugin = await loadFrontendPlugin(owner, runtime);
  const value = plugin.services?.[service];
  if (value === undefined) throw new Error(`Frontend service ${owner}/${service} is unavailable`);
  return value as T;
}

/** The structure Pack owns parsing and rendering; consumers need no MatterViz. */
export interface StructureRendererService {
  prepare(file: { name: string; data: string }): Promise<unknown>;
  render(target: HTMLElement, file: { name: string; data: string }, onError: (message: string) => void, prepared?: unknown): Promise<StructureRendererHandle>;
}
export interface StructureRendererHandle {
  (): void;
  update(file: { name: string; data: string }, prepared?: unknown): void;
  painted(): Promise<void>;
}
