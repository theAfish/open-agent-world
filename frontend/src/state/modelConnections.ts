import type { ModelSettings } from "./modelSettings";

export interface ConfiguredModel { id: string; name: string; model_id: string; enabled: boolean }
export interface ModelConnection {
  id: string; name: string; adapter: "openai" | "anthropic" | "gemini" | "legacy";
  base_url: string; enabled: boolean; auth_mode: "api_key" | "none" | "environment";
  environment_variable?: string | null;
  api_key_configured: boolean; models: ConfiguredModel[];
  api_key?: string; clear_api_key?: boolean;
}
export interface ModelCatalog { revision: number; connections: ModelConnection[]; default_model: string | null }
export const EMPTY_MODEL_CATALOG: ModelCatalog = { revision: 0, connections: [], default_model: null };
export const modelRef = (id: string) => `oaw:model:${id}`;
export const availableModels = (catalog: ModelCatalog) => catalog.connections.filter(c => c.enabled)
  .flatMap(c => c.models.filter(m => m.enabled).map(m => ({ value: modelRef(m.id), label: m.name, connection: c.name })));

export function importLegacyModels(catalog: ModelCatalog, legacy: ModelSettings): ModelCatalog {
  if (catalog.revision > 0 || catalog.connections.some(c => c.models.length)) return catalog;
  const connection: ModelConnection = catalog.connections[0] ?? {
    id: "legacy", name: "Previous models", adapter: "legacy", base_url: "", enabled: true,
    auth_mode: "environment", api_key_configured: false, models: [],
  };
  return { ...catalog, connections: [{ ...connection, models: legacy.models.map(model => ({
    id: crypto.randomUUID(), name: model, model_id: model, enabled: true,
  })) }] };
}
