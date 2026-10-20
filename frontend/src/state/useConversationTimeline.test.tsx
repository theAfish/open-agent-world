// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import type { ConversationMessage, ConversationMessagePage, RuntimeEvent } from "../types/world";
import { useConversationTimeline } from "./useConversationTimeline";
const messages: ConversationMessage[] = Array.from({ length: 420 }, (_, i) => ({
  id: String(i + 1), sequence: i + 1, conversation_id: "room", session_id: "a",
  sender_kind: "agent", sender_name: "Atlas", content: `Message ${i + 1}`, mention_agent_ids: [], created_at: "2026-09-09T00:00:00Z",
}));
function Harness({ session = "a", refresh = "", events = [], socketLive = true }: { session?: string; refresh?: string; events?: RuntimeEvent[]; socketLive?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const history = useConversationTimeline("room", session, refresh, socketLive, ref, events);
  return <><button onClick={history.loadOlder}>Older</button><button onClick={history.loadNewer}>Newer</button>
    <div ref={ref} onScroll={history.onScroll}>{history.messages.map((m) => <p key={m.id} data-message-id={m.id}>{m.content}</p>)}</div>
    <output data-testid="active">{history.activeAgentIds.join(",")}</output>
    <output data-testid="summaries">{Object.keys(history.runSummaries).sort().join(',')}</output>
    <output data-testid="has-before">{String(history.hasBefore)}</output>
    <span>{history.loading ? "busy" : "ready"}</span></>;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('silently polls an empty session while retaining first-load and session-switch loading', async () => {
  vi.useFakeTimers();
  const empty = { items: [], has_before: false, has_after: false };
  let finish!: (page: ConversationMessagePage) => void;
  const fetch = vi.spyOn(worldApi, 'getConversationTimeline')
    .mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const { rerender } = render(<Harness socketLive={false} />);
  expect(screen.getByText('busy')).toBeTruthy();
  await act(async () => finish(empty));
  expect(screen.getByText('ready')).toBeTruthy();
  for (let i = 0; i < 3; i++) {
    await act(async () => vi.advanceTimersByTime(3000));
    expect(fetch).toHaveBeenCalledTimes(i + 2);
    expect(screen.queryByText('busy')).toBeNull();
    await act(async () => finish(empty));
  }
  rerender(<Harness session="b" socketLive={false} />);
  expect(screen.getByText('busy')).toBeTruthy();
  await act(async () => finish(empty));
  expect(screen.getByText('ready')).toBeTruthy();
});

it('keeps older messages reachable when an empty session gains a full tail between refreshes', async () => {
  const fetch = vi.spyOn(worldApi, 'getConversationTimeline')
    .mockResolvedValue({ items: [], has_before: false, has_after: false });
  const { rerender } = render(<Harness />);
  await screen.findByText('ready');
  fetch.mockResolvedValue({ items: messages.slice(-50), has_before: true, has_after: false });
  rerender(<Harness refresh="new-tail" />);
  await screen.findByText('Message 420');
  expect(screen.getByTestId('has-before').textContent).toBe('true');
  fetch.mockResolvedValue({ items: messages.slice(-100, -50), has_before: true, has_after: true });
  fireEvent.click(screen.getByText('Older'));
  await screen.findByText('Message 321');
  expect(fetch).toHaveBeenLastCalledWith('room', 'a', { before: 371 });
});
it('keeps summaries for retained messages when paging and refreshing the tail', async () => {
  const summary = (id: string) => ({ run_id: id, agent_id: 'atlas', status: 'succeeded', tool_count: 1, tool_trace: [] });
  const latest = { items: [{ ...messages[1], run_id: 'new' }], has_before: true, has_after: false, run_summaries: { new: summary('new') } };
  const fetch = vi.spyOn(worldApi, 'getConversationTimeline').mockResolvedValue(latest);
  const { rerender } = render(<Harness />);
  await screen.findByText('Message 2');
  fetch.mockResolvedValue({ items: [{ ...messages[0], run_id: 'old' }], has_before: false, has_after: true, run_summaries: { old: summary('old') } });
  fireEvent.click(screen.getByText('Older'));
  await screen.findByText('Message 1');
  expect(screen.getByTestId('summaries').textContent).toBe('new,old');
  fetch.mockResolvedValue(latest);
  rerender(<Harness refresh="tail" />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(screen.getByTestId('summaries').textContent).toBe('new,old');
});
it('refreshes the same stream ID and sequence without adding a second message', async () => {
  const first = { ...messages[0], content: 'Inspecting', is_final: false };
  const fetch = vi.spyOn(worldApi, 'getConversationTimeline').mockResolvedValue({ items: [first], has_before: false, has_after: false });
  const { container, rerender } = render(<Harness />);
  await screen.findByText('Inspecting');
  const original = container.querySelector('[data-message-id="1"]');
  fetch.mockResolvedValue({ items: [{ ...first, content: 'Inspecting local resources.', is_final: true }], has_before: false, has_after: false });
  rerender(<Harness refresh="stream-update" />);
  await screen.findByText('Inspecting local resources.');
  expect(fetch).toHaveBeenLastCalledWith('room', 'a', {});
  expect(container.querySelectorAll('[data-message-id]')).toHaveLength(1);
  expect(container.querySelector('[data-message-id="1"]')).toBe(original);
});

it('does not bridge a missing interval when refreshing after many missed events', async () => {
  const fetch = vi.spyOn(worldApi, 'getConversationTimeline').mockResolvedValue({ items: messages.slice(0, 50), has_before: false, has_after: false });
  const { container, rerender } = render(<Harness />);
  await screen.findByText('Message 50');
  fetch.mockResolvedValue({ items: messages.slice(350, 400), has_before: true, has_after: false });
  rerender(<Harness refresh="reconnected" />);
  await screen.findByText('Message 400');
  expect(screen.queryByText('Message 50')).toBeNull();
  expect(container.querySelectorAll('[data-message-id]')).toHaveLength(50);
});
it("preserves events arriving during a snapshot request, then lets a later snapshot repair missed stops", async () => {
  let finish!: (page: ConversationMessagePage) => void;
  const fetch = vi.spyOn(worldApi, "getConversationTimeline").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const { rerender } = render(<Harness />);
  const started: RuntimeEvent = { id: "start", type: "run_started", agent_id: "atlas", conversation_id: "room",
    session_id: "a", timestamp: "2026-09-14T00:00:00Z", payload: {} };
  rerender(<Harness events={[started]} />);
  expect(screen.getByTestId("active").textContent).toBe("atlas");
  const snapshot = { items: [messages[0]], has_before: false, has_after: false, active_agent_ids: [] };
  await act(async () => finish(snapshot));
  expect(screen.getByTestId("active").textContent).toBe("atlas");
  fetch.mockResolvedValue(snapshot);
  rerender(<Harness events={[started]} refresh="repair" />);
  await waitFor(() => expect(screen.getByTestId("active").textContent).toBe(""));
  fetch.mockResolvedValue({ ...snapshot, active_agent_ids: ["river"] });
  rerender(<Harness events={[started]} refresh="reconnect" />);
  await waitFor(() => expect(screen.getByTestId("active").textContent).toBe("river"));
});
it("keeps a bounded contiguous window while paging in both directions", async () => {
  vi.spyOn(worldApi, "getConversationTimeline").mockImplementation(async (_room, _session, cursor = {}) => {
    const filtered = messages.filter((m) => cursor.before !== undefined ? m.sequence! < cursor.before : cursor.after !== undefined ? m.sequence! > cursor.after : true);
    const items = cursor.after !== undefined ? filtered.slice(0, 50) : filtered.slice(-50);
    return { items, has_before: items[0]?.sequence !== 1, has_after: items.at(-1)?.sequence !== 420 };
  });
  const { container } = render(<Harness />);
  await screen.findByText("Message 420");
  for (let i = 0; i < 4; i++) {
    fireEvent.click(screen.getByText("Older"));
    await waitFor(() => expect(screen.getByText("ready")).toBeTruthy());
  }
  const ids = () => [...container.querySelectorAll("[data-message-id]")].map((n) => Number(n.getAttribute("data-message-id")));
  expect(ids()).toEqual(Array.from({ length: 150 }, (_, i) => i + 171));
  fireEvent.click(screen.getByText("Newer"));
  await waitFor(() => expect(screen.getByText("Message 370")).toBeTruthy());
  expect(ids()).toEqual(Array.from({ length: 150 }, (_, i) => i + 221));
});
it("ignores a delayed old-session page after switching sessions", async () => {
  let resolveOld!: (page: ConversationMessagePage) => void;
  vi.spyOn(worldApi, "getConversationTimeline").mockImplementation((_room, session) => session === "a"
    ? new Promise((resolve) => { resolveOld = resolve; })
    : Promise.resolve({ items: [{ ...messages[0], id: "b", session_id: "b", content: "Session B" }], has_before: false, has_after: false }));
  const { rerender } = render(<Harness />);
  rerender(<Harness session="b" />);
  await screen.findByText("Session B");
  await act(async () => resolveOld({ items: [messages[0]], has_before: false, has_after: false }));
  expect(screen.queryByText("Message 1")).toBeNull();
  expect(screen.getByText("Session B")).toBeTruthy();
});

it("refreshes status and new-message availability without evicting a reader's window", async () => {
  const initial = messages.slice(100, 150);
  const fetch = vi.spyOn(worldApi, "getConversationTimeline").mockResolvedValue({ items: initial, has_before: true, has_after: false });
  const { container, rerender } = render(<Harness />);
  await screen.findByText("Message 150");
  const transcript = container.querySelector("div > div") as HTMLElement;
  Object.defineProperties(transcript, {
    scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 200 },
  });
  transcript.scrollTop = 100;
  fireEvent.scroll(transcript);
  fetch.mockResolvedValue({ items: messages.slice(150, 200), has_before: true, has_after: true, active_agent_ids: [] });
  rerender(<Harness refresh="new-event" />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Message 101")).toBeTruthy();
  expect(screen.queryByText("Message 200")).toBeNull();
  expect(container.querySelectorAll("[data-message-id]")).toHaveLength(50);
});

it("queues an invalidation behind an in-flight snapshot instead of waiting for polling", async () => {
  let finish!: (page: ConversationMessagePage) => void;
  const fetch = vi.spyOn(worldApi, "getConversationTimeline")
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValue({ items: [messages[0]], has_before: false, has_after: false });
  const { rerender } = render(<Harness />);
  rerender(<Harness refresh="message-arrived" />);
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => finish({ items: [], has_before: false, has_after: false }));
  await screen.findByText("Message 1");
  expect(fetch).toHaveBeenCalledTimes(2);
});
