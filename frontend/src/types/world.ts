export type CardType = string;

export type AgentStatus = "idle" | "running" | "waiting" | "error";
export type SandboxStatus = "stopped" | "ready" | "running" | "error";
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
  security?: string;
  revision?: number;
}

export interface WorldCard {
  id: string;
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
  id: CardType;
  label: string;
  description: string;
  icon: string;
  color: string;
  deck_id: string;
  deck_label: string;
  deck_icon: string;
  default_name: string;
  default_size: WorldSize;
  default_status: CardStatus;
  traits: string[];
  surfaces: {
    preview: boolean;
    inspector: boolean;
    workspace: boolean;
  };
  default_config: CardConfig;
}

export interface RelationshipCatalogItem {
  id: Relationship;
  label: string;
  short_label: string;
  description: string;
  source_types: CardType[];
  target_types: CardType[];
  source_traits: string[];
  target_traits: string[];
  directions: EdgeDirection[];
}

export interface PluginCatalog {
  node_types: NodeTypeCatalogItem[];
  relationships: RelationshipCatalogItem[];
}

export interface RuntimeEvent {
  id: string;
  type: string;
  node_id?: string;
  agent_id?: string;
  sandbox_id?: string;
  resource_id?: string;
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
