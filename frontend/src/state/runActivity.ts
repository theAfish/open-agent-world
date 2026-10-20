import type { ConversationRunSummary, RuntimeEvent, SavedModelThinking, SavedInvocation } from "../types/world";
import { compareTimestamp } from './activityTiming';

export interface RunActivityItem {
  id: string;
  type: "agent_message" | "agent_progress" | "tool_started" | "tool_completed";
  timestamp?: string;
  updated_at?: string;
  preview_truncated?: boolean;
  kind?: string;
  role?: string;
  model_request?: number;
  model_attempt?: number;
  started_at?: string;
  finished_at?: string;
  run_attempt?: number;
  text?: string;
  provider_message_id?: string;
  conversation_message_id?: string;
  call_id?: string;
  archive_id?: string;
  activity_id?: string;
  source_event_id?: string;
  name?: string;
  tool_name?: string;
  capability_id?: string;
  author?: string;
  subagent?: boolean;
  response_visibility?: string;
  arguments?: unknown;
  response?: unknown;
  error?: unknown;
  success?: boolean;
  truncated?: boolean;
  streaming?: boolean;
  interrupted?: boolean;
  completed_tool_count?: number;
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
const progressKinds = new Set(['status', 'plan', 'reasoning_summary', 'model_reasoning', 'model_stream_retry', 'model_stream_interrupted']);

/** Runtime ticks carry lifecycle/context data, not transcript content. The same
 * rule applies to live events and restored metadata, before grouping or markup. */
export function visibleActivity(item: RunActivityItem): boolean {
  if (item.type === 'tool_started' || item.type === 'tool_completed') return true;
  if (item.type === 'agent_progress') {
    if (item.kind && !progressKinds.has(item.kind)) return false;
    if (item.kind === 'model_reasoning' && item.provider_message_id) return true;
  }
  return Boolean(item.text?.trim());
}
const text = (value: unknown): string | undefined => typeof value === "string" ? value : undefined;
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

type ToolIdentity = Pick<RunActivityItem, 'archive_id' | 'call_id' | 'role' | 'run_attempt'>;

/** Call IDs are provider-local, so roles and retry attempts must agree too.
 * A legacy event without scope can only match one compatible call. */
function toolIndex(items: RunActivityItem[], target: ToolIdentity): number {
  if (target.archive_id) {
    const exact = items.findIndex(item => item.archive_id === target.archive_id);
    if (exact >= 0) return exact;
  }
  if (!target.call_id) return -1;
  const candidates = items.flatMap((item, index) => item.type.startsWith('tool_') &&
    item.call_id === target.call_id &&
    !(item.archive_id && target.archive_id && item.archive_id !== target.archive_id) &&
    !(item.role && target.role && item.role !== target.role) &&
    !(item.run_attempt !== undefined && target.run_attempt !== undefined && item.run_attempt !== target.run_attempt)
    ? [index] : []);
  return candidates.length === 1 ? candidates[0] : -1;
}

/** One ordered stream. Only match provider message IDs and scoped tool IDs;
 * never infer phases, group by tool type, or classify the content with an LLM. */
export function mergeRunActivity(
  previous: RunActivityState | undefined,
  run: Pick<ConversationRunSummary, "run_id" | "tool_trace" | "tool_trace_truncated">,
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
      (item.call_id ? event.payload.call_id === item.call_id &&
        (!item.role || !event.payload.role || event.payload.role === item.role) &&
        (item.run_attempt === undefined || event.payload.run_attempt === undefined || event.payload.run_attempt === item.run_attempt)
        : event.payload.name === item.name))) return [];
    return [{ id: text(item.id) ?? `legacy:${item.call_id ?? index}:${type}`,
      type, timestamp: text(item.timestamp) ?? "", payload: item }];
  });
  sources.push(...[...live].reverse());
  sources.sort((a, b) => a.timestamp && b.timestamp
    ? compareTimestamp(a.timestamp, b.timestamp) : a.timestamp ? 1 : b.timestamp ? -1 : 0);
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
      if (type === "agent_progress" && payload.kind !== undefined && !progressKinds.has(String(payload.kind))) continue;
      const content = text(payload.text);
      if (!content?.trim()) continue;
      if (providerId) index = items.findIndex(item => item.type === type && item.provider_message_id === providerId);
      const item: RunActivityItem = { id: event.id, type, timestamp: event.timestamp,
        updated_at: event.timestamp,
        kind: text(payload.kind), role: text(payload.role),
        model_request: typeof payload.model_request === "number" ? payload.model_request : undefined,
        model_attempt: typeof payload.model_attempt === "number" ? payload.model_attempt : undefined,
        text: content.slice(0, 100_000), provider_message_id: providerId,
        truncated: Boolean(payload.truncated) || content.length > 100_000,
        streaming: payload.streaming === true,
        interrupted: payload.interrupted === true,
        completed_tool_count: typeof payload.completed_tool_count === "number" ? payload.completed_tool_count : undefined,
        conversation_message_id: text(payload.conversation_message_id) };
      if (index < 0) items.push(item);
      else items[index] = { ...item, id: items[index].id, timestamp: items[index].timestamp };
      continue;
    }
    const name = text(payload.name) ?? "tool";
    if (callId) index = toolIndex(items, { call_id: callId, archive_id: text(payload.archive_id),
      role: text(payload.role), run_attempt: typeof payload.run_attempt === 'number' ? payload.run_attempt : undefined });
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
      timestamp: existing?.timestamp && event.timestamp
        ? (compareTimestamp(existing.timestamp, event.timestamp) < 0 ? existing.timestamp : event.timestamp)
        : existing?.timestamp || event.timestamp,
      updated_at: event.timestamp,
      type: existing?.type === "tool_completed" ? "tool_completed" : type,
      ...(type === 'tool_started' && event.timestamp ? { started_at: existing?.started_at ?? event.timestamp } : {}),
      ...(type === 'tool_completed' && event.timestamp ? { finished_at: existing?.finished_at ?? event.timestamp } : {}),
      name, call_id: callId,
      archive_id: text(payload.archive_id) ?? existing?.archive_id,
      activity_id: text(payload.activity_id) ?? existing?.activity_id,
      ...(typeof payload.tool_name === 'string' ? { tool_name: payload.tool_name } : {}),
      ...(typeof payload.capability_id === 'string' ? { capability_id: payload.capability_id } : {}),
      ...(typeof payload.author === 'string' ? { author: payload.author } : {}),
      ...(typeof payload.subagent === 'boolean' ? { subagent: payload.subagent } : {}),
      ...(typeof payload.response_visibility === 'string' ? { response_visibility: payload.response_visibility } : {}),
      role: text(payload.role) ?? existing?.role,
      run_attempt: typeof payload.run_attempt === "number" ? payload.run_attempt : existing?.run_attempt,
      ...(Object.hasOwn(payload, "arguments") ? { arguments: payload.arguments } : {}),
      ...(Object.hasOwn(payload, "response") ? { response: payload.response } : {}),
      ...(Object.hasOwn(payload, "error") ? { error: payload.error } : {}),
      ...(typeof payload.success === "boolean" ? { success: payload.success }
        : typeof payload.ok === 'boolean' ? { success: payload.ok } : {}),
      truncated: Boolean(existing?.truncated || payload.truncated),
    };
    if (index < 0) items.push(item);
    else items[index] = item;
  }
  if (!changed && (!run.tool_trace_truncated || state.truncated)) return state;
  // An older REST checkpoint can arrive after a live event. Keep each tool at
  // its original start position while its completion fills in the same row.
  items = [...items].sort((a, b) => a.timestamp && b.timestamp ? compareTimestamp(a.timestamp, b.timestamp) : 0);
  return { items: items.slice(-MAX_ITEMS), seen: [...seen].slice(-MAX_SEEN),
    truncated: state.truncated || Boolean(run.tool_trace_truncated) || items.length > MAX_ITEMS };
}

/** Restore persisted thinking without replacing a newer live observation. */
export function withSavedThinking(activity: RunActivityState | undefined, rows: SavedModelThinking[]): RunActivityState {
  const state = activity ?? empty;
  const items = [...state.items];
  for (const row of rows) {
    const index = items.findIndex(item => item.kind === "model_reasoning" && item.provider_message_id === row.provider_message_id);
    const live = index >= 0 ? items[index] : undefined;
    if (live && compareTimestamp(live.updated_at ?? live.timestamp ?? "", row.updated_at) >= 0) continue;
    const item: RunActivityItem = { ...row, id: live?.id ?? `saved:${row.provider_message_id}`,
      type: "agent_progress", timestamp: row.created_at };
    if (index < 0) items.push(item);
    else items[index] = item;
  }
  items.sort((a, b) => compareTimestamp(a.timestamp ?? "", b.timestamp ?? ""));
  return { ...state, items };
}

export function toolFailed(item: RunActivityItem): boolean {
  const response = object(item.response);
  const result = object(response.result);
  const failed = (value: Record<string, unknown>) => {
    const exitCode = value.exitCode ?? value.exit_code;
    return value.ok === false || value.success === false || Boolean(value.error) ||
      (typeof exitCode === 'number' && exitCode !== 0) || ['failed', 'error'].includes(String(value.status));
  };
  return item.success === false || Boolean(item.error) || failed(response) || failed(result);
}

/** A bounded archive directory repairs missed live rows without discarding
 * newer websocket text or moving calls when their result arrives. */
export function withSavedActivity(activity: RunActivityState | undefined, rows: RunActivityItem[], limit = MAX_ITEMS): RunActivityState {
  const state = activity ?? empty;
  const items = state.items.filter(visibleActivity);
  for (const row of rows) {
    if (!visibleActivity(row)) continue;
    const eventIndex = items.findIndex(item => item.id === row.id || item.id === row.source_event_id);
    const index = eventIndex >= 0 ? eventIndex : row.kind === 'model_reasoning'
      ? items.findIndex(item => item.kind === row.kind && item.provider_message_id === row.provider_message_id)
      : row.type.startsWith('tool_') ? toolIndex(items, row)
      : items.findIndex(item => item.type === row.type && (
          item.provider_message_id === row.provider_message_id && Boolean(row.provider_message_id)
          || item.activity_id === row.activity_id && Boolean(row.activity_id)));
    const live = index >= 0 ? items[index] : undefined;
    const newer = live && compareTimestamp(live.updated_at ?? live.timestamp ?? '', row.updated_at ?? row.timestamp ?? '') >= 0;
    const merged = live ? { ...(newer ? row : live), ...(newer ? live : row), id: live.id,
      text: live.text ?? row.text, timestamp: live.timestamp || row.timestamp } : row;
    if (index < 0) items.push(merged); else items[index] = merged;
  }
  items.sort((a, b) => compareTimestamp(a.timestamp ?? '', b.timestamp ?? ''));
  return { ...state, items: items.slice(-limit), truncated: state.truncated || items.length > limit };
}

/** Native archival identities keep SDK tools and old trace previews in one row. */
export function withSavedTools(activity: RunActivityState, rows: SavedInvocation[]): RunActivityState {
  const items = [...activity.items];
  for (const row of rows) {
    const index = toolIndex(items, { archive_id: row.id, call_id: row.call_id, role: row.role, run_attempt: row.run_attempt });
    const live = index >= 0 ? items[index] : undefined;
    const newer = live && compareTimestamp(live.updated_at ?? live.timestamp ?? "", row.updated_at) > 0;
    const item: RunActivityItem = { ...live, id: live?.id ?? `saved:${row.id}`, archive_id: row.id,
      call_id: row.call_id, name: row.name, role: row.role, run_attempt: row.run_attempt,
      timestamp: row.started_at, updated_at: newer ? live.updated_at : row.updated_at,
      type: newer ? live.type : row.state === "started" ? live?.type ?? "tool_started" : "tool_completed",
      arguments: live?.arguments ?? row.arguments_preview,
      interrupted: newer ? live.interrupted : row.state === "interrupted",
      success: newer || row.state === "started" || row.state === "returned" ? live?.success : false };
    if (index < 0) items.push(item); else items[index] = item;
  }
  items.sort((a, b) => compareTimestamp(a.timestamp ?? "", b.timestamp ?? ""));
  return { ...activity, items };
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
    if (item.conversation_message_id) return activity;
    if (item.text?.trim() !== finalText) return activity;
    return { ...activity, items: activity.items.filter((_, i) => i !== index) };
  }
  return activity;
}
