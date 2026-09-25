import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { FrontendPlugin, PluginViewProps } from "./sdk";
import type { RuntimeFrontendModule } from "../types/world";
import { providePackRuntime } from "./sharedRuntime";

interface Manifest { pluginId: string; apiVersion: number }
const manifests = import.meta.glob<Manifest>(["../../../plugins/*/frontend/plugin.json", "../../../examples/deployed-workspace/plugins/*/frontend/plugin.json"], { eager: true, import: "default" });
const modules = import.meta.glob<{ default: FrontendPlugin }>(["../../../plugins/*/frontend/index.tsx", "../../../examples/deployed-workspace/plugins/*/frontend/index.tsx"]);
const loaders = new Map<string, () => Promise<{ default: FrontendPlugin }>>();
for (const [path, manifest] of Object.entries(manifests)) {
  if (manifest.apiVersion !== 1 || !/^[a-z][a-z0-9._-]*$/.test(manifest.pluginId)) throw new Error(`Invalid frontend plugin manifest: ${path}`);
  if (loaders.has(manifest.pluginId)) throw new Error(`Duplicate frontend plugin: ${manifest.pluginId}`);
  const load = modules[path.replace(/plugin\.json$/, "index.tsx")];
  if (!load) throw new Error(`Missing frontend entry: ${path}`);
  loaders.set(manifest.pluginId, load);
}

export async function loadRuntimePlugin(pluginId: string, module: RuntimeFrontendModule) {
  const prefix = `/api/packs/${encodeURIComponent(pluginId)}/versions/${encodeURIComponent(module.version)}/frontend/`;
  if (module.api_version !== 1 || !module.url.startsWith(prefix)
      || /[?#\\]/.test(module.url) || module.url.split('/').some(part => part === '..' || part === '.')) {
    throw new Error(`Invalid installed frontend location for ${pluginId}`);
  }
  providePackRuntime();
  return await import(/* @vite-ignore */ module.url) as { default: FrontendPlugin };
}

export function createViewRegistry(sources: typeof loaders, runtimeLoader = loadRuntimePlugin) {
  const cache = new Map<string, LazyExoticComponent<ComponentType<PluginViewProps>>>();
  return (pluginId: string, view: string, runtime?: RuntimeFrontendModule) => {
    const key = `${pluginId}:${view}:${runtime?.version ?? 'bundled'}:${runtime?.url ?? ''}`;
    if (!cache.has(key)) cache.set(key, lazy(async () => {
      if (runtime && sources.has(pluginId)) throw new Error(`Conflicting frontend ownership for ${pluginId}`);
      if (runtime) {
        const { default: plugin } = await runtimeLoader(pluginId, runtime);
        if (plugin?.apiVersion !== 1 || typeof plugin.views?.[view] !== "function") throw new Error(`Unsupported or missing Pack view: ${key}`);
        return { default: plugin.views[view] };
      }
      const load = sources.get(pluginId);
      if (!load) throw new Error(`Frontend for ${pluginId} is unavailable. Check its Pack installation and restart OAW.`);
      const { default: plugin } = await load();
      if (plugin.apiVersion !== 1 || typeof plugin.views?.[view] !== "function") throw new Error(`Unsupported or missing plugin view: ${key}`);
      return { default: plugin.views[view] };
    }));
    return cache.get(key)!;
  };
}
export const pluginView = createViewRegistry(loaders);
