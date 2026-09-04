// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
});
