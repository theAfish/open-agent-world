import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "../types/world";
import { activeConversationAgentIds } from "./conversationActivity";

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
});
