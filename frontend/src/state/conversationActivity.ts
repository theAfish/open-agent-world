import type { RuntimeEvent } from "../types/world";

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
