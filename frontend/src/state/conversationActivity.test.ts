import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "../types/world";
import { activeConversationAgentIds, conversationLiveUpdates } from "./conversationActivity";

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

  it("exposes the newest provider update for a conversation run", () => {
    const tool = runtimeEvent("1", "tool_started", "atlas", "session-a", { name: "read_file", run_id: "run-1" });
    const text = runtimeEvent("2", "agent_message", "atlas", "session-a", { text: "I found the notes.", run_id: "run-1" });
    expect(conversationLiveUpdates([text, tool], "conversation-a", "session-a")).toEqual([
      { agentId: "atlas", runId: "run-1", text: "I found the notes." },
    ]);
  });

  it("does not retain a live update after a failed or stopped run", () => {
    const failed = runtimeEvent("2", "runtime_error", "atlas", "session-a", { run_id: "run-1" });
    const text = runtimeEvent("1", "agent_message", "atlas", "session-a", { text: "partial", run_id: "run-1" });
    expect(conversationLiveUpdates([failed, text], "conversation-a", "session-a")).toEqual([]);
  });
});

describe("run lifecycle reliability", () => {
  it("surfaces a failed run as an error notice", () => {
    const failed = runtimeEvent("2", "run_failed", "atlas", "session-a", { run_id: "run-1", error: "model endpoint rejected the request" });
    const text = runtimeEvent("1", "agent_message", "atlas", "session-a", { text: "partial", run_id: "run-1" });
    expect(conversationLiveUpdates([failed, text], "conversation-a", "session-a")).toEqual([
      { agentId: "atlas", runId: "run-1", notice: "model endpoint rejected the request", tone: "error" },
    ]);
  });

  it("surfaces a cancelled run as an informational notice", () => {
    const cancelled = runtimeEvent("2", "run_cancelled", "atlas", "session-a", { run_id: "run-1" });
    const text = runtimeEvent("1", "agent_message", "atlas", "session-a", { text: "partial", run_id: "run-1" });
    expect(conversationLiveUpdates([cancelled, text], "conversation-a", "session-a")).toEqual([
      { agentId: "atlas", runId: "run-1", notice: "The response was stopped before completion.", tone: "info" },
    ]);
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
