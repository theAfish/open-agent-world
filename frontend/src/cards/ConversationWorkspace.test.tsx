// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    vi.spyOn(worldApi, "getConversationTimeline").mockResolvedValue({ items: [historicalMessage], has_before: false, has_after: false });
  });

  afterEach(() => cleanup());

  it("uploads and sends an attachment without text, then previews its image", async () => {
    const attachment = { version_id: 'version-1', path: 'plot.png', name: 'plot.png', size_bytes: 12, media_type: 'image/png' };
    vi.spyOn(worldApi, 'uploadConversationAttachment').mockResolvedValue(attachment);
    vi.spyOn(worldApi, 'postConversationMessage').mockImplementation(async (_conversation, _session, input) => ({
      message: { ...historicalMessage, id: input.message_id!, content: '', attachments: [attachment] }, accepted_agent_ids: [],
    }));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.change(screen.getByLabelText('Attach files', { selector: 'input' }), { target: { files: [new File(['image'], 'plot.png', { type: 'image/png' })] } });
    await screen.findByRole('button', { name: 'Remove attachment plot.png' });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(worldApi.postConversationMessage).toHaveBeenCalledWith(card.id, session.id,
      expect.objectContaining({ content: '', attachments: [{ version_id: 'version-1', path: 'plot.png' }] })));
    fireEvent.click(await screen.findByRole('button', { name: 'Preview plot.png' }));
    expect(screen.getByRole('dialog', { name: 'Preview plot.png' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it("does not put an in-flight upload into a different session", async () => {
    const second = { ...session, id: 'session-2', title: 'Second' };
    vi.mocked(worldApi.getConversation).mockResolvedValue({ conversation_id: card.id, sessions: [session, second], agents: [] });
    let finish!: (value: { version_id: string; path: string; name: string; size_bytes: number; media_type: string }) => void;
    vi.spyOn(worldApi, 'uploadConversationAttachment').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.change(screen.getByLabelText('Attach files', { selector: 'input' }), { target: { files: [new File(['private'], 'private.txt')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Second' }));
    await act(async () => finish({ version_id: 'private', path: 'private.txt', name: 'private.txt', size_bytes: 7, media_type: 'text/plain' }));
    expect(screen.queryByRole('button', { name: 'Remove attachment private.txt' })).toBeNull();
  });

  it("loads persisted history while the WebSocket is offline", async () => {
    render(<ConversationWorkspace card={card} />);

    expect(await screen.findByText(historicalMessage.content)).toBeTruthy();
    expect(worldApi.getConversation).toHaveBeenCalledWith(card.id);
    expect(worldApi.getConversationTimeline).toHaveBeenCalledWith(
      card.id,
      session.id,
      {},
    );
  });

  it("refreshes the authoritative snapshot when the socket reconnects", async () => {
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    const initialSummaryCalls = vi.mocked(worldApi.getConversation).mock.calls.length;
    const initialMessageCalls = vi.mocked(worldApi.getConversationTimeline).mock.calls.length;

    useWorldStore.getState().setSocketState("live");

    await waitFor(() => {
      expect(worldApi.getConversation).toHaveBeenCalledTimes(initialSummaryCalls + 1);
      expect(worldApi.getConversationTimeline).toHaveBeenCalledTimes(initialMessageCalls + 1);
    });
  });

  it("restores intermediate messages and tools from durable history after remount", async () => {
    const items: ConversationMessage[] = [historicalMessage,
      { ...historicalMessage, id: "middle", sequence: 2, sender_kind: "agent", content: "Checking the file" },
      { ...historicalMessage, id: "tool", sequence: 3, sender_kind: "agent", kind: "tool_completed", content: "Finished read_file" },
      { ...historicalMessage, id: "final", sequence: 4, sender_kind: "agent", content: "Final answer" },
    ];
    vi.mocked(worldApi.getConversationTimeline).mockResolvedValue({ items, has_before: false, has_after: false });
    render(<ConversationWorkspace card={card} />);
    await screen.findByText("Final answer");
    cleanup();
    useWorldStore.setState({ events: [] });
    render(<ConversationWorkspace card={card} />);
    expect(await screen.findByText("Checking the file")).toBeTruthy();
    expect(screen.getByText("Finished read_file")).toBeTruthy();
    expect(screen.getAllByText("Final answer")).toHaveLength(1);
  });

  it("creates independent sessions within the selected group and renames them", async () => {
    vi.mocked(worldApi.getConversation).mockResolvedValue({ conversation_id: card.id,
      sessions: [{ ...session, group_id: "group-1", group_title: "Research" }], agents: [] });
    const created = { ...session, id: "session-2", title: "New session", group_id: "group-1", group_title: "Research" };
    vi.spyOn(worldApi, "createConversationSession").mockResolvedValue(created);
    vi.spyOn(worldApi, "renameConversationSession").mockResolvedValue({ ...created, title: "Design review" });
    render(<ConversationWorkspace card={card} />);
    await screen.findByText("Research");
    fireEvent.click(screen.getByRole("button", { name: "New session" }));
    await waitFor(() => expect(worldApi.createConversationSession).toHaveBeenCalledWith(card.id,
      expect.objectContaining({ group_id: "group-1", title: "New session" })));
    fireEvent.click(screen.getByLabelText("Session actions for New session"));
    fireEvent.click(within(screen.getByLabelText("Session actions for New session").closest("details")!).getByRole("button", { name: "Rename session" }));
    fireEvent.change(screen.getByLabelText("Session title"), { target: { value: "Design review" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() => expect(worldApi.renameConversationSession).toHaveBeenCalledWith(card.id, "session-2", "Design review"));
  });
  it("shows a bubble before send and naming finish, reconciles its ID, and preserves the next draft", async () => {
    let finish!: (result: { message: ConversationMessage; accepted_agent_ids: string[] }) => void;
    const send = vi.spyOn(worldApi, "postConversationMessage").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.change(screen.getByLabelText("Conversation message"), { target: { value: "Immediate bubble" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(screen.getByText("Immediate bubble")).toBeTruthy();
    expect(screen.getByText("Sending...")).toBeTruthy();
    expect((screen.getByLabelText("Conversation message") as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("Conversation message"), { target: { value: "Next draft" } });
    const saved: ConversationMessage = { ...historicalMessage, id: send.mock.calls[0][2].message_id!, content: "Immediate bubble", sequence: 2 };
    vi.mocked(worldApi.getConversationTimeline).mockResolvedValue({ items: [historicalMessage, saved], has_before: false, has_after: false });
    // The durable notification can arrive before the POST response.
    act(() => useWorldStore.setState({ events: [{ id: "saved", type: "conversation_message", conversation_id: card.id, session_id: session.id, timestamp: saved.created_at, payload: { message: saved } }] }));
    await waitFor(() => expect(screen.queryByText("Sending...")).toBeNull());
    await act(async () => finish({ message: saved, accepted_agent_ids: [] }));
    expect(screen.getAllByText("Immediate bubble")).toHaveLength(1);
    expect((screen.getByLabelText("Conversation message") as HTMLTextAreaElement).value).toBe("Next draft");
  });

  it("keeps the text with an unconfirmed status when sending fails", async () => {
    vi.spyOn(worldApi, "postConversationMessage").mockRejectedValue(new Error("Offline"));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.change(screen.getByLabelText("Conversation message"), { target: { value: "Keep my text" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Send not confirmed. Your text is kept here.")).toBeTruthy();
    expect(screen.getByText("Keep my text")).toBeTruthy();
  });

});
