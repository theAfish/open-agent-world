// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useConversationView } from "../state/conversationView";
import type {
  ConversationMessage,
  ConversationSession,
  ConversationSummary,
  WorldCard,
} from "../types/world";
import { ConversationWorkspace } from "./ConversationWorkspace";
import { WorkspaceSectionProvider, type WorkspaceSectionRegistration } from "../workspace/WorkspaceSection";

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
    useConversationView.setState({ sessions: {}, activeConversationId: undefined });
    vi.spyOn(worldApi, "getConversation").mockResolvedValue({
      conversation_id: card.id,
      sessions: [session],
      agents: [],
    });
    vi.spyOn(worldApi, "getConversationTimeline").mockResolvedValue({ items: [historicalMessage], has_before: false, has_after: false });
  });

  afterEach(() => cleanup());

  it('restores session selection after remount and switches the canvas scope between open conversations', async () => {
    const otherSession = { ...session, id: 'session-2', title: 'Second', group_id: 'group', group_title: 'Research' };
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: card.id, sessions: [{ ...session, group_id: 'group', group_title: 'Research' }, otherSession], agents: [],
    });
    const first = render(<ConversationWorkspace card={card} />);
    fireEvent.click(await screen.findByRole('button', { name: /^Second/ }));
    expect(useConversationView.getState().sessions[card.id]).toBe(otherSession.id);
    first.unmount();
    const restored = render(<ConversationWorkspace card={card} />);
    await waitFor(() => expect(screen.getByRole('button', { name: /^Second/ }).getAttribute('aria-current')).toBe('true'));
    const otherCard = { ...card, id: 'other-room' };
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: otherCard.id, sessions: [{ ...session, conversation_id: otherCard.id }], agents: [],
    });
    const other = render(<ConversationWorkspace card={otherCard} />);
    await waitFor(() => expect(useConversationView.getState().sessions[otherCard.id]).toBe(session.id));
    // A background chat mounting/loading must not take over the active canvas.
    expect(useConversationView.getState().activeConversationId).toBe(card.id);
    fireEvent.pointerDown(other.container.querySelector('.conversation-workspace-grid')!);
    expect(useConversationView.getState().activeConversationId).toBe(otherCard.id);
    fireEvent.pointerDown(restored.container.querySelector('.conversation-workspace-grid')!);
    expect(useConversationView.getState().activeConversationId).toBe(card.id);
    expect(useConversationView.getState().sessions[card.id]).toBe(otherSession.id);
    other.unmount();
  });

  it("creates an inline named group with the only connected agent selected by default", async () => {
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: card.id, sessions: [session],
      agents: [
        { id: "atlas", name: "Atlas", status: "idle", model: "mock", connected: true },
        { id: "offline", name: "Offline", status: "idle", model: "mock", connected: false },
      ],
    });
    const create = vi.spyOn(worldApi, "createConversationSession").mockResolvedValue({
      ...session, id: "new-session", group_id: "new-group", group_title: "Research", participant_ids: ["atlas"],
    });
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.click(screen.getByRole("button", { name: "New group" }));
    const input = screen.getByRole("textbox", { name: "Group name" }) as HTMLInputElement;
    expect(input.value).toBe("New group");
    expect(document.activeElement).toBe(input);
    expect(input.selectionEnd).toBe(input.value.length);
    expect((screen.getByRole("checkbox", { name: "Atlas" }) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByRole("checkbox", { name: "Offline" })).toBeNull();
    expect(screen.queryByText("Create session")).toBeNull();
    fireEvent.change(input, { target: { value: "Research" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(create).toHaveBeenCalledWith(card.id, expect.objectContaining({
      group_title: "Research", participant_ids: ["atlas"],
    })));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Group name" })).toBeNull());
  });

  it("requires a group name and agent selection, retaining the draft on failure and allowing cancellation", async () => {
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: card.id, sessions: [session],
      agents: ["Atlas", "Boreal"].map((name) => ({ id: name, name, status: "idle", model: "mock", connected: true })),
    });
    const create = vi.spyOn(worldApi, "createConversationSession").mockRejectedValue(new Error("Unavailable"));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.click(screen.getByRole("button", { name: "New group" }));
    const input = screen.getByRole("textbox", { name: "Group name" }) as HTMLInputElement;
    const confirm = screen.getByRole("button", { name: "Create group" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Boreal" }));
    fireEvent.change(input, { target: { value: " " } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "My group" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(confirm.disabled).toBe(false));
    expect(create).toHaveBeenCalledWith(card.id, expect.objectContaining({ group_title: "My group", participant_ids: ["Boreal"] }));
    expect(input.value).toBe("My group");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "Group name" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "New group" }));
    fireEvent.click(screen.getByRole("button", { name: "New group" }));
    expect((screen.getByRole("textbox", { name: "Group name" }) as HTMLInputElement).value).toBe("New group");
    expect((screen.getByRole("checkbox", { name: "Boreal" }) as HTMLInputElement).checked).toBe(false);
  });

  it("scopes quiet participant rings to the selected session and refreshes on context events", async () => {
    const participants = ["atlas", "boreal", "plugin"];
    const first = { ...session, participant_ids: participants, group_id: "group" };
    const second = { ...first, id: "session-2", title: "Second topic" };
    const summary: ConversationSummary = {
      conversation_id: card.id, sessions: [first, second],
      agents: participants.map((id) => ({ id, name: id, model: "test", status: "idle", connected: true })),
      context_statuses: {
        [first.id]: {
          atlas: { pressure: .92, state: "high" as const, compaction_count: 1 },
          boreal: { pressure: .3, state: "normal" as const, compaction_count: 0 },
        },
        [second.id]: { atlas: { pressure: .1, state: "normal" as const, compaction_count: 0 } },
      },
    };
    vi.mocked(worldApi.getConversation).mockResolvedValue(summary);
    const view = render(<ConversationWorkspace card={card} />);
    await screen.findByTitle("Context 92% · compacted 1 times");
    expect(view.container.querySelectorAll(".context-pressure-ring")).toHaveLength(2);
    expect(view.container.querySelectorAll(".workspace-message .context-pressure-ring")).toHaveLength(0);
    fireEvent.click(screen.getByTitle("Second topic"));
    expect(screen.getByTitle("Context 10% · compacted 0 times")).toBeTruthy();
    expect(screen.queryByTitle("Context 92% · compacted 1 times")).toBeNull();
    fireEvent.click(screen.getByTitle("General"));
    expect(screen.getByTitle("Context 30% · compacted 0 times")).toBeTruthy();
    vi.mocked(worldApi.getConversation).mockResolvedValue({ ...summary, context_statuses: {
      ...summary.context_statuses, [first.id]: { ...summary.context_statuses![first.id],
        atlas: { pressure: .2, state: "normal", compaction_count: 2 } },
    } });
    const historyCalls = vi.mocked(worldApi.getConversationTimeline).mock.calls.length;
    act(() => useWorldStore.getState().ingestEvent({ id: "context-update", type: "context_status", timestamp: "now",
      conversation_id: card.id, session_id: first.id, agent_id: "atlas", payload: {} }));
    await screen.findByTitle("Context 20% · compacted 2 times");
    expect(screen.getByTitle("Context 30% · compacted 0 times")).toBeTruthy();
    expect(worldApi.getConversationTimeline).toHaveBeenCalledTimes(historyCalls);
    expect(useWorldStore.getState().toasts).toHaveLength(0);
  });

  it("keeps the active conversation and composer alive when detached and returned", async () => {
    const hosts = new Map<string, HTMLDivElement>();
    const register = ({ id, host }: WorkspaceSectionRegistration) => {
      hosts.set(id, host);
      return () => { hosts.delete(id); };
    };
    const noop = () => {};
    const workspace = (detachedSectionIds: Set<string>) => <WorkspaceSectionProvider cardId={card.id}
      editing={false} detachedSectionIds={detachedSectionIds} hiddenSectionIds={new Set()}
      register={register} onSelect={noop} onDragStart={noop} onHide={noop}>
      <ConversationWorkspace card={card} />
    </WorkspaceSectionProvider>;
    const view = render(workspace(new Set()));
    await screen.findByText(historicalMessage.content);
    const composer = screen.getByRole("textbox", { name: "Conversation message" }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Unsent draft" } });
    const historyCalls = vi.mocked(worldApi.getConversationTimeline).mock.calls.length;
    const detached = document.createElement("div");
    view.container.append(detached);

    view.rerender(workspace(new Set(["conversation", "sessions"])));
    detached.append(hosts.get("conversation")!, hosts.get("sessions")!);
    expect(screen.getByRole("textbox", { name: "Conversation message" })).toBe(composer);
    expect(composer.value).toBe("Unsent draft");
    expect(within(detached).getByText(historicalMessage.content)).toBeTruthy();
    expect(within(detached).getByRole("button", { name: "New session" })).toBeTruthy();
    expect(worldApi.getConversationTimeline).toHaveBeenCalledTimes(historyCalls);
    fireEvent.change(composer, { target: { value: "Draft edited outside the card" } });

    view.rerender(workspace(new Set()));
    expect(screen.getByRole("textbox", { name: "Conversation message" })).toBe(composer);
    expect(composer.value).toBe("Draft edited outside the card");
    expect(detached.childElementCount).toBe(0);
  });

  it("shows start and stop events immediately while the history refresh is stalled", async () => {
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: card.id, sessions: [{ ...session, participant_ids: ["atlas"] }],
      agents: [{ id: "atlas", name: "Atlas", status: "idle", model: "mock", connected: true }],
    });
    vi.mocked(worldApi.getConversationTimeline).mockResolvedValue({
      items: [historicalMessage], has_before: false, has_after: false, active_agent_ids: [],
    });
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    const initialCalls = vi.mocked(worldApi.getConversationTimeline).mock.calls.length;
    vi.mocked(worldApi.getConversationTimeline).mockReturnValue(new Promise(() => {}));
    const started = { id: "start", type: "run_started", agent_id: "atlas", conversation_id: card.id,
      session_id: session.id, timestamp: historicalMessage.created_at, payload: {} };
    act(() => useWorldStore.setState({ events: [started] }));
    expect(screen.getByLabelText("Atlas is responding")).toBeTruthy();
    await waitFor(() => expect(worldApi.getConversationTimeline).toHaveBeenCalledTimes(initialCalls + 1));
    act(() => useWorldStore.setState({ events: [{ ...started, id: "stop", type: "run_succeeded" }, started] }));
    expect(screen.queryByLabelText("Atlas is responding")).toBeNull();
  });

  it("sends unmatched @ text to the default participant after a cancelled run", async () => {
    vi.mocked(worldApi.getConversation).mockResolvedValue({
      conversation_id: card.id, sessions: [{ ...session, participant_ids: ["atlas"] }],
      agents: [{ id: "atlas", name: "Atlas", status: "idle", model: "mock", connected: true }],
    });
    const send = vi.spyOn(worldApi, "postConversationMessage").mockImplementation(async (_id, _session, request) => ({
      message: { ...historicalMessage, id: request.message_id!, content: request.content, mention_agent_ids: request.mention_agent_ids ?? [] },
      accepted_agent_ids: ["atlas"],
    }));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    const started = { id: "start", type: "run_started", agent_id: "atlas", conversation_id: card.id,
      session_id: session.id, timestamp: historicalMessage.created_at, payload: {} };
    act(() => useWorldStore.setState({ events: [started] }));
    expect(screen.getByLabelText("Atlas is responding")).toBeTruthy();
    act(() => useWorldStore.setState({ events: [{ ...started, id: "cancel", type: "run_cancelled" }, started] }));
    expect(screen.queryByLabelText("Atlas is responding")).toBeNull();

    const content = "npm install -g @dptech-corp/bohr-cli@latest";
    fireEvent.change(screen.getByLabelText("Conversation message"), { target: { value: content } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(send).toHaveBeenCalledWith(card.id, session.id,
      expect.objectContaining({ content, mention_agent_ids: ["atlas"] })));
    await waitFor(() => expect(screen.queryByText("Sending...")).toBeNull());
  });

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
    fireEvent.click(screen.getByRole("button", { name: "Rename session" }));
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

  it('shows the stop cleanup failure and lets the user send the retained text after recovery', async () => {
    const send = vi.spyOn(worldApi, 'postConversationMessage').mockRejectedValueOnce(new Error('Agent admission is closed until its pending Run cleanup is resolved'));
    send.mockImplementationOnce(async (_id, _session, request) => ({
      message: { ...historicalMessage, id: request.message_id!, content: request.content }, accepted_agent_ids: [],
    }));
    render(<ConversationWorkspace card={card} />);
    await screen.findByText(historicalMessage.content);
    fireEvent.change(screen.getByLabelText('Conversation message'), { target: { value: 'Continue after Stop' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(await screen.findByText('Agent admission is closed until its pending Run cleanup is resolved')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Conversation message'), { target: { value: 'Keep the next draft' } });
    expect((screen.getByRole('button', { name: 'Copy back to composer' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Conversation message'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Copy back to composer' }));
    expect((screen.getByLabelText('Conversation message') as HTMLTextAreaElement).value).toBe('Continue after Stop');
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText('Sending...')).toBeNull());
    expect(screen.queryByText('Send not confirmed. Your text is kept here.')).toBeNull();
    expect(screen.getAllByText('Continue after Stop')).toHaveLength(1);
  });

});
