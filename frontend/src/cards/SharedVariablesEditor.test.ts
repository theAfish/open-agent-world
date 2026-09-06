import { describe, expect, it } from "vitest";
import { newVariable, variablesFromValue, variablesToValue } from "./SharedVariablesEditor";

describe("shared variable values", () => {
  it("preserves mixed data types when editing an existing state", () => {
    const state = { goal: "Report", limit: 3, review: true, disabled: false, options: { locale: "zh" }, tasks: ["a", "b"], empty: null };
    expect(variablesToValue(variablesFromValue(state))).toEqual(state);
  });

  it("rejects blank or duplicate names instead of losing values", () => {
    expect(() => variablesToValue([newVariable()])).toThrow("Give each variable a name");
    const rows = variablesFromValue({ goal: "first" });
    rows.push({ ...newVariable(), name: " goal ", value: "second" });
    expect(() => variablesToValue(rows)).toThrow("appears twice");
  });

  it("validates numeric and JSON values before sending them to the server", () => {
    for (const value of ["", "not a number", "Infinity"]) {
      expect(() => variablesToValue([{ ...newVariable(), name: "limit", type: "number", value }])).toThrow("valid number");
    }
    expect(() => variablesToValue([{ ...newVariable(), name: "options", type: "json", value: "{" }])).toThrow("Check the JSON");
    expect(variablesToValue([{ ...newVariable(), name: "limit", type: "number", value: "0" }])).toEqual({ limit: 0 });
  });
});
