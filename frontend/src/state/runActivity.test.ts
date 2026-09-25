import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "../types/world";
import { mergeRunActivity, toolFailed } from "./runActivity";
import { activeConversationRuns } from "./conversationActivity";

const run = { run_id: "run-1", tool_trace: [] };
function event(id: number, type: string, payload: Record<string, unknown> = {}): RuntimeEvent {
  return { id: String(id), type, payload, run_id: run.run_id, agent_id: "atlas",
    conversation_id: "room", session_id: "session", timestamp: new Date(1_800_000_000_000 + id).toISOString() };
}

describe("ordered Run activity", () => {
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
