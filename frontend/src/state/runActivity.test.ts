import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "../types/world";
import { mergeRunActivity, toolFailed, withSavedThinking, withSavedTools, withSavedActivity } from "./runActivity";
import type { SavedModelThinking, SavedInvocation } from "../types/world";

it('repairs directory rows by recorded event/provider identity without duplicating public progress or losing newer thinking', () => {
  const live = { seen: [], truncated: false, items: [
    { id: 'event', type: 'agent_progress' as const, provider_message_id: 'plan', conversation_message_id: 'message', text: 'Plan' },
    { id: 'reasoning', type: 'agent_progress' as const, kind: 'model_reasoning', provider_message_id: 'thought',
      text: 'New thinking', streaming: true, updated_at: '2026-10-19T00:00:03Z' },
  ] };
  const merged = withSavedActivity(live, [
    { id: 'progress:plan', source_event_id: 'event', type: 'agent_progress', provider_message_id: 'plan', text: 'Plan' },
    { id: 'thinking:thought', type: 'agent_progress', kind: 'model_reasoning', provider_message_id: 'thought',
      streaming: true, updated_at: '2026-10-19T00:00:02Z' },
  ]);
  expect(merged.items).toHaveLength(2);
  expect(merged.items[0].conversation_message_id).toBe('message');
  expect(merged.items[1].text).toBe('New thinking');
});

it("merges native tool rows with live previews without duplication or reopening completed calls", () => {
  const row: SavedInvocation = { id: "archive", run_id: "r", role: "builder", call_id: "same", name: "execute",
    state: "started", run_attempt: 0, started_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:01Z" };
  const live = { seen: [], truncated: false, items: [{ id: "live", type: "tool_completed" as const, call_id: "same",
    arguments: { argv: ["python3", "probe.py"] }, response: { exit_code: 0 }, success: true }] };
  const merged = withSavedTools(live, [row]);
  expect(merged.items).toHaveLength(1);
  expect(merged.items[0]).toMatchObject({ id: "live", archive_id: "archive", type: "tool_completed", success: true });
  const restored = withSavedTools(merged, [{ ...row, state: "returned" }]);
  expect(restored.items).toHaveLength(1);
  const retry = withSavedTools(restored, [{ ...row, id: "retry", run_attempt: 1, state: "interrupted" }]);
  expect(retry.items).toHaveLength(2);
  expect(retry.items[1].interrupted).toBe(true);
});
import { activeConversationRuns } from "./conversationActivity";

const run = { run_id: "run-1", tool_trace: [] };

it("keeps newer live thinking and restores separate persisted attempts", () => {
  const row: SavedModelThinking = { run_id: run.run_id, provider_message_id: "thought:1",
    kind: "model_reasoning", text: "old snapshot", text_length: 12, preview_truncated: false,
    streaming: true, interrupted: false, truncated: false, revision: 1,
    created_at: "2026-10-06T01:00:00Z", updated_at: "2026-10-06T01:00:01Z" };
  const live = { seen: [], truncated: false, items: [{ id: "live", type: "agent_progress" as const,
    kind: "model_reasoning", provider_message_id: "thought:1", text: "new live text",
    timestamp: row.created_at, updated_at: "2026-10-06T01:00:02Z" }] };
  const restored = withSavedThinking(live, [row, { ...row, provider_message_id: "thought:1:attempt2", model_attempt: 2 }]);
  expect(restored.items).toHaveLength(2);
  expect(restored.items[0].text).toBe("new live text");
  const terminated = withSavedThinking(restored, [{ ...row, updated_at: "2026-10-06T01:00:03Z",
    streaming: false, interrupted: true, text: "last received tail" }]);
  expect(terminated.items[0].id).toBe("live");
  expect(terminated.items[0].text).toBe("last received tail");
  expect(terminated.items[0].interrupted).toBe(true);
});

it("preserves server trace truncation even without newer events", () => {
  const state = mergeRunActivity(undefined, { ...run, tool_trace_truncated: true }, []);
  expect(state.truncated).toBe(true);
  expect(mergeRunActivity(state, { ...run, tool_trace_truncated: true }, [])).toBe(state);
});
function event(id: number, type: string, payload: Record<string, unknown> = {}): RuntimeEvent {
  return { id: String(id), type, payload, run_id: run.run_id, agent_id: "atlas",
    conversation_id: "room", session_id: "session", timestamp: new Date(1_800_000_000_000 + id).toISOString() };
}

describe("ordered Run activity", () => {
  it('keeps provider-local call IDs separate across specialists and resumed attempts', () => {
    const events = [
      event(1, 'tool_started', { call_id: 'same', name: 'read', role: 'planner', run_attempt: 0, archive_id: 'p' }),
      event(2, 'tool_started', { call_id: 'same', name: 'read', role: 'builder', run_attempt: 0, archive_id: 'b' }),
      event(3, 'tool_completed', { call_id: 'same', name: 'read', role: 'builder', run_attempt: 0, archive_id: 'b', response: 'B' }),
      event(4, 'tool_started', { call_id: 'same', name: 'read', role: 'builder', run_attempt: 1, archive_id: 'retry' }),
    ];
    const state = mergeRunActivity(undefined, run, [...events].reverse());
    expect(state.items).toHaveLength(3);
    expect(state.items.map(item => [item.archive_id, item.type, item.response])).toEqual([
      ['p', 'tool_started', undefined], ['b', 'tool_completed', 'B'], ['retry', 'tool_started', undefined],
    ]);
    const restored = withSavedTools(state, [{ id: 'b', run_id: run.run_id, name: 'read', call_id: 'same', role: 'builder',
      run_attempt: 0, state: 'returned', started_at: events[1].timestamp, updated_at: events[2].timestamp }]);
    expect(restored.items).toHaveLength(3);
    expect(restored.items.find(item => item.archive_id === 'b')?.response).toBe('B');
    const ambiguous = mergeRunActivity(restored, run, [event(5, 'tool_completed', { call_id: 'same', name: 'read' })]);
    expect(ambiguous.items).toHaveLength(4);
  });
  it("keeps interrupted and recovered model attempts separate in recorded events", () => {
    const snapshots = [
      event(1, "agent_progress", { kind: "model_reasoning", role: "structure_builder", model_request: 2,
        model_attempt: 1, provider_message_id: "model-reasoning:structure_builder:2",
        text: "incomplete", interrupted: true }),
      event(2, "agent_progress", { kind: "model_reasoning", role: "structure_builder", model_request: 2,
        model_attempt: 2, provider_message_id: "model-reasoning:structure_builder:2:attempt2",
        text: "recovered", streaming: false }),
    ];
    const activity = mergeRunActivity(undefined, run, snapshots);
    expect(activity.items).toHaveLength(2);
    expect(activity.items[0]).toMatchObject({ model_attempt: 1, interrupted: true });
    expect(activity.items[1]).toMatchObject({ model_attempt: 2, interrupted: false });
    expect(activity.items[0].provider_message_id).not.toBe(activity.items[1].provider_message_id);
  });

  it("shows a model recovery status with its attempt number", () => {
    const activity = mergeRunActivity(undefined, run, [event(1, "agent_progress", {
      kind: "status", phase: "model_recovery", role: "structure_builder",
      model_request: 2, model_attempt: 1, text: "request interrupted; retrying",
    })]);
    expect(activity.items[0]).toMatchObject({ kind: "status", model_attempt: 1 });
  });
  it('pairs concurrent wrappers by call ID and retains identity and legacy failure on reload', () => {
    const events = [
      event(1, 'tool_started', { name: 'oaw_invoke_tool', call_id: 'a', tool_name: 'read_file', arguments: { path: 'a' } }),
      event(2, 'tool_started', { name: 'oaw_invoke_tool', call_id: 'b', tool_name: 'execute_command' }),
      event(3, 'tool_completed', { name: 'oaw_invoke_tool', call_id: 'b', ok: false, response: { error: 'denied' } }),
      event(4, 'tool_completed', { name: 'oaw_invoke_tool', call_id: 'a', response: { content: 'A' } }),
    ];
    const state = mergeRunActivity(undefined, run, [...events].reverse());
    const restored = mergeRunActivity(undefined, { ...run, tool_trace: events.map(event => ({
      ...event.payload, id: event.id, timestamp: event.timestamp, type: event.type })) }, []);
    expect(restored.items).toEqual(state.items);
    expect(state.items[0]).toMatchObject({ tool_name: 'read_file', response: { content: 'A' } });
    expect(state.items[1]).toMatchObject({ tool_name: 'execute_command', success: false });
    expect(toolFailed(state.items[1])).toBe(true);
    expect(toolFailed({ id: 'nested', type: 'tool_completed', response: { ok: true, result: { exit_code: 1 } } })).toBe(true);
  });

  it("interleaves public progress and tools, updating completion in place", () => {
    const state = mergeRunActivity(undefined, run, [
      event(5, "agent_message", { text: "Answer" }),
      event(4, "tool_completed", { call_id: "c", name: "read", response: { content: "file" } }),
      event(3, "agent_progress", { kind: "reasoning_summary", text: "Compare the result" }),
      event(2, "tool_started", { call_id: "c", name: "read", arguments: { path: "notes" } }),
      event(1, "agent_progress", { kind: "status", text: "Check the file" }),
    ]);
    expect(state.items.map(item => item.type)).toEqual(["agent_progress", "tool_completed", "agent_progress", "agent_message"]);
    expect(state.items[1]).toMatchObject({ id: "2", arguments: { path: "notes" }, response: { content: "file" } });
  });

  it("patches snapshots of the same provider message without appending token rows", () => {
    const state = mergeRunActivity(undefined, run, [
      event(3, "agent_message", { text: "Hello world", provider_message_id: "m" }),
      event(2, "agent_message", { text: "Hello", provider_message_id: "m" }),
      event(1, "agent_message", { text: "H", provider_message_id: "m" }),
    ]);
    expect(state.items).toHaveLength(1);
    expect(state.items[0]).toMatchObject({ id: "1", text: "Hello world" });
  });

  it("does not merge distinct messages without provider IDs", () => {
    expect(mergeRunActivity(undefined, run, [event(2, "agent_message", { text: "B" }),
      event(1, "agent_message", { text: "A" })]).items).toHaveLength(2);
  });

  it("retains earlier progress after the socket buffer rolls or REST repairs", () => {
    const first = mergeRunActivity(undefined, run, [event(1, "agent_progress", { text: "First" })]);
    const next = mergeRunActivity(first, run, [event(2, "agent_progress", { text: "Second" })]);
    expect(next.items.map(item => item.text)).toEqual(["First", "Second"]);
    expect(mergeRunActivity(next, run, []).items).toEqual(next.items);
    expect(first.items).toHaveLength(1);
  });

  it("deduplicates a tool event shared by REST and WebSocket", () => {
    const start = event(1, "tool_started", { call_id: "c", name: "read", arguments: { path: "notes" } });
    const trace = [{ ...start.payload, type: start.type, name: "read", id: start.id, timestamp: start.timestamp }];
    const state = mergeRunActivity(undefined, { ...run, tool_trace: trace }, [start]);
    expect(state.items).toHaveLength(1);
    expect(mergeRunActivity(state, { ...run, tool_trace: trace }, [start])).toBe(state);
  });

  it("keeps same-name parallel tools separate by call ID", () => {
    const state = mergeRunActivity(undefined, run, [
      event(3, "tool_completed", { call_id: "b", name: "read", response: "B" }),
      event(2, "tool_started", { call_id: "b", name: "read", arguments: "B" }),
      event(1, "tool_started", { call_id: "a", name: "read", arguments: "A" }),
    ]);
    expect(state.items).toHaveLength(2);
    expect(state.items[0].type).toBe("tool_started");
    expect(state.items[1]).toMatchObject({ type: "tool_completed", response: "B" });
  });

  it("only pairs an unambiguous legacy tool completion", () => {
    const state = mergeRunActivity(undefined, run, [
      event(3, "tool_completed", { name: "read", response: "unknown call" }),
      event(2, "tool_started", { name: "read" }), event(1, "tool_started", { name: "read" }),
    ]);
    expect(state.items).toHaveLength(3);
    expect(state.items[0].response).toBeUndefined();
  });

  it('restores the original call time when an older REST start follows a live completion', () => {
    const done = event(3, 'tool_completed', { name: 'read', call_id: 'c', response: 'done' });
    const state = mergeRunActivity(undefined, run, [done]);
    const start = event(1, 'tool_started', { name: 'read', call_id: 'c', arguments: 'file' });
    const repaired = mergeRunActivity(state, { ...run, tool_trace: [{ ...start.payload, type: start.type,
      timestamp: start.timestamp, id: start.id }] }, []);
    expect(repaired.items).toHaveLength(1);
    expect(repaired.items[0]).toMatchObject({ type: 'tool_completed', timestamp: start.timestamp,
      started_at: start.timestamp, finished_at: done.timestamp, response: 'done', arguments: 'file' });
    expect(state.items[0].timestamp).toBe(done.timestamp);
    const restored = mergeRunActivity(undefined, { ...run, tool_trace: [start, done].map(event => ({
      ...event.payload, id: event.id, type: event.type, timestamp: event.timestamp })) }, []);
    expect(restored.items[0].started_at).toBe(repaired.items[0].started_at);
    expect(restored.items[0].finished_at).toBe(repaired.items[0].finished_at);
  });

  it('reports a truncated durable checkpoint even when there are no new events', () => {
    const state = mergeRunActivity(undefined, run, []);
    expect(mergeRunActivity(state, { ...run, tool_trace_truncated: true }, []).truncated).toBe(true);
    expect(state.truncated).toBe(false);
  });

  it("renders old name-only traces without inventing arguments or results", () => {
    const state = mergeRunActivity(undefined, { ...run, tool_trace: [
      { type: "tool_started", call_id: "c", name: "read" },
      { type: "tool_completed", call_id: "c", name: "read" },
    ] }, []);
    expect(state.items).toHaveLength(1);
    expect(state.items[0].arguments).toBeUndefined();
    expect(state.items[0].response).toBeUndefined();
  });

  it("ignores raw reasoning and another Run's events", () => {
    const state = mergeRunActivity(undefined, run, [
      event(1, "agent_progress", { kind: "raw_reasoning", text: "not public" }),
      { ...event(2, "agent_message", { text: "other" }), run_id: "other" },
    ]);
    expect(state.items).toHaveLength(0);
  });

  it("shows only explicitly labelled AtomSculptor model reasoning", () => {
    const state = mergeRunActivity(undefined, run, [
      event(2, "agent_progress", { kind: "model_reasoning", role: "atom_sculptor",
        model_request: 1, text: "Provider reasoning", truncated: true }),
      event(1, "agent_progress", { kind: "raw_reasoning", text: "not public" }),
    ]);
    expect(state.items).toMatchObject([{ kind: "model_reasoning", role: "atom_sculptor",
      model_request: 1, text: "Provider reasoning", truncated: true }]);
  });

  it("updates one live thinking entry instead of adding a row per chunk", () => {
    const first = mergeRunActivity(undefined, run, [event(1, "agent_progress", {
      kind: "model_reasoning", provider_message_id: "model-reasoning:planner:1",
      text: "First", streaming: true,
    })]);
    const second = mergeRunActivity(first, run, [event(2, "agent_progress", {
      kind: "model_reasoning", provider_message_id: "model-reasoning:planner:1",
      text: "First second", streaming: false,
    })]);
    expect(second.items).toHaveLength(1);
    expect(second.items[0]).toMatchObject({ text: "First second", streaming: false });
  });

  it("closes an interrupted thinking preview without treating it as final", () => {
    const first = mergeRunActivity(undefined, run, [event(1, "agent_progress", {
      kind: "model_reasoning", provider_message_id: "model-reasoning:planner:1",
      text: "Partial thought", streaming: true,
    })]);
    const second = mergeRunActivity(first, run, [event(2, "agent_progress", {
      kind: "model_reasoning", provider_message_id: "model-reasoning:planner:1",
      text: "Partial thought", streaming: false, interrupted: true,
    }), event(3, "agent_progress", {
      kind: "model_stream_interrupted", text: "Stream interrupted", completed_tool_count: 1,
    })]);
    expect(second.items).toHaveLength(2);
    expect(second.items[0]).toMatchObject({ streaming: false, interrupted: true });
    expect(second.items[1]).toMatchObject({ kind: "model_stream_interrupted", completed_tool_count: 1 });
  });

  it("bounds activity and reports truncation", () => {
    const events = Array.from({ length: 1200 }, (_, i) => event(i, "agent_progress", { text: String(i) })).reverse();
    const state = mergeRunActivity(undefined, run, events);
    expect(state.items).toHaveLength(200);
    expect(state.seen).toHaveLength(1024);
    expect(state.truncated).toBe(true);
  });

  it("preserves failure details and recognizes command exit codes", () => {
    const state = mergeRunActivity(undefined, run, [event(2, "tool_completed", {
      name: "command", call_id: "c", response: { exitCode: 1, stderr: "bad command" },
    }), event(1, "tool_started", { name: "command", call_id: "c", arguments: { command: "false" } })]);
    expect(toolFailed(state.items[0])).toBe(true);
    expect(state.items[0].response).toEqual({ exitCode: 1, stderr: "bad command" });
  });

  it("does not resurrect a terminal Run from trailing idle/cleanup events", () => {
    const runs = activeConversationRuns([
      event(4, "agent_status_changed", { status: "idle" }),
      event(3, "agent_message", { text: "late" }),
      event(2, "run_succeeded"), event(1, "run_started"),
    ], "room", "session");
    expect(runs).toEqual([]);
  });
});
