import type { ConversationRunSummary, RuntimeEvent } from "../types/world";

export interface RunActivityItem {
  id: string;
  type: "agent_message" | "agent_progress" | "tool_started" | "tool_completed";
  timestamp?: string;
  text?: string;
  provider_message_id?: string;
  call_id?: string;
  name?: string;
  arguments?: unknown;
  response?: unknown;
  error?: unknown;
  success?: boolean;
  truncated?: boolean;
}
export interface RunActivityState {
  items: RunActivityItem[];
  seen: string[];
  truncated: boolean;
}
const MAX_ITEMS = 200;
const MAX_SEEN = 1024;
const empty: RunActivityState = { items: [], seen: [], truncated: false };
const types = new Set(["agent_message", "agent_progress", "tool_started", "tool_completed"]);
const text = (value: unknown): string | undefined => typeof value === "string" ? value : undefined;
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** One ordered stream. Only match provider message IDs and tool call IDs;
 * never infer phases, group by tool type, or classify the content with an LLM. */
export function mergeRunActivity(
  previous: RunActivityState | undefined,
  run: Pick<ConversationRunSummary, "run_id" | "tool_trace">,
  events: readonly RuntimeEvent[],
): RunActivityState {
  const state = previous ?? empty;
  const live = events.filter(event => (event.run_id ?? event.payload.run_id) === run.run_id && types.has(event.type));
  const sources = (run.tool_trace ?? []).flatMap((trace, index) => {
    const item = object(trace);
    const type = text(item.type);
    if (!type || !types.has(type)) return [];
    // Older servers supplied name-only tool traces. Prefer the corresponding
    // live event instead of inventing another start row for the same call.
    if (!item.id && live.some(event => event.type === type &&
      (item.call_id ? event.payload.call_id === item.call_id : event.payload.name === item.name))) return [];
    return [{ id: text(item.id) ?? `legacy:${item.call_id ?? index}:${type}`,
      type, timestamp: text(item.timestamp) ?? "", payload: item }];
  });
  sources.push(...[...live].reverse());
  sources.sort((a, b) => a.timestamp && b.timestamp
    ? a.timestamp.localeCompare(b.timestamp) : a.timestamp ? 1 : b.timestamp ? -1 : 0);
  const seen = new Set(state.seen);
  let items = state.items;
  let changed = false;
  for (const event of sources) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    if (!changed) { items = [...items]; changed = true; }
    const payload = event.payload;
    const type = event.type as RunActivityItem["type"];
    const providerId = text(payload.provider_message_id);
    const callId = text(payload.call_id);
    let index = items.findIndex(item => item.id === event.id);
    if (type === "agent_message" || type === "agent_progress") {
      if (type === "agent_progress" && payload.kind !== undefined &&
        !["status", "plan", "reasoning_summary"].includes(String(payload.kind))) continue;
      const content = text(payload.text);
      if (!content?.trim()) continue;
      if (providerId) index = items.findIndex(item => item.type === type && item.provider_message_id === providerId);
      const item: RunActivityItem = { id: event.id, type, timestamp: event.timestamp,
        text: content.slice(0, 100_000), provider_message_id: providerId, truncated: content.length > 100_000 };
      if (index < 0) items.push(item);
      else items[index] = { ...item, id: items[index].id, timestamp: items[index].timestamp };
      continue;
    }
    const name = text(payload.name) ?? "tool";
    if (callId) index = items.findIndex(item => item.type.startsWith("tool_") && item.call_id === callId);
    if (index < 0 && !callId && type === "tool_completed") {
      const pending = items.map((item, i) => ({ item, i })).filter(({ item }) =>
        item.type === "tool_started" && !item.call_id && item.name === name);
      // Without a call ID, pair only an unambiguous legacy start; never attach
      // a result to an arbitrary concurrent call that happens to share a name.
      if (pending.length === 1) index = pending[0].i;
    }
    const existing = index >= 0 ? items[index] : undefined;
    const item: RunActivityItem = {
      ...existing, id: existing?.id ?? event.id,
      timestamp: existing?.timestamp ?? event.timestamp,
      type: existing?.type === "tool_completed" ? "tool_completed" : type,
      name, call_id: callId,
      ...(Object.hasOwn(payload, "arguments") ? { arguments: payload.arguments } : {}),
      ...(Object.hasOwn(payload, "response") ? { response: payload.response } : {}),
      ...(Object.hasOwn(payload, "error") ? { error: payload.error } : {}),
      ...(typeof payload.success === "boolean" ? { success: payload.success } : {}),
      truncated: Boolean(existing?.truncated || payload.truncated),
    };
    if (index < 0) items.push(item);
    else items[index] = item;
  }
  if (!changed) return state;
  // An older REST checkpoint can arrive after a live event. Keep each tool at
  // its original start position while its completion fills in the same row.
  items.sort((a, b) => a.timestamp && b.timestamp ? a.timestamp.localeCompare(b.timestamp) : 0);
  return { items: items.slice(-MAX_ITEMS), seen: [...seen].slice(-MAX_SEEN),
    truncated: state.truncated || items.length > MAX_ITEMS };
}

export function toolFailed(item: RunActivityItem): boolean {
  const response = object(item.response);
  const exitCode = response.exitCode ?? response.exit_code;
  return item.success === false || Boolean(item.error) || Boolean(response.error) ||
    (typeof exitCode === "number" && exitCode !== 0) || response.status === "failed";
}

/** The durable reply owns the final text; keep the live cache untouched.
 * Match only the last assistant row, not earlier commentary or tool output. */
export function withoutFinalReply(
  activity: RunActivityState | undefined,
  finalReply?: string,
): RunActivityState | undefined {
  const finalText = finalReply?.trim();
  if (!activity || !finalText) return activity;
  for (let index = activity.items.length - 1; index >= 0; index -= 1) {
    const item = activity.items[index];
    if (item.type !== "agent_message") continue;
    if (item.text?.trim() !== finalText) return activity;
    return { ...activity, items: activity.items.filter((_, i) => i !== index) };
  }
  return activity;
}
