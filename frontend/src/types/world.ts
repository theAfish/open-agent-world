export type CardType = "agent" | "text" | "image" | "sandbox";

export type AgentStatus = "idle" | "running" | "waiting" | "error";
export type SandboxStatus = "stopped" | "ready" | "running" | "error";
export type CardStatus = AgentStatus | SandboxStatus | "available" | "modified";

export type Relationship =
  | "communicate"
  | "read"
  | "read_edit"
  | "view"
  | "execute"
  | "mount_read_only"
  | "mount_read_write";

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

export const CARD_TYPE_LABELS: Record<CardType, string> = {
  agent: "Agent",
  text: "Text file",
  image: "Image file",
  sandbox: "Sandbox",
};

export const COMPACT_CARD_SIZES: Record<CardType, WorldSize> = {
  agent: { width: 272, height: 178 },
  text: { width: 272, height: 196 },
  image: { width: 248, height: 226 },
  sandbox: { width: 286, height: 190 },
};

export const EXPANDED_CARD_SIZES: Record<CardType, WorldSize> = {
  agent: { width: 438, height: 570 },
  text: { width: 438, height: 570 },
  image: { width: 420, height: 520 },
  sandbox: { width: 466, height: 560 },
};
