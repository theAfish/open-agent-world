import type { WorldCard, WorldEdge, WorldPosition } from "./world";

export interface MinisterWorldView {
  nodes: WorldCard[];
  edges: Array<WorldEdge & { external: boolean; derived: boolean }>;
  total: number;
  next_offset: number | null;
  allowed_operations: string[];
  scope: { center: WorldPosition; radius: number };
}

export interface MinisterChat { conversation_id: string; session_id: string }

export interface MinisterProposal {
  id: string;
  status: 'pending' | 'applying' | 'applied' | 'rejected' | 'failed';
  reasons: string[];
  changes: Array<{ id: string | null; name: string; type: string; action: string; configuration?: Record<string, unknown> }>;
  affected_cards: Array<{ id: string; name: string; status: string }>;
  connections: Array<{ source: string; target: string; relationship: string; action: string; description: string }>;
  running_resources: Array<{ id: string; agent_id: string; status: string }>;
  resources: Array<{ card_id?: string; name?: string; kind: string; status?: string; workspace_root?: string | null; runtime?: string; size_bytes?: number; sessions?: Array<{ title: string }> }>;
}
