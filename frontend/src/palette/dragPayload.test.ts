import { describe, expect, it } from "vitest";
import { parsePaletteDrag } from "./dragPayload";

describe("palette drag payload", () => {
  it("reads node and Legion entries without embedding graph state", () => {
    expect(parsePaletteDrag(JSON.stringify({ version: 1, kind: "node", type: "vendor.widget" }))).toEqual({
      version: 1,
      kind: "node",
      type: "vendor.widget",
    });
    expect(parsePaletteDrag(JSON.stringify({ version: 1, kind: "legion", id: "legion-1", revision: 4 }))).toEqual({
      version: 1,
      kind: "legion",
      id: "legion-1",
      revision: 4,
    });
    expect(parsePaletteDrag(JSON.stringify({ version: 1, kind: 'legion_instance', id: 'imported-1' }))).toEqual({
      version: 1, kind: 'legion_instance', id: 'imported-1',
    });
  });

  it("rejects malformed and unsupported payload versions", () => {
    expect(parsePaletteDrag("not-json")).toBeUndefined();
    expect(parsePaletteDrag(JSON.stringify({ version: 2, kind: "node", type: "agent" }))).toBeUndefined();
    expect(parsePaletteDrag(JSON.stringify({ version: 1, kind: "legion", id: "legion-1" }))).toBeUndefined();
    expect(parsePaletteDrag(JSON.stringify({ version: 1, kind: 'legion_instance', id: '' }))).toBeUndefined();
    expect(parsePaletteDrag(JSON.stringify({ version: 1, kind: 'legion_instance', id: 123 }))).toBeUndefined();
  });
});
