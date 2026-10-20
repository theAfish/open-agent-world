import type { ConversationRunSummary, RuntimeEvent } from "../types/world";
import { compareTimestamp } from './activityTiming';

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
  const checkpoints = new Map(snapshot.map(run => [run.run_id, {
    ids: new Set((run.tool_trace ?? []).map(item => item.id).filter(Boolean)),
    through: (run.tool_trace ?? []).map(item => item.timestamp).filter((value): value is string => typeof value === 'string')
      .sort(compareTimestamp).at(-1),
  }]));
  const seen = new Set<string>();
  const ended = new Set<string>();
  for (const event of [...events].reverse()) {
    if (
      scopeValue(event, "conversation_id") !== conversationId
      || scopeValue(event, "session_id") !== sessionId
    ) continue;
    const id = runId(event);
    if (!id || !event.agent_id) continue;
    const type = normalizedType(event);
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    const checkpoint = checkpoints.get(id);
    // The snapshot count already includes its retained trace and earlier calls.
    // Do not replay an old start over a completed tool or count it twice.
    if ((type === 'tool_started' || type === 'tool_completed') && checkpoint &&
      (checkpoint.ids.has(event.id) || (checkpoint.through && event.timestamp &&
        compareTimestamp(event.timestamp, checkpoint.through) < 0))) continue;
    const durable = eventRun(event);
    if (["run_succeeded", "run_failed", "run_cancelled", "run_interrupted"].includes(type)
      || (durable?.status && ["succeeded", "failed", "cancelled", "interrupted"].includes(durable.status))) {
      runs.delete(id);
      ended.add(id);
      continue;
    }
    // Operational/status cleanup events must not resurrect a terminal Run.
    if (ended.has(id) || !["run_created", "run_started", "run_resumed", "run_waiting",
      "agent_started", "agent_message", "agent_progress", "tool_started", "tool_completed"].includes(type)) continue;
    const existing = runs.get(id);
    const current: ConversationRunSummary = {
      run_id: id,
      agent_id: event.agent_id,
      status: durable?.status ?? existing?.status ?? "running",
      started_at: durable?.started_at ?? existing?.started_at ?? event.timestamp,
      finished_at: durable?.finished_at ?? existing?.finished_at,
      awaiting: durable?.awaiting ?? existing?.awaiting,
      progress: durable?.progress ?? existing?.progress,
      recovery_kind: durable?.recovery_kind ?? existing?.recovery_kind,
      recovery_classification: durable?.recovery_classification ?? existing?.recovery_classification,
      recovery_receipts: durable?.recovery_receipts ?? existing?.recovery_receipts,
      confirmed_tool_count: durable?.confirmed_tool_count ?? existing?.confirmed_tool_count,
      tool_count: durable?.tool_count ?? existing?.tool_count ?? 0,
      tool_trace: [...(durable?.tool_trace ?? existing?.tool_trace ?? [])],
      structure_outcomes: durable?.structure_outcomes ?? existing?.structure_outcomes,
      delivery_state: durable?.delivery_state ?? existing?.delivery_state,
      tool_trace_truncated: durable?.tool_trace_truncated ?? existing?.tool_trace_truncated,
      live_text: existing?.live_text,
    };
    if (type === "run_waiting") current.status = "waiting";
    if (type === "run_started" || type === "run_resumed" || type === "run_created") current.status = "running";
    if (type === "agent_message" && typeof event.payload.text === "string") {
      current.live_text = event.payload.text;
      current.progress = undefined;
    } else if (type === "agent_progress" && event.payload.kind !== "model_reasoning"
      && typeof event.payload.text === "string") {
      current.progress = event.payload.text;
      if (event.payload.kind === "model_stream_interrupted") {
        current.recovery_kind = "model_stream_interrupted";
        current.recovery_classification = typeof event.payload.recovery_classification === "string"
          ? event.payload.recovery_classification : undefined;
        current.recovery_receipts = Array.isArray(event.payload.recovery_receipts)
          ? event.payload.recovery_receipts as ConversationRunSummary["recovery_receipts"] : undefined;
        current.confirmed_tool_count = typeof event.payload.completed_tool_count === "number"
          ? event.payload.completed_tool_count : undefined;
      }
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
