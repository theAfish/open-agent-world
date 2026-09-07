import { describe, expect, it } from "vitest";
import { environmentVariablesFromValue, environmentVariablesToValue } from "./ExecutionConfiguration";

describe("execution configuration form values", () => {
  it("round-trips environment values and secret references", () => {
    const value = { variables: { REGION: "test", API_TOKEN: { secret_ref: "api-token" } } };
    expect(environmentVariablesToValue(environmentVariablesFromValue(value))).toEqual(value);
  });

  it("rejects missing and duplicate variable names", () => {
    const rows = environmentVariablesFromValue({ variables: { REGION: "test" } });
    rows.push({ id: 999, name: " REGION ", kind: "value", value: "second" });
    expect(() => environmentVariablesToValue(rows)).toThrow("appears twice");
    expect(() => environmentVariablesToValue([{ id: 1000, name: "", kind: "value", value: "" }])).toThrow("Give each environment variable a name");
  });

  it("requires a name for secret references", () => {
    expect(() => environmentVariablesToValue([{ id: 1001, name: "TOKEN", kind: "secret", value: "" }])).toThrow("secret reference name");
  });

  it("does not silently discard unsupported imported fields", () => {
    expect(() => environmentVariablesFromValue({ variables: {}, typo: true })).toThrow("unsupported field");
    expect(() => environmentVariablesFromValue({ variables: { TOKEN: { secret_ref: "token", value: "unsafe" } } })).toThrow("unsupported field");
  });
});
