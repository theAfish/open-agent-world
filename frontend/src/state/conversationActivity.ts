import type { RuntimeEvent } from "../types/world";

export interface ConversationLiveUpdate {
  agentId: string;
  runId: string;
  text?: string;
  activity?: string;
  notice?: string;
  tone?: "error" | "info";
}

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

function runId(event: RuntimeEvent): string | undefined {
  if (typeof event.run_id === "string") return event.run_id;
  const nested = event.payload.run_id;
  return typeof nested === "string" ? nested : undefined;
}

/**
 * The runtime stream is plugin-neutral: providers may emit text and/or tool
 * lifecycle events.  Surface the newest useful event per Run while its durable
 * conversation message is still being saved.
 */
export function conversationLiveUpdates(
  events: RuntimeEvent[],
  conversationId: string,
  sessionId?: string,
): ConversationLiveUpdate[] {
  if (!sessionId) return [];
  const updates = new Map<string, ConversationLiveUpdate>();
  const stoppedRuns = new Set<string>();
  for (const event of events) {
    if (
      scopeValue(event, "conversation_id") !== conversationId
      || scopeValue(event, "session_id") !== sessionId
      || !event.agent_id
    ) continue;
    const id = runId(event);
    if (!id) continue;
    const type = normalizedType(event);
    if (type === "runtime_error" || type === "agent_stopped") {
      stoppedRuns.add(id);
      updates.delete(id);
      continue;
    }
    if (stoppedRuns.has(id) || updates.has(id)) continue;
    if (type === "run_failed") {
      const error = typeof event.payload.error === "string" && event.payload.error
        ? event.payload.error
        : "The run failed without an error detail.";
      updates.set(id, { agentId: event.agent_id, runId: id, notice: error, tone: "error" });
    } else if (type === "run_cancelled") {
      updates.set(id, {
        agentId: event.agent_id,
        runId: id,
        notice: "The response was stopped before completion.",
        tone: "info",
      });
    } else if (type === "run_interrupted") {
      updates.set(id, {
        agentId: event.agent_id,
        runId: id,
        notice: "The response was interrupted by a backend restart.",
        tone: "info",
      });
    } else if (type === "agent_message" && typeof event.payload.text === "string") {
      updates.set(id, { agentId: event.agent_id, runId: id, text: event.payload.text });
    } else if (type === "tool_started" || type === "tool_completed") {
      const name = typeof event.payload.name === "string" ? event.payload.name : "tool";
      updates.set(id, {
        agentId: event.agent_id,
        runId: id,
        activity: type === "tool_started" ? `Using ${name}…` : `Finished ${name}.`,
      });
    }
  }
  return [...updates.values()];
}

/** Events are stored newest-first; the first lifecycle state wins per Agent. */
export function activeConversationAgentIds(
  events: RuntimeEvent[],
  conversationId: string,
  sessionId?: string,
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
  return [...resolved].filter(([, active]) => active).map(([agentId]) => agentId);
}
