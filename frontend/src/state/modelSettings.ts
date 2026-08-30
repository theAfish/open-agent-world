export interface ModelSettings {
  baseUrl: string;
  apiKey: string;
  models: string[];
}

export const DEFAULT_MODEL_SETTINGS: ModelSettings = {
  baseUrl: "",
  apiKey: "",
  models: [
    "openai/gpt-4o-mini",
    "openai/gpt-4.1-mini",
    "anthropic/claude-3-5-sonnet",
    "openai/local-model",
  ],
};

const MODEL_SETTINGS_KEY = "oaw-model-settings";
const SESSION_API_KEY = "oaw-litellm-api-key";

export function normalizeModelList(value: string | string[]): string[] {
  const entries = Array.isArray(value) ? value : value.split(/\r?\n/);
  return [...new Set(entries.map((item) => item.trim()).filter(Boolean))].slice(0, 100);
}

export function readModelSettings(): ModelSettings {
  if (typeof window === "undefined") return { ...DEFAULT_MODEL_SETTINGS };
  let stored: Partial<ModelSettings> = {};
  try {
    const raw = window.localStorage.getItem(MODEL_SETTINGS_KEY);
    stored = raw ? JSON.parse(raw) as Partial<ModelSettings> : {};
  } catch {
    stored = {};
  }
  let apiKey = "";
  try {
    apiKey = window.sessionStorage.getItem(SESSION_API_KEY) ?? "";
  } catch {
    apiKey = "";
  }
  const models = normalizeModelList(stored.models ?? DEFAULT_MODEL_SETTINGS.models);
  return {
    baseUrl: typeof stored.baseUrl === "string" ? stored.baseUrl : "",
    apiKey,
    models: models.length > 0 ? models : [...DEFAULT_MODEL_SETTINGS.models],
  };
}

export function persistModelSettings(settings: ModelSettings): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      MODEL_SETTINGS_KEY,
      JSON.stringify({ baseUrl: settings.baseUrl, models: settings.models }),
    );
    if (settings.apiKey) {
      window.sessionStorage.setItem(SESSION_API_KEY, settings.apiKey);
    } else {
      window.sessionStorage.removeItem(SESSION_API_KEY);
    }
  } catch {
    // Settings still remain available in Zustand for this session.
  }
}
