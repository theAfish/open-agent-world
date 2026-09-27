import { describe, expect, it } from "vitest";
import { describeRuntimeError } from "./runtimeErrors";

describe("describeRuntimeError", () => {
  it("turns a LiteLLM credential error into an actionable ADK message", () => {
    expect(describeRuntimeError(
      "OpenAIException - Missing credentials. Please set the OPENAI_API_KEY.",
      "openai/gpt-4o-mini",
    )).toEqual({
      title: "Model credentials unavailable",
      detail: "Open Settings → Models to check the API key and selected model, then retry your request.",
    });
  });
});
