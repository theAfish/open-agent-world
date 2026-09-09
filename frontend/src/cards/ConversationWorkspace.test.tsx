// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type {
  ConversationMessage,
  ConversationSession,
  WorldCard,
} from "../types/world";
import { ConversationWorkspace } from "./ConversationWorkspace";

const card: WorldCard = {
  id: "conversation-1",
  type: "conversation",
  name: "Durable room",
  position: { x: 0, y: 0 },
  size: { width: 320, height: 210 },
  expanded: false,
  status: "available",
  config: {},
};

const session: ConversationSession = {
  id: "session-1",
  conversation_id: card.id,
  title: "General",
  participant_ids: [],
  created_at: "2026-09-04T00:00:00Z",
  updated_at: "2026-09-04T00:00:00Z",
  revision: 1,
};

const historicalMessage: ConversationMessage = {
  id: "message-1",
  conversation_id: card.id,
  session_id: session.id,
  sender_kind: "user",
  sender_name: "You",
  content: "Persisted while the socket was offline",
  mention_agent_ids: [],
  created_at: "2026-09-04T00:00:01Z",
};

describe("ConversationWorkspace snapshots", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    useWorldStore.setState({ events: [], socketState: "closed", toasts: [] });
    vi.spyOn(worldApi, "getConversation").mockResolvedValue({
      conversation_id: card.id,
      sessions: [session],
      agents: [],
    });
    vi.spyOn(worldApi, "getConversationMessages").mockResolvedValue([
      historicalMessage,
    ]);
  });

  afterEach(() => cleanup());

  it("loads persisted history while the WebSocket is offline", async () => {
    render(<ConversationWorkspace card={card} />);

    expect(await screen.findByText(historicalMessage.content)).toBeTruthy();
    expect(worldApi.getConversation).toHaveBeenCalledWith(card.id);
    expect(worldApi.getConversationMessages).toHaveBeenCalledWith(
      card.id,
      session.id,
    );
  });

  it("refreshes the authoritative snapshot when the socket reconnects", async () => {
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    const initialSummaryCalls = vi.mocked(worldApi.getConversation).mock.calls.length;
    const initialMessageCalls = vi.mocked(worldApi.getConversationMessages).mock.calls.length;

    useWorldStore.getState().setSocketState("live");

    await waitFor(() => {
      expect(worldApi.getConversation).toHaveBeenCalledTimes(initialSummaryCalls + 1);
      expect(worldApi.getConversationMessages).toHaveBeenCalledTimes(initialMessageCalls + 1);
    });
  });

  it("keeps intermediate bubbles after final persistence and event buffer eviction", async () => {
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: card.id, sessions: [session], agents: [],
    });
    const { container } = render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    const event = (id: string, type: string, payload: Record<string, unknown>) => ({
      id, type, payload, agent_id: "agent-1", run_id: "run-1",
      conversation_id: card.id, session_id: session.id,
      timestamp: `2026-09-04T00:00:0${id}Z`,
    });
    act(() => useWorldStore.setState({ events: [
      event("4", "agent_message", { text: "Final answer" }),
      event("3", "tool_completed", { name: "read_file" }),
      event("2", "agent_message", { text: "Checking the file" }),
    ] }));
    await screen.findByText("Checking the file");
    expect(screen.getByText("Finished read_file.")).toBeTruthy();
    vi.mocked(worldApi.getConversationMessages).mockResolvedValue([
      historicalMessage,
      { ...historicalMessage, id: "final", sender_kind: "agent", run_id: "run-1",
        content: "Final answer", created_at: "2026-09-04T00:00:05Z" },
    ]);
    act(() => useWorldStore.setState({ events: [event("5", "conversation_message", {})] }));
    await waitFor(() => expect(container.querySelector('[data-message-id="final"]')).toBeTruthy());
    expect(screen.getAllByText("Final answer")).toHaveLength(1);
    expect([...container.querySelectorAll(".workspace-message p, .conversation-live-activity")]
      .map((element) => element.textContent)).toEqual([
      historicalMessage.content, "Checking the file", "Finished read_file.", "Final answer",
    ]);
  });

  it("follows messages inside the transcript and preserves a reader's scroll position", async () => {
    const { container } = render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    const transcript = container.querySelector<HTMLElement>(".workspace-transcript")!;
    Object.defineProperties(transcript, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 200 },
    });
    act(() => useWorldStore.setState({ events: [] }));
    expect(transcript.scrollTop).toBe(1000);
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();

    transcript.scrollTop = 100;
    fireEvent.scroll(transcript);
    act(() => useWorldStore.setState({ events: [] }));
    expect(transcript.scrollTop).toBe(100);

    transcript.scrollTop = 800;
    fireEvent.scroll(transcript);
    act(() => useWorldStore.setState({ events: [] }));
    expect(transcript.scrollTop).toBe(1000);
  });
});
