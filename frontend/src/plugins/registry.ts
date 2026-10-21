import { createElement, lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { FrontendPlugin, PluginViewProps } from "./sdk";
import type { RuntimeFrontendModule } from "../types/world";
import { providePackRuntime } from "./sharedRuntime";
import { useWorldStore } from '../state/worldStore';

interface Manifest { pluginId: string; apiVersion: number }
const manifests = import.meta.glob<string>(["../../../plugins/*/frontend/plugin.json", "../../../examples/deployed-workspace/plugins/*/frontend/plugin.json"], { eager: true, query: '?raw', import: "default" });
const loaders = new Map<string, () => Promise<{ default: FrontendPlugin }>>();
const duplicateOwners = new Set<string>();
for (const [path, raw] of Object.entries(manifests)) {
  let manifest: Manifest;
  try {
    manifest = JSON.parse(raw) as Manifest;
    if (!manifest || manifest.apiVersion !== 1 || !/^[a-z][a-z0-9._-]*$/.test(manifest.pluginId)) throw new Error(`Invalid frontend plugin manifest: ${path}`);
    if (loaders.has(manifest.pluginId) || duplicateOwners.has(manifest.pluginId)) {
      duplicateOwners.add(manifest.pluginId);
      loaders.delete(manifest.pluginId);
      throw new Error(`Duplicate frontend plugin: ${manifest.pluginId}`);
    }
  } catch (error) { console.error('Optional frontend plugin unavailable', path, error); continue; }
  loaders.set(manifest.pluginId, async () => {
    providePackRuntime();
    const url = `${import.meta.env.BASE_URL}assets/packs/${manifest.pluginId}/index.js`;
    return import(/* @vite-ignore */ url) as Promise<{ default: FrontendPlugin }>;
  });
}

export function runtimeFrontendUrl(pluginId: string, module: RuntimeFrontendModule) {
  const distribution = module.distribution_id ?? pluginId;
  const prefix = `/api/packs/${encodeURIComponent(distribution)}/versions/${encodeURIComponent(module.version)}/frontend/`;
  if (module.api_version !== 1 || !module.url.startsWith(prefix)
      || /[?#\\]/.test(module.url) || module.url.split('/').some(part => part === '..' || part === '.')) {
    throw new Error(`Invalid installed frontend location for ${pluginId}`);
  }
  return module.url;
}

export async function loadRuntimePlugin(pluginId: string, module: RuntimeFrontendModule) {
  const url = runtimeFrontendUrl(pluginId, module);
  assertFrontendAvailable(pluginId, module);
  providePackRuntime();
  return await import(/* @vite-ignore */ url) as { default: FrontendPlugin };
}

/** Artifact presence is not enrollment. Only the live runtime catalog can
 * authorize loading an owner, including source artifacts retained on disk.
 */
export function assertFrontendAvailable(owner: string, runtime?: RuntimeFrontendModule) {
  const catalog = useWorldStore.getState().catalog;
  if (!catalog.plugins.some(plugin => plugin.id === owner)
      || catalog.enabled_plugin_ids && !catalog.enabled_plugin_ids.includes(owner)) {
    throw new Error(`Required Pack ${owner} is not loaded or enabled`);
  }
  if (runtime) {
    const current = catalog.frontend_modules?.[owner];
    if (!current || current.version !== runtime.version || current.url !== runtime.url
        || current.distribution_id !== runtime.distribution_id) throw new Error(`Frontend for ${owner} is no longer active`);
  } else if (catalog.frontend_modules?.[owner]) {
    throw new Error(`Source frontend for ${owner} is no longer active`);
  }
}

export async function loadFrontendPlugin(pluginId: string, runtime?: RuntimeFrontendModule): Promise<FrontendPlugin> {
  assertFrontendAvailable(pluginId, runtime);
  const load = loaders.get(pluginId);
  if (!runtime && !load) throw new Error(`Frontend for ${pluginId} is unavailable. Check its Pack installation and restart OAW.`);
  const { default: plugin } = runtime ? await loadRuntimePlugin(pluginId, runtime) : await load!();
  assertFrontendAvailable(pluginId, runtime);
  if (plugin?.apiVersion !== 1) throw new Error(`Unsupported frontend API for ${pluginId}`);
  return plugin;
}

export function createViewRegistry(sources: typeof loaders, runtimeLoader = loadRuntimePlugin,
  assertAvailable: (owner: string, runtime?: RuntimeFrontendModule) => void = () => {}) {
  const cache = new Map<string, LazyExoticComponent<ComponentType<PluginViewProps>>>();
  return (pluginId: string, view: string, runtime?: RuntimeFrontendModule) => {
    const key = `${pluginId}:${view}:${runtime?.version ?? 'bundled'}:${runtime?.url ?? ''}`;
    if (!cache.has(key)) cache.set(key, lazy(async () => {
      assertAvailable(pluginId, runtime);
      if (runtime) {
        const { default: plugin } = await runtimeLoader(pluginId, runtime);
        if (plugin?.apiVersion !== 1 || typeof plugin.views?.[view] !== "function") throw new Error(`Unsupported or missing Pack view: ${key}`);
        const View = plugin.views[view];
        return { default: (props: PluginViewProps) => { assertAvailable(pluginId, runtime); return createElement(View, props); } };
      }
      const load = sources.get(pluginId);
      if (!load) throw new Error(`Frontend for ${pluginId} is unavailable. Check its Pack installation and restart OAW.`);
      const { default: plugin } = await load();
      if (plugin.apiVersion !== 1 || typeof plugin.views?.[view] !== "function") throw new Error(`Unsupported or missing plugin view: ${key}`);
      const View = plugin.views[view];
      return { default: (props: PluginViewProps) => { assertAvailable(pluginId, runtime); return createElement(View, props); } };
    }));
    return cache.get(key)!;
  };
}
export const pluginView = createViewRegistry(loaders, loadRuntimePlugin, assertFrontendAvailable);
