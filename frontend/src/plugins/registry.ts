import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { FrontendPlugin, PluginViewProps } from "./sdk";

interface Manifest { pluginId: string; apiVersion: number }
const manifests = import.meta.glob<Manifest>("../../../plugins/*/frontend/plugin.json", { eager: true, import: "default" });
const modules = import.meta.glob<{ default: FrontendPlugin }>("../../../plugins/*/frontend/index.tsx");
const loaders = new Map<string, () => Promise<{ default: FrontendPlugin }>>();
for (const [path, manifest] of Object.entries(manifests)) {
  if (manifest.apiVersion !== 1 || !/^[a-z][a-z0-9._-]*$/.test(manifest.pluginId)) throw new Error(`Invalid frontend plugin manifest: ${path}`);
  if (loaders.has(manifest.pluginId)) throw new Error(`Duplicate frontend plugin: ${manifest.pluginId}`);
  const load = modules[path.replace(/plugin\.json$/, "index.tsx")];
  if (!load) throw new Error(`Missing frontend entry: ${path}`);
  loaders.set(manifest.pluginId, load);
}

export function createViewRegistry(sources: typeof loaders) {
  const cache = new Map<string, LazyExoticComponent<ComponentType<PluginViewProps>>>();
  return (pluginId: string, view: string) => {
    const key = `${pluginId}:${view}`;
    if (!cache.has(key)) cache.set(key, lazy(async () => {
      const load = sources.get(pluginId);
      if (!load) throw new Error(`Frontend for ${pluginId} is unavailable. Restart or rebuild the frontend with this plugin.`);
      const { default: plugin } = await load();
      if (plugin.apiVersion !== 1 || typeof plugin.views?.[view] !== "function") throw new Error(`Unsupported or missing plugin view: ${key}`);
      return { default: plugin.views[view] };
    }));
    return cache.get(key)!;
  };
}
export const pluginView = createViewRegistry(loaders);
