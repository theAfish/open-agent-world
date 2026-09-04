import { describe, expect, it } from "vitest";
import { getRelationshipOptions, validateConnection } from "./relationships";
import { TEST_CATALOG } from "./catalog.fixture";

describe("semantic relationship rules", () => {
  it("offers only the closed permission set for each direction", () => {
    expect(getRelationshipOptions(TEST_CATALOG, "agent", "agent").map((item) => item.value)).toEqual([
      "communicate",
    ]);
    expect(getRelationshipOptions(TEST_CATALOG, "agent", "text").map((item) => item.value)).toEqual([
      "read",
      "read_edit",
    ]);
    expect(getRelationshipOptions(TEST_CATALOG, "text", "sandbox").map((item) => item.value)).toEqual([
      "mount_read_only",
      "mount_read_write",
    ]);
    expect(getRelationshipOptions(TEST_CATALOG, "sandbox", "agent")).toEqual([]);
    expect(getRelationshipOptions(TEST_CATALOG, "image", "sandbox").map((item) => item.value)).toEqual([
      "mount_read_only",
    ]);
  });

  it("normalizes reverse drags and rejects self, unsupported, and duplicate pairs", () => {
    expect(validateConnection(TEST_CATALOG, "a", "a", "agent", "agent").valid).toBe(false);
    expect(validateConnection(TEST_CATALOG, "t", "i", "text", "image").valid).toBe(false);

    const reverseDrag = validateConnection(TEST_CATALOG, "s", "a", "sandbox", "agent");
    expect(reverseDrag).toMatchObject({
      valid: true,
      source: "a",
      target: "s",
    });
    expect(reverseDrag.options.map((item) => item.value)).toEqual(["execute"]);

    expect(
      validateConnection(TEST_CATALOG, "t", "a", "text", "agent", [
        { id: "e", source: "a", target: "t", relationship: "read", direction: "forward" },
      ]).valid,
    ).toBe(false);
    expect(validateConnection(TEST_CATALOG, "a", "t", "agent", "text").valid).toBe(true);
  });

  it("matches plugin-defined node traits without adding frontend type branches", () => {
    const pluginCatalog = {
      ...TEST_CATALOG,
      node_types: [
        ...TEST_CATALOG.node_types,
        {
          ...TEST_CATALOG.node_types[1],
          id: "acme.dataset",
          label: "Dataset",
          traits: ["acme.queryable"],
        },
      ],
      relationships: [
        ...TEST_CATALOG.relationships,
        {
          id: "acme.query",
          plugin_id: "acme.dataset",
          label: "Query",
          short_label: "query",
          description: "Query a plugin dataset.",
          source_types: [],
          target_types: [],
          source_traits: ["core.agent"],
          target_traits: ["acme.queryable"],
          directions: ["forward" as const],
          templateable: true,
        },
      ],
    };

    const validation = validateConnection(
      pluginCatalog,
      "dataset",
      "agent",
      "acme.dataset",
      "agent",
    );
    expect(validation).toMatchObject({
      valid: true,
      source: "agent",
      target: "dataset",
    });
    expect(validation.options.map((item) => item.value)).toEqual(["acme.query"]);
  });
});

