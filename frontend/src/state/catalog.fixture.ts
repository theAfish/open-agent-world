import type { NodeTypeCatalogItem, PluginCatalog, RelationshipCatalogItem } from "../types/world";

function node(
  id: string,
  traits: string[],
  overrides: Partial<NodeTypeCatalogItem> = {},
): NodeTypeCatalogItem {
  return {
    id,
    label: id,
    description: `${id} node`,
    icon: "puzzle",
    color: "#777777",
    deck_id: "test",
    deck_label: "Test",
    deck_icon: "folder",
    default_name: `New ${id}`,
    default_size: { width: 100, height: 100 },
    default_status: "available",
    traits,
    surfaces: { preview: true, inspector: true, workspace: false },
    default_config: {},
    ...overrides,
  };
}

function relationship(
  id: string,
  sourceTraits: string[],
  targetTraits: string[],
  overrides: Partial<RelationshipCatalogItem> = {},
): RelationshipCatalogItem {
  return {
    id,
    label: id,
    short_label: id,
    description: `${id} relationship`,
    source_types: [],
    target_types: [],
    source_traits: sourceTraits,
    target_traits: targetTraits,
    directions: ["forward"],
    ...overrides,
  };
}

export const TEST_CATALOG: PluginCatalog = {
  node_types: [
    node("agent", ["core.agent"], { default_status: "idle" }),
    node("text", ["core.resource", "core.text"]),
    node("image", ["core.resource", "core.image"]),
    node("sandbox", ["core.sandbox"], { default_status: "stopped" }),
  ],
  relationships: [
    relationship("communicate", ["core.agent"], ["core.agent"], {
      directions: ["forward", "bidirectional"],
    }),
    relationship("read", ["core.agent"], ["core.text"]),
    relationship("read_edit", ["core.agent"], ["core.text"]),
    relationship("view", ["core.agent"], ["core.image"]),
    relationship("execute", ["core.agent"], ["core.sandbox"]),
    relationship("mount_read_only", ["core.resource"], ["core.sandbox"]),
    relationship("mount_read_write", ["core.text"], ["core.sandbox"]),
  ],
};
