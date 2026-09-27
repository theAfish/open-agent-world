import type { ConversationRunSummary, RuntimeEvent } from "../types/world";

function normalizedType(event: RuntimeEvent): string {
  return event.type.replace(/[.\s-]/g, "_").toLowerCase();
}

function scopeValue(event: RuntimeEvent, key: "conversation_id" | "session_id"): string | undefined {
  const direct = event[key];
  if (direct) return direct;
  const nested = event.payload[key];
  return typeof nested === "string" ? nested : undefined;
}

function responderState(event: RuntimeEvent): boolean | undefined {
  const type = normalizedType(event);
  if (type === "agent_status_changed") {
    const status = event.payload.status;
    if (status === "running" || status === "waiting") return true;
    if (status === "idle" || status === "error") return false;
    return undefined;
  }
  if (
    type === "agent_completed"
    || type === "agent_stopped"
    || type === "runtime_error"
    || type === "run_succeeded"
    || type === "run_failed"
    || type === "run_cancelled"
    || type === "run_interrupted"
  ) {
    return false;
  }
  if (
    type === "agent_started"
    || type === "agent_message"
    || type === "tool_started"
    || type === "tool_completed"
    || type === "run_started"
    || type === "run_resumed"
  ) {
    return true;
  }
  return undefined;
}

/** Events are stored newest-first; the first lifecycle state wins per Agent. */
export function activeConversationAgentIds(
  events: RuntimeEvent[],
  conversationId: string,
  sessionId?: string,
  snapshotAgentIds: string[] = [],
): string[] {
  if (!sessionId) return [];
  const resolved = new Map<string, boolean>();
  for (const event of events) {
    if (
      scopeValue(event, "conversation_id") !== conversationId
      || scopeValue(event, "session_id") !== sessionId
      || !event.agent_id
      || resolved.has(event.agent_id)
    ) {
      continue;
    }
    const state = responderState(event);
    if (state !== undefined) resolved.set(event.agent_id, state);
  }
  for (const agentId of snapshotAgentIds) {
    if (!resolved.has(agentId)) resolved.set(agentId, true);
  }
  return [...resolved].filter(([, active]) => active).map(([agentId]) => agentId);
}


function runId(event: RuntimeEvent): string | undefined {
  if (event.run_id) return event.run_id;
  const nested = event.payload.run_id;
  return typeof nested === "string" ? nested : undefined;
}

function eventRun(event: RuntimeEvent): Partial<ConversationRunSummary> | undefined {
  const value = event.payload.run;
  return value && typeof value === "object" ? value as Partial<ConversationRunSummary> : undefined;
}

/** Merge the durable active-Run snapshot with newer websocket-only activity. */
export function activeConversationRuns(
  events: RuntimeEvent[],
  conversationId: string,
  sessionId: string | undefined,
  snapshot: ConversationRunSummary[] = [],
): ConversationRunSummary[] {
  if (!sessionId) return [];
  const runs = new Map(snapshot.map((run) => [run.run_id, { ...run, tool_trace: [...(run.tool_trace ?? [])] }]));
  const ended = new Set<string>();
  for (const event of [...events].reverse()) {
    if (
      scopeValue(event, "conversation_id") !== conversationId
      || scopeValue(event, "session_id") !== sessionId
    ) continue;
    const id = runId(event);
    if (!id || !event.agent_id) continue;
    const type = normalizedType(event);
    if (["run_succeeded", "run_failed", "run_cancelled", "run_interrupted"].includes(type)) {
      runs.delete(id);
      ended.add(id);
      continue;
    }
    // Operational/status cleanup events must not resurrect a terminal Run.
    if (ended.has(id) || !["run_created", "run_started", "run_resumed", "run_waiting",
      "agent_started", "agent_message", "agent_progress", "tool_started", "tool_completed"].includes(type)) continue;
    const durable = eventRun(event);
    const existing = runs.get(id);
    const current: ConversationRunSummary = {
      run_id: id,
      agent_id: event.agent_id,
      status: durable?.status ?? existing?.status ?? "running",
      started_at: durable?.started_at ?? existing?.started_at ?? event.timestamp,
      finished_at: durable?.finished_at ?? existing?.finished_at,
      awaiting: durable?.awaiting ?? existing?.awaiting,
      progress: durable?.progress ?? existing?.progress,
      tool_count: durable?.tool_count ?? existing?.tool_count ?? 0,
      tool_trace: [...(durable?.tool_trace ?? existing?.tool_trace ?? [])],
      live_text: existing?.live_text,
    };
    if (type === "run_waiting") current.status = "waiting";
    if (type === "run_started" || type === "run_resumed" || type === "run_created") current.status = "running";
    if (type === "agent_message" && typeof event.payload.text === "string") {
      current.live_text = event.payload.text;
      current.progress = undefined;
    } else if (type === "agent_progress" && typeof event.payload.text === "string") {
      current.progress = event.payload.text;
    } else if (type === "tool_started") {
      const name = typeof event.payload.name === "string" ? event.payload.name : "tool";
      current.awaiting = name;
      current.tool_count += 1;
      current.tool_trace = [...current.tool_trace, { ...event.payload, id: event.id, timestamp: event.timestamp, type, name,
        ...(typeof event.payload.call_id === "string" ? { call_id: event.payload.call_id } : {}) }].slice(-50);
    } else if (type === "tool_completed") {
      const name = typeof event.payload.name === "string" ? event.payload.name : "tool";
      current.awaiting = undefined;
      current.tool_trace = [...current.tool_trace, { ...event.payload, id: event.id, timestamp: event.timestamp, type, name,
        ...(typeof event.payload.call_id === "string" ? { call_id: event.payload.call_id } : {}) }].slice(-50);
    }
    runs.set(id, current);
  }
  return [...runs.values()];
}
