// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import type { ConversationMessage, ConversationMessagePage } from "../types/world";
import { useConversationTimeline } from "./useConversationTimeline";
const messages: ConversationMessage[] = Array.from({ length: 420 }, (_, i) => ({
  id: String(i + 1), sequence: i + 1, conversation_id: "room", session_id: "a",
  sender_kind: "agent", sender_name: "Atlas", content: `Message ${i + 1}`, mention_agent_ids: [], created_at: "2026-09-09T00:00:00Z",
}));
function Harness({ session = "a", refresh = "" }: { session?: string; refresh?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const history = useConversationTimeline("room", session, refresh, true, ref);
  return <><button onClick={history.loadOlder}>Older</button><button onClick={history.loadNewer}>Newer</button>
    <div ref={ref} onScroll={history.onScroll}>{history.messages.map((m) => <p key={m.id} data-message-id={m.id}>{m.content}</p>)}</div>
    <span>{history.loading ? "busy" : "ready"}</span></>;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
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
