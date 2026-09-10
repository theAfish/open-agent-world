export type CardType = string;

export type AgentStatus = "idle" | "running" | "waiting" | "error";
export type SandboxStatus = "stopped" | "ready" | "running" | "error";
export type SandboxWorkspaceAccess = "read_write" | "read_only";

export interface SandboxConfig {
  runtime: string;
  workspace_path: string | null;
  workspace_access: SandboxWorkspaceAccess;
  network_enabled?: boolean;
  memory_bytes?: number;
  active_process_limit?: number;
  command_timeout?: number;
  presets?: Record<string, string>;
}

export interface SandboxRuntime {
  id: string;
  label: string;
  platform: string;
  available: boolean;
  reason: string | null;
  shell: string[];
  supports_workspace: boolean;
  supported_network_modes?: string[];
  network_reason?: string;
  network_available?: boolean;
  network_status?: string;
}

export interface SandboxRuntimeCatalog {
  runtimes: SandboxRuntime[];
  default_runtime: string | null;
}

export interface SandboxInfo {
  sandbox_id: string;
  state: string;
  runtime_id: string | null;
  runtime_locked: boolean;
  platform: string | null;
  shell: string[];
  available: boolean;
  unavailable_reason: string | null;
  workspace_path: string | null;
  workspace_access: SandboxWorkspaceAccess;
  workspace: string | null;
  resources_path: string | null;
  security_boundary: string | null;
  network_enabled?: boolean;
  supported_network_modes?: string[];
  network_reason?: string;
  network_available?: boolean;
  network_status?: string;
}
export type CardStatus = AgentStatus | SandboxStatus | "available" | "modified" | string;

export type Relationship = string;

export type EdgeDirection = "forward" | "bidirectional";

export interface WorldPosition {
  x: number;
  y: number;
}

export interface WorldSize {
  width: number;
  height: number;
}

export interface ModificationRecord {
  at: string;
  summary: string;
  actor?: string;
}

export interface CardConfig extends Record<string, unknown> {
  system_instruction?: string;
  model?: string;
  prompt?: string;
  content?: string;
  preview?: string;
  filename?: string;
  mime_type?: string;
  bytes?: number;
  image_width?: number;
  image_height?: number;
  preview_url?: string;
  history?: ModificationRecord[];
  output?: string[];
  active_command?: string;
  description?: string;
  security?: string;
  revision?: number;
  runtime?: string;
  workspace_path?: string | null;
  workspace_access?: SandboxWorkspaceAccess;
}

export interface WorldCard {
  id: string;
  parent_id?: string | null;
  equipment?: { owner_id: string; relationship: string | null } | null;
  type: CardType;
  name: string;
  position: WorldPosition;
  size: WorldSize;
  /** Legacy transport field; transient UI expansion is owned by nodeSurfaces. */
  expanded: boolean;
  status: CardStatus;
  config: CardConfig;
  created_at?: string;
  updated_at?: string;
  ephemeral?: boolean;
}

export interface WorldEdge {
  id: string;
  source: string;
  target: string;
  relationship: Relationship;
  direction: EdgeDirection;
  created_at?: string;
  updated_at?: string;
}

export interface WorldChunk {
  x: number;
  y: number;
  key?: string;
}

export interface WorldSnapshot {
  nodes: WorldCard[];
  edges: WorldEdge[];
  chunks: Array<WorldChunk | string | [number, number]>;
}

export interface NodeTypeCatalogItem {
  icon_url?: string | null;
  frontend?: Partial<Record<"preview" | "body" | "settings" | "workspace", string>>;
  id: CardType;
  plugin_id: string;
  label: string;
  description: string;
  icon: string;
  color: string;
  deck_id: string;
  deck_label: string;
  deck_icon: string;
  /** Bumped by a plugin when its default card-deck placement changes. */
  deck_revision?: number;
  default_name: string;
  default_size: WorldSize;
  default_status: CardStatus;
  traits: string[];
  surfaces: {
    preview: boolean;
    inspector: boolean;
    workspace: boolean;
  };
  /** Whether this plugin node can be captured inside a reusable Legion. */
  templateable: boolean;
  /** Whether a user may create this node directly from the card library. */
  user_creatable: boolean;
  has_document?: boolean;
  transformations?: Record<string, { label: string; source_traits: string[] }>;
  has_execution?: boolean;
  summoning?: Record<string, never> | null;
  container?: ContainerDefinition | null;
  default_config: CardConfig;
  config_schema?: Record<string, unknown>;
}

export interface RelationshipCatalogItem {
  generated?: boolean;
  id: Relationship;
  plugin_id: string;
  label: string;
  short_label: string;
  description: string;
  source_types: CardType[];
  target_types: CardType[];
  source_traits: string[];
  target_traits: string[];
  directions: EdgeDirection[];
  /** Whether this plugin relationship can be captured inside a reusable Legion. */
  templateable: boolean;
}

export interface PackDefinition {
  id: string; plugin_id: string; name: string; description: string; cards: string[]; compatibility: boolean;
  artwork_asset?: string | null; artwork_url?: string | null; accent_color?: string | null;
}

export interface PluginCatalog {
  packs?: PackDefinition[];
  plugins: Array<{
    id: string;
    version: string;
    plugin_api_version: string;
    name: string | null;
    description: string | null;
  }>;
  node_types: NodeTypeCatalogItem[];
  relationships: RelationshipCatalogItem[];
}

export interface LegionBounds {
  width: number;
  height: number;
}

/**
 * Backend-owned summary for a reusable subgraph template. The serialized node
 * and relationship state intentionally stays behind the API boundary.
 */
export interface LegionSummary {
  id: string;
  name: string;
  description?: string;
  node_count: number;
  edge_count: number;
  bounds: LegionBounds;
  node_types: CardType[];
  plugin_ids: string[];
  compatible: boolean;
  issues: string[];
  created_at?: string;
  updated_at?: string;
  revision: number;
}

export interface LegionInstantiation {
  legion_id: string;
  nodes: WorldCard[];
  edges: WorldEdge[];
}

export interface RuntimeEvent {
  id: string;
  type: string;
  node_id?: string;
  agent_id?: string;
  sandbox_id?: string;
  resource_id?: string;
  run_id?: string;
  conversation_id?: string;
  session_id?: string;
  message?: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface FlowViewportState {
  x: number;
  y: number;
  zoom: number;
  width: number;
  height: number;
}

export interface ToastMessage {
  id: string;
  tone: "neutral" | "success" | "error";
  title: string;
  detail?: string;
}

export interface ConversationSession {
  group_id?: string;
  group_title?: string;
  auto_title?: boolean;
  is_default?: boolean;
  id: string;
  conversation_id: string;
  conversation_name?: string;
  title: string;
  participant_ids: string[];
  created_at: string;
  updated_at: string;
  revision: number;
}

export interface ConversationAgent {
  id: string;
  name: string;
  status: string;
  model: string;
  connected: boolean;
}

export interface ConversationAttachment {
  version_id: string;
  path: string;
  name: string;
  size_bytes: number;
  media_type: string;
}

export interface ConversationMessage {
  attachments?: ConversationAttachment[];
  sequence?: number;
  kind?: string;
  is_final?: boolean;
  id: string;
  conversation_id: string;
  session_id: string;
  sender_kind: "user" | "agent" | "system";
  sender_id?: string;
  sender_name: string;
  content: string;
  mention_agent_ids: string[];
  run_id?: string;
  created_at: string;
}

export interface ConversationSummary {
  conversation_id: string;
  sessions: ConversationSession[];
  agents: ConversationAgent[];
}

export interface ContainerDefinition {
  member_display?: "cards" | "workspace";
  virtual?: boolean;
  member_type?: CardType | null;
  member_traits: string[];
  parentable: boolean;
  connectable: boolean;
  min_size: [number, number];
  content_inset: [number, number, number, number];
  max_members: number;
  document_field: string | null;
}

export interface ConversationMessagePage {
  active_agent_ids?: string[];
  items: ConversationMessage[];
  has_before: boolean;
  has_after: boolean;
}
