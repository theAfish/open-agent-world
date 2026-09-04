import type { NodeTypeCatalogItem, PluginCatalog, RelationshipCatalogItem } from "../types/world";

function node(
  id: string,
  traits: string[],
  overrides: Partial<NodeTypeCatalogItem> = {},
): NodeTypeCatalogItem {
  return {
    id,
    plugin_id: "open-agent-world.core",
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
    templateable: true,
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
    plugin_id: "open-agent-world.core",
    label: id,
    short_label: id,
    description: `${id} relationship`,
    source_types: [],
    target_types: [],
    source_traits: sourceTraits,
    target_traits: targetTraits,
    directions: ["forward"],
    templateable: true,
    ...overrides,
  };
}

export const TEST_CATALOG: PluginCatalog = {
  plugins: [
    {
      id: "open-agent-world.core",
      version: "0.1.0",
      plugin_api_version: "1.0",
      name: "Open Agent World Core",
      description: "Test catalog",
    },
  ],
  node_types: [
    node("agent", ["core.agent"], { default_status: "idle" }),
    node("conversation", ["core.field", "core.conversation"], {
      default_status: "available",
      surfaces: { preview: true, inspector: true, workspace: true },
      templateable: false,
    }),
    node("text", ["core.resource", "core.text"]),
    node("image", ["core.resource", "core.image"]),
    node("sandbox", ["core.sandbox"], { default_status: "stopped" }),
  ],
  relationships: [
    relationship("communicate", ["core.agent"], ["core.agent"], {
      directions: ["forward", "bidirectional"],
    }),
    relationship("participate", ["core.agent"], ["core.conversation"]),
    relationship("read", ["core.agent"], ["core.text"]),
    relationship("read_edit", ["core.agent"], ["core.text"]),
    relationship("view", ["core.agent"], ["core.image"]),
    relationship("execute", ["core.agent"], ["core.sandbox"]),
    relationship("mount_read_only", ["core.resource"], ["core.sandbox"]),
    relationship("mount_read_write", ["core.text"], ["core.sandbox"]),
  ],
};
