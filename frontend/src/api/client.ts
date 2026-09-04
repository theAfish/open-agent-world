import type {
  CardConfig,
  CardStatus,
  CardType,
  ConversationMessage,
  ConversationSession,
  ConversationSummary,
  EdgeDirection,
  LegionInstantiation,
  LegionSummary,
  PluginCatalog,
  Relationship,
  RuntimeEvent,
  WorldCard,
  WorldEdge,
  WorldSnapshot,
} from "../types/world";

export type CardCreateInput = (Omit<WorldCard, "id"> | WorldCard) & {
  content?: string;
  data_base64?: string;
  media_type?: string;
};

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => asRecord(item).msg)
      .filter((message): message is string => typeof message === "string");
    if (messages.length > 0) return messages.join("; ");
  }
  return `Request failed with status ${status}.`;
}

const IMAGE_TYPE_BY_EXTENSION: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
};

function imageMediaType(file: File): string {
  const declared = file.type.toLowerCase().split(";", 1)[0];
  if (Object.values(IMAGE_TYPE_BY_EXTENSION).includes(declared)) return declared;
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  return IMAGE_TYPE_BY_EXTENSION[extension] ?? (declared || "application/octet-stream");
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function normalizeCardType(value: unknown): CardType {
  if (value === "text_file" || value === "text-resource") return "text";
  if (value === "image_file" || value === "image-resource") return "image";
  if (typeof value === "string" && value.trim()) return value;
  throw new ApiError(`Unknown world object type: ${String(value)}`, 500, value);
}

function normalizeRelationship(value: unknown): Relationship {
  const aliases: Record<string, Relationship> = {
    edit: "read_edit",
    read_only: "mount_read_only",
    read_write: "mount_read_write",
  };
  const normalized = aliases[String(value)] ?? value;
  if (typeof normalized === "string" && normalized.trim()) return normalized;
  throw new ApiError(`Unknown relationship: ${String(value)}`, 500, value);
}

export function normalizeCard(input: unknown): WorldCard {
  const source = asRecord(input);
  const position = asRecord(source.position);
  const size = asRecord(source.size);
  const resource = asRecord(source.resource);
  const dimensions = asRecord(resource.dimensions);
  const config = {
    ...asRecord(source.config),
    ...resource,
    system_instruction: asRecord(source.config).system_instruction ?? asRecord(source.config).instruction,
    revision: resource.revision ?? asRecord(source.config).revision ?? asRecord(source.config).version,
    image_width: resource.image_width ?? resource.width ?? dimensions.width ?? asRecord(source.config).image_width,
    image_height: resource.image_height ?? resource.height ?? dimensions.height ?? asRecord(source.config).image_height,
    mime_type: resource.mime_type ?? resource.media_type ?? asRecord(source.config).mime_type,
    bytes: resource.bytes ?? resource.size_bytes ?? asRecord(source.config).bytes,
  } as CardConfig;
  const type = normalizeCardType(source.type ?? source.card_type);
  if (type === "image" && Number(config.revision ?? 0) > 0 && !config.preview_url) {
    config.preview_url = resourceContentUrl(String(source.id));
  }
  return {
    id: String(source.id),
    type,
    name: String(source.name ?? config.filename ?? type),
    position: {
      x: asNumber(position.x ?? source.x, 0),
      y: asNumber(position.y ?? source.y, 0),
    },
    size: {
      width: asNumber(size.width ?? source.width, type === "sandbox" ? 286 : 272),
      height: asNumber(size.height ?? source.height, 190),
    },
    expanded: Boolean(source.expanded),
    status: String(config.status ?? source.status ?? (type === "sandbox" ? "stopped" : type === "agent" ? "idle" : "available")) as CardStatus,
    config,
    created_at: typeof source.created_at === "string" ? source.created_at : undefined,
    updated_at: typeof source.updated_at === "string" ? source.updated_at : undefined,
  };
}

export function normalizeEdge(input: unknown): WorldEdge {
  const source = asRecord(input);
  return {
    id: String(source.id),
    source: String(source.source ?? source.source_id),
    target: String(source.target ?? source.target_id),
    relationship: normalizeRelationship(source.relationship ?? source.permission),
    direction: source.direction === "bidirectional" ? "bidirectional" : "forward",
    created_at: typeof source.created_at === "string" ? source.created_at : undefined,
    updated_at: typeof source.updated_at === "string" ? source.updated_at : undefined,
  };
}

export function normalizeLegionSummary(input: unknown): LegionSummary {
  const source = asRecord(input);
  const bounds = asRecord(source.bounds);
  return {
    id: String(source.id),
    name: String(source.name ?? "Untitled Legion"),
    description: typeof source.description === "string" && source.description.trim()
      ? source.description
      : undefined,
    node_count: asNumber(source.node_count, 0),
    edge_count: asNumber(source.edge_count, 0),
    bounds: {
      width: asNumber(bounds.width, 0),
      height: asNumber(bounds.height, 0),
    },
    node_types: asStringArray(source.node_types),
    plugin_ids: asStringArray(source.plugin_ids),
    compatible: source.compatible === true,
    issues: asStringArray(source.issues),
    created_at: typeof source.created_at === "string" ? source.created_at : undefined,
    updated_at: typeof source.updated_at === "string" ? source.updated_at : undefined,
    revision: asNumber(source.revision, 1),
  };
}

export function normalizeLegionInstantiation(input: unknown): LegionInstantiation {
  const source = asRecord(input);
  const nodes = Array.isArray(source.nodes) ? source.nodes : [];
  const edges = Array.isArray(source.edges) ? source.edges : [];
  return {
    legion_id: String(source.legion_id),
    nodes: nodes.map(normalizeCard),
    edges: edges.map(normalizeEdge),
  };
}

export function normalizeWorldSnapshot(input: unknown): WorldSnapshot {
  const source = asRecord(input);
  const nodes = (source.nodes ?? source.cards ?? []) as unknown[];
  const edges = (source.edges ?? []) as unknown[];
  return {
    nodes: nodes.map(normalizeCard),
    edges: edges.map(normalizeEdge),
    chunks: Array.isArray(source.chunks) ? (source.chunks as WorldSnapshot["chunks"]) : [],
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && init?.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (error) {
    throw new ApiError(
      "The world service is not reachable.",
      0,
      error instanceof Error ? error.message : error,
    );
  }

  const contentType = response.headers.get("content-type") ?? "";
  const body = response.status === 204
    ? undefined
    : contentType.includes("application/json")
      ? await response.json()
      : await response.text();

  if (!response.ok) {
    const bodyRecord = asRecord(body);
    const errorRecord = asRecord(bodyRecord.error);
    const detail = errorRecord.message ?? bodyRecord.detail ?? body;
    throw new ApiError(
      errorMessage(detail, response.status),
      response.status,
      detail,
    );
  }
  return body as T;
}

function unwrap<T>(input: unknown, key: string): T {
  const record = asRecord(input);
  return (record[key] ?? input) as T;
}

export const worldApi = {
  async getCatalog(): Promise<PluginCatalog> {
    return request<PluginCatalog>("/catalog");
  },

  async getWorld(chunks?: string[]): Promise<WorldSnapshot> {
    const query = chunks?.length
      ? `?chunks=${encodeURIComponent(chunks.join(","))}`
      : "";
    const body = await request<unknown>(`/world${query}`);
    return normalizeWorldSnapshot(body);
  },

  async getLegions(): Promise<LegionSummary[]> {
    const body = await request<unknown>("/legions");
    const source = asRecord(body);
    const legions = Array.isArray(body) ? body : Array.isArray(source.legions) ? source.legions : [];
    return legions.map(normalizeLegionSummary);
  },

  async createLegion(input: {
    name: string;
    description?: string;
    node_ids: string[];
  }): Promise<LegionSummary> {
    const body = await request<unknown>("/legions", {
      method: "POST",
      body: JSON.stringify(input),
    });
    return normalizeLegionSummary(unwrap(body, "legion"));
  },

  async deleteLegion(id: string): Promise<LegionSummary> {
    const body = await request<unknown>(`/legions/${encodeURIComponent(id)}`, { method: "DELETE" });
    return normalizeLegionSummary(unwrap(body, "legion"));
  },

  async instantiateLegion(id: string, position: { x: number; y: number }): Promise<LegionInstantiation> {
    const body = await request<unknown>(`/legions/${encodeURIComponent(id)}/instances`, {
      method: "POST",
      body: JSON.stringify({ position }),
    });
    return normalizeLegionInstantiation(body);
  },

  async createNode(node: CardCreateInput): Promise<WorldCard> {
    const payload = {
      ...("id" in node ? { id: node.id } : {}),
      type: node.type,
      name: node.name,
      position: node.position,
      size: node.size,
      expanded: node.expanded,
      status: node.status,
      config: node.config,
      content: node.content,
      data_base64: node.data_base64,
      media_type: node.media_type,
    };
    const body = await request<unknown>("/nodes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return normalizeCard(unwrap(body, "node"));
  },

  async updateNode(
    id: string,
    patch: Partial<Omit<WorldCard, "id" | "type">>,
  ): Promise<WorldCard> {
    const body = await request<unknown>(`/nodes/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    return normalizeCard(unwrap(body, "node"));
  },

  async batchUpdateNodes(updates: Array<{
    node_id: string;
    patch: Partial<Omit<WorldCard, "id" | "type">>;
  }>): Promise<WorldCard[]> {
    const body = await request<unknown>("/nodes/batch-update", {
      method: "POST",
      body: JSON.stringify({ updates }),
    });
    return (Array.isArray(body) ? body : []).map(normalizeCard);
  },

  deleteNode(id: string): Promise<void> {
    return request<void>(`/nodes/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  async deleteNodes(ids: string[]): Promise<WorldCard[]> {
    const body = await request<unknown>("/nodes/batch-delete", {
      method: "POST",
      body: JSON.stringify({ node_ids: ids }),
    });
    return (Array.isArray(body) ? body : []).map(normalizeCard);
  },

  async getTextContent(nodeId: string): Promise<string> {
    const response = await fetch(resourceContentUrl(nodeId));
    if (!response.ok) throw new ApiError("The text resource could not be read.", response.status);
    return response.text();
  },

  async getImageRestoreData(nodeId: string): Promise<{ data_base64: string; media_type: string }> {
    const response = await fetch(resourceContentUrl(nodeId));
    if (!response.ok) throw new ApiError("The image resource could not be read.", response.status);
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = "";
    for (let index = 0; index < bytes.length; index += 8192) {
      binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
    }
    return {
      data_base64: btoa(binary),
      media_type: response.headers.get("content-type") ?? "application/octet-stream",
    };
  },

  async createEdge(input: {
    id?: string;
    source: string;
    target: string;
    relationship: Relationship;
    direction?: EdgeDirection;
  }): Promise<WorldEdge> {
    const body = await request<unknown>("/edges", {
      method: "POST",
      body: JSON.stringify(input),
    });
    return normalizeEdge(unwrap(body, "edge"));
  },

  async updateEdge(
    id: string,
    patch: { relationship?: Relationship; direction?: EdgeDirection },
  ): Promise<WorldEdge> {
    const body = await request<unknown>(`/edges/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    return normalizeEdge(unwrap(body, "edge"));
  },

  deleteEdge(id: string): Promise<void> {
    return request<void>(`/edges/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  async getText(nodeId: string): Promise<{ content: string; revision?: number; history?: unknown[] }> {
    const encodedId = encodeURIComponent(nodeId);
    const [body, historyBody] = await Promise.all([
      request<unknown>(`/resources/${encodedId}/text`),
      request<unknown>(`/resources/${encodedId}/history`),
    ]);
    const source = asRecord(body);
    const history = Array.isArray(historyBody)
      ? historyBody.map((item) => {
          const record = asRecord(item);
          const operation = String(record.operation ?? "modified");
          const revision = typeof record.revision === "number" ? ` · v${record.revision}` : "";
          return {
            at: String(record.created_at ?? new Date().toISOString()),
            summary: `${operation.replaceAll("_", " ")}${revision}`,
            actor: typeof record.actor_id === "string" ? record.actor_id : undefined,
          };
        })
      : [];
    return {
      content: String(source.content ?? ""),
      revision: typeof source.revision === "number" ? source.revision : undefined,
      history,
    };
  },

  async saveText(nodeId: string, content: string, revision?: number): Promise<Record<string, unknown>> {
    const body = await request<unknown>(`/resources/${encodeURIComponent(nodeId)}/text`, {
      method: "PUT",
      body: JSON.stringify({ content, expected_revision: revision }),
    });
    return asRecord(body);
  },

  async uploadImage(nodeId: string, file: File): Promise<Record<string, unknown>> {
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(String(reader.result)));
      reader.addEventListener("error", () => reject(reader.error));
      reader.readAsDataURL(file);
    });
    const body = await request<unknown>(`/resources/${encodeURIComponent(nodeId)}/image`, {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        media_type: imageMediaType(file),
        data_base64: dataUrl.slice(dataUrl.indexOf(",") + 1),
      }),
    });
    return asRecord(body);
  },

  runAgent(nodeId: string, prompt: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/agents/${encodeURIComponent(nodeId)}/run`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
  },

  stopAgent(nodeId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/agents/${encodeURIComponent(nodeId)}/stop`, {
      method: "POST",
    });
  },

  getConversation(conversationId: string): Promise<ConversationSummary> {
    return request<ConversationSummary>(`/conversations/${encodeURIComponent(conversationId)}`);
  },

  createConversationSession(
    conversationId: string,
    input: { title: string; participant_ids: string[] },
  ): Promise<ConversationSession> {
    return request<ConversationSession>(`/conversations/${encodeURIComponent(conversationId)}/sessions`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  addConversationSessionParticipants(
    conversationId: string,
    sessionId: string,
    participantIds: string[],
  ): Promise<ConversationSession> {
    return request<ConversationSession>(
      `/conversations/${encodeURIComponent(conversationId)}/sessions/${encodeURIComponent(sessionId)}/participants`,
      {
        method: "POST",
        body: JSON.stringify({ participant_ids: participantIds }),
      },
    );
  },

  removeConversationSessionParticipant(
    conversationId: string,
    sessionId: string,
    agentId: string,
  ): Promise<ConversationSession> {
    return request<ConversationSession>(
      `/conversations/${encodeURIComponent(conversationId)}/sessions/${encodeURIComponent(sessionId)}/participants/${encodeURIComponent(agentId)}`,
      { method: "DELETE" },
    );
  },

  deleteConversationSession(conversationId: string, sessionId: string): Promise<void> {
    return request<void>(
      `/conversations/${encodeURIComponent(conversationId)}/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
  },

  getConversationMessages(
    conversationId: string,
    sessionId: string,
  ): Promise<ConversationMessage[]> {
    return request<ConversationMessage[]>(
      `/conversations/${encodeURIComponent(conversationId)}/sessions/${encodeURIComponent(sessionId)}/messages`,
    );
  },

  postConversationMessage(
    conversationId: string,
    sessionId: string,
    input: { content: string; mention_agent_ids: string[] },
  ): Promise<{ message: ConversationMessage; accepted_agent_ids: string[] }> {
    return request(`/conversations/${encodeURIComponent(conversationId)}/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  getAgentConversationSessions(agentId: string): Promise<ConversationSession[]> {
    return request<ConversationSession[]>(`/agents/${encodeURIComponent(agentId)}/conversation-sessions`);
  },

  configureLlm(settings: { base_url: string; api_key: string }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/settings/llm", {
      method: "PUT",
      body: JSON.stringify(settings),
    });
  },

  startSandbox(nodeId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/sandboxes/${encodeURIComponent(nodeId)}/start`, {
      method: "POST",
    });
  },

  stopSandbox(nodeId: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/sandboxes/${encodeURIComponent(nodeId)}/stop`, {
      method: "POST",
    });
  },

  executeSandbox(nodeId: string, command: string): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/sandboxes/${encodeURIComponent(nodeId)}/execute`, {
      method: "POST",
      body: JSON.stringify({ command }),
    });
  },
};

export function runtimeWebSocketUrl(): string {
  const configured = import.meta.env.VITE_WS_URL as string | undefined;
  if (configured) return configured;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/events`;
}

export function resourceContentUrl(nodeId: string): string {
  return `${API_BASE}/resources/${encodeURIComponent(nodeId)}/content`;
}

export function normalizeRuntimeEvent(input: unknown): RuntimeEvent {
  const source = asRecord(input);
  const payload = asRecord(source.payload ?? source.data);
  const timestamp = String(source.timestamp ?? source.at ?? new Date().toISOString());
  return {
    id: String(source.id ?? `${timestamp}-${Math.random().toString(36).slice(2, 8)}`),
    type: String(source.type ?? "runtime_event"),
    node_id: typeof source.node_id === "string" ? source.node_id : undefined,
    agent_id: typeof source.agent_id === "string" ? source.agent_id : undefined,
    sandbox_id: typeof source.sandbox_id === "string" ? source.sandbox_id : undefined,
    resource_id: typeof source.resource_id === "string" ? source.resource_id : undefined,
    conversation_id: typeof source.conversation_id === "string" ? source.conversation_id : undefined,
    session_id: typeof source.session_id === "string" ? source.session_id : undefined,
    message: typeof source.message === "string"
      ? source.message
      : typeof payload.message === "string"
        ? payload.message
        : undefined,
    timestamp,
    payload,
  };
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "An unexpected error occurred.";
}
