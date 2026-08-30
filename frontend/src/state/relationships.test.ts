import { describe, expect, it } from "vitest";
import { getRelationshipOptions, validateConnection } from "./relationships";

describe("semantic relationship rules", () => {
  it("offers only the closed permission set for each direction", () => {
    expect(getRelationshipOptions("agent", "text").map((item) => item.value)).toEqual([
      "read",
      "read_edit",
    ]);
    expect(getRelationshipOptions("text", "sandbox").map((item) => item.value)).toEqual([
      "mount_read_only",
      "mount_read_write",
    ]);
    expect(getRelationshipOptions("sandbox", "agent")).toEqual([]);
    expect(getRelationshipOptions("image", "sandbox").map((item) => item.value)).toEqual([
      "mount_read_only",
    ]);
  });

  it("rejects self, reversed, and duplicate relationships", () => {
    expect(validateConnection("a", "a", "agent", "agent").valid).toBe(false);
    expect(validateConnection("s", "a", "sandbox", "agent").valid).toBe(false);
    expect(
      validateConnection("a", "t", "agent", "text", [
        { id: "e", source: "a", target: "t", relationship: "read" },
      ]).valid,
    ).toBe(false);
    expect(validateConnection("a", "t", "agent", "text").valid).toBe(true);
  });
});

