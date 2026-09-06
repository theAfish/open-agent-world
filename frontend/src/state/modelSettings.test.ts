// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import { persistModelSettings, readModelSettings } from "./modelSettings";

describe("model settings credential safety", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("never persists an API key in browser storage", () => {
    sessionStorage.setItem("oaw-litellm-api-key", "legacy-secret");
    persistModelSettings({
      baseUrl: "https://example.test/v1",
      apiKey: "new-secret",
      apiKeyConfigured: true,
      models: ["openai/test"],
    });

    expect(localStorage.getItem("oaw-model-settings")).not.toContain("new-secret");
    expect(sessionStorage.getItem("oaw-litellm-api-key")).toBeNull();
    expect(readModelSettings().apiKey).toBe("");
  });
});
