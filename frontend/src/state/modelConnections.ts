import type { ModelSettings } from "./modelSettings";

export const DEFAULT_CONTEXT_WINDOW = 128_000;
export const DEFAULT_MAX_OUTPUT_TOKENS = 8192;
export interface ConfiguredModel {
  id: string; name: string; model_id: string; enabled: boolean;
  // Older catalog snapshots may not have these fields yet.
  context_window?: number; max_output_tokens?: number;
}
export interface ModelConnection {
  id: string; name: string; adapter: "openai" | "anthropic" | "gemini" | "typesafe" | "legacy";
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
  if (!legacy.models.length) return catalog;
  const connection: ModelConnection = catalog.connections[0] ?? {
    id: "legacy", name: "Previous models", adapter: "legacy", base_url: "", enabled: true,
    auth_mode: "api_key", api_key_configured: false, models: [],
  };
  return { ...catalog, connections: [{ ...connection, models: legacy.models.map(model => ({
    id: crypto.randomUUID(), name: model, model_id: model, enabled: true,
    context_window: DEFAULT_CONTEXT_WINDOW, max_output_tokens: DEFAULT_MAX_OUTPUT_TOKENS,
  })) }] };
}

/** Checks saved configuration, without claiming provider connectivity. */
export function hasDefaultModelConfiguration(catalog: ModelCatalog): boolean {
  return hasModelConfiguration(catalog, catalog.default_model);
}

/** An existing Agent can select a saved model independently of the global default. */
export function hasModelConfiguration(catalog: ModelCatalog, selected: unknown): boolean {
  const reference = !selected || selected === 'oaw:default' ? catalog.default_model : selected;
  return catalog.connections.some(c => c.enabled
    && c.models.some(m => m.enabled && modelRef(m.id) === reference)
    && (c.auth_mode === "environment" || (c.auth_mode === "none" ? !!c.base_url : c.api_key_configured)));
}
