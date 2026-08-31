import { describe, expect, it } from "vitest";
import { describeRuntimeError } from "./runtimeErrors";

describe("describeRuntimeError", () => {
  it("turns a LiteLLM credential error into an actionable ADK message", () => {
    expect(describeRuntimeError(
      "OpenAIException - Missing credentials. Please set the OPENAI_API_KEY.",
      "openai/gpt-4o-mini",
    )).toEqual({
      title: "Model credentials unavailable",
      detail: "ADK could not authenticate openai/gpt-4o-mini. Set OPENAI_API_KEY in the terminal that runs scripts/dev.ps1, then restart the development server.",
    });
  });
});
