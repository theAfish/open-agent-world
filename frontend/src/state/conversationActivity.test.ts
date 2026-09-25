import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "../types/world";
import { activeConversationAgentIds, activeConversationRuns } from "./conversationActivity";

function runtimeEvent(
  id: string,
  type: string,
  agentId: string,
  sessionId = "session-a",
  payload: Record<string, unknown> = {},
): RuntimeEvent {
  return {
    id,
    type,
    agent_id: agentId,
    conversation_id: "conversation-a",
    session_id: sessionId,
    timestamp: "2026-09-03T00:00:00Z",
    payload,
  };
}

describe("conversation response activity", () => {
  it("patches live output, progress, and tool activity without a REST snapshot", () => {
    const events = [
      runtimeEvent("4", "agent_progress", "atlas", "session-a", { run_id: "run-1", text: "Checking tests" }),
      runtimeEvent("3", "tool_started", "atlas", "session-a", { run_id: "run-1", name: "read_file" }),
      runtimeEvent("2", "agent_message", "atlas", "session-a", { run_id: "run-1", text: "Working…" }),
      runtimeEvent("1", "run_started", "atlas", "session-a", { run_id: "run-1" }),
    ];
    const runs = activeConversationRuns(events, "conversation-a", "session-a");
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      run_id: "run-1",
      agent_id: "atlas",
      live_text: "Working…",
      progress: "Checking tests",
      tool_count: 1,
      awaiting: "read_file",
    });
  });

  it("shows newer streamed output instead of stale progress", () => {
    const events = [
      runtimeEvent("4", "agent_message", "atlas", "session-a", { run_id: "run-1", text: "Fresh answer" }),
      runtimeEvent("3", "tool_started", "atlas", "session-a", { run_id: "run-1", name: "read_file" }),
      runtimeEvent("2", "agent_progress", "atlas", "session-a", { run_id: "run-1", text: "Running read_file" }),
      runtimeEvent("1", "run_started", "atlas", "session-a", { run_id: "run-1" }),
    ];
    expect(activeConversationRuns(events, "conversation-a", "session-a")[0]).toMatchObject({
      live_text: "Fresh answer",
      progress: undefined,
    });
  });

  it("removes a live run on its terminal event", () => {
    const events = [
      runtimeEvent("2", "run_cancelled", "atlas", "session-a", { run_id: "run-1" }),
      runtimeEvent("1", "run_started", "atlas", "session-a", { run_id: "run-1" }),
    ];
    expect(activeConversationRuns(events, "conversation-a", "session-a")).toEqual([]);
  });

  it("tracks multiple responding agents independently", () => {
    const events = [
      runtimeEvent("2", "agent_status_changed", "river", "session-a", { status: "waiting" }),
      runtimeEvent("1", "agent_started", "atlas"),
    ];
    expect(activeConversationAgentIds(events, "conversation-a", "session-a"))
      .toEqual(["river", "atlas"]);
  });

  it("hides an agent after its newest terminal event while retaining others", () => {
    const events = [
      runtimeEvent("4", "agent_completed", "atlas"),
      runtimeEvent("3", "agent_message", "river"),
      runtimeEvent("2", "agent_started", "atlas"),
      runtimeEvent("1", "agent_started", "river"),
    ];
    expect(activeConversationAgentIds(events, "conversation-a", "session-a"))
      .toEqual(["river"]);
  });

  it("does not leak activity from another session", () => {
    const events = [runtimeEvent("1", "agent_started", "atlas", "session-b")];
    expect(activeConversationAgentIds(events, "conversation-a", "session-a")).toEqual([]);
  });

  it("accepts conversation scope mirrored inside the event payload", () => {
    const event = runtimeEvent("1", "tool_started", "atlas");
    delete event.conversation_id;
    delete event.session_id;
    event.payload = { conversation_id: "conversation-a", session_id: "session-a" };
    expect(activeConversationAgentIds([event], "conversation-a", "session-a"))
      .toEqual(["atlas"]);
  });

  it("stops the responding indicator on run terminal events", () => {
    const events = [
      runtimeEvent("2", "run_failed", "atlas", "session-a", { run_id: "run-1" }),
      runtimeEvent("1", "run_started", "atlas", "session-a", { run_id: "run-1" }),
    ];
    expect(activeConversationAgentIds(events, "conversation-a", "session-a")).toEqual([]);
  });

  it("marks an agent as responding from run start events", () => {
    const events = [runtimeEvent("1", "run_started", "atlas", "session-a", { run_id: "run-1" })];
    expect(activeConversationAgentIds(events, "conversation-a", "session-a")).toEqual(["atlas"]);
  });
});
