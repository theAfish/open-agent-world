import { describe, expect, it } from "vitest";
import { availableModels, importLegacyModels, type ModelCatalog } from "./modelConnections";
import { DEFAULT_MODEL_SETTINGS } from "./modelSettings";

describe("model connection migration", () => {
  it("imports browser model names without changing their adapter semantics or copying credentials", () => {
    const input: ModelCatalog = { revision: 0, default_model: null, connections: [{ id: "legacy", name: "Previous connection",
      adapter: "legacy", base_url: "https://proxy.test/v1", auth_mode: "environment", enabled: true, api_key_configured: true, models: [] }] };
    const draft = importLegacyModels(input, { ...DEFAULT_MODEL_SETTINGS, models: ["openai/custom", "gemini-native"] });
    expect(draft.connections[0]).toMatchObject({ adapter: "legacy", api_key_configured: true, base_url: "https://proxy.test/v1" });
    expect(draft.connections[0].models.map(m => m.model_id)).toEqual(["openai/custom", "gemini-native"]);
    expect(input.connections[0].models).toEqual([]);
    expect(importLegacyModels({ ...draft, revision: 1 }, DEFAULT_MODEL_SETTINGS).connections).toEqual(draft.connections);
  });

  it("keeps identical model names in different connections distinct and excludes disabled entries", () => {
    const draft = importLegacyModels({ revision: 0, connections: [], default_model: null }, DEFAULT_MODEL_SETTINGS);
    const c = draft.connections[0];
    draft.connections.push({ ...c, id: "second", name: "Second", models: c.models.map(m => ({ ...m, id: "second-" + m.id })) });
    expect(new Set(availableModels(draft).map(m => m.value)).size).toBe(c.models.length * 2);
    c.enabled = false;
    expect(availableModels(draft)).toHaveLength(c.models.length);
  });
});
