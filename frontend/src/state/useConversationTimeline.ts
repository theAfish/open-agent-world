import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import type { ConversationMessagePage } from "../types/world";

export const CONVERSATION_WINDOW_SIZE = 150;
const emptyPage: ConversationMessagePage = { items: [], has_before: false, has_after: false };
type Direction = "latest" | "before" | "after" | "refresh";

/** REST is authoritative. Keep a bounded contiguous window, including after reconnect. */
export function useConversationTimeline(conversationId: string, sessionId: string | undefined,
  refresh: string | undefined, socketLive: boolean, element: RefObject<HTMLDivElement>) {
  const scope = `${conversationId}/${sessionId ?? ""}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const [state, setState] = useState({ scope, page: emptyPage });
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const page = state.scope === scope ? state.page : emptyPage;
  const pageRef = useRef(page);
  pageRef.current = page;
  const generation = useRef(0);
  const pending = useRef(false);
  const queued = useRef<Direction>();
  const refreshScope = useRef<string>();
  const follow = useRef(true);
  const anchor = useRef<{ id: string; offset: number }>();
  const bottom = useRef(false);
  const load = useCallback(async function loadPage(direction: Direction): Promise<void> {
    if (!sessionId || currentScope.current !== scope) return;
    if (pending.current) {
      if (direction === "latest" || queued.current !== "latest") queued.current = direction;
      return;
    }
    const version = generation.current;
    pending.current = true;
    if (direction !== "refresh") setLoading(true);
    const current = pageRef.current;
    const cursor = direction === "before" ? { before: current.items[0]?.sequence }
      : (direction === "after" || direction === "refresh") ? { after: current.items.at(-1)?.sequence } : {};
    try {
      const incoming = await worldApi.getConversationTimeline(conversationId, sessionId, cursor);
      if (version !== generation.current || currentScope.current !== scope) return;
      if (direction === "refresh" && !follow.current) {
        const next = { ...current, active_agent_ids: incoming.active_agent_ids,
          has_after: current.has_after || incoming.items.length > 0 };
        pageRef.current = next;
        setState({ scope, page: next });
        setError(undefined);
        return;
      }
      const node = element.current;
      const visible = node && [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
        .find((item) => item.getBoundingClientRect().bottom > node.getBoundingClientRect().top);
      anchor.current = visible ? { id: visible.dataset.messageId!, offset: visible.getBoundingClientRect().top - node!.getBoundingClientRect().top } : undefined;
      bottom.current = direction === "latest" || ((direction === "after" || direction === "refresh") && follow.current);
      const merged = direction === "latest" ? incoming.items
        : [...new Map([...current.items, ...incoming.items].map((item) => [item.id, item])).values()]
          .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
      const trimmed = merged.length > CONVERSATION_WINDOW_SIZE;
      const items = direction === "before" ? merged.slice(0, CONVERSATION_WINDOW_SIZE) : merged.slice(-CONVERSATION_WINDOW_SIZE);
      const next = {
        active_agent_ids: incoming.active_agent_ids,
        items,
        has_before: direction === "before" || direction === "latest" ? incoming.has_before : current.has_before || trimmed,
        has_after: direction === "before" ? current.has_after || trimmed : incoming.has_after,
      };
      pageRef.current = next;
      setState({ scope, page: next });
      setError(undefined);
    } catch (reason) {
      if (version === generation.current) setError(apiErrorMessage(reason));
    } finally {
      if (version === generation.current) {
        pending.current = false;
        setLoading(false);
        const next = queued.current;
        queued.current = undefined;
        if (next) void loadPage(next);
      }
    }
  }, [conversationId, sessionId, scope, element]);

  useEffect(() => {
    generation.current += 1;
    pending.current = false;
    queued.current = undefined;
    follow.current = true;
    setAwayFromBottom(false);
    pageRef.current = emptyPage;
    setState({ scope, page: emptyPage });
    setError(undefined);
    void load("latest");
    return () => { generation.current += 1; pending.current = false; queued.current = undefined; };
  }, [scope, load]);

  useEffect(() => {
    const refreshTail = () => {
      // Repair missed notifications and status without moving a reader's historical window.
      void load(pageRef.current.items.length ? "refresh" : "latest");
    };
    // The scope effect already starts the initial snapshot. Later invalidations
    // must queue even when a prior snapshot is still in flight.
    if (refreshScope.current === scope) refreshTail();
    refreshScope.current = scope;
    const timer = window.setInterval(refreshTail, 3000);
    return () => window.clearInterval(timer);
  }, [refresh, socketLive, load, scope]);

  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    if (bottom.current) { node.scrollTop = node.scrollHeight; follow.current = true; }
    else if (anchor.current) {
      const item = [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
        .find((entry) => entry.dataset.messageId === anchor.current?.id);
      if (item) node.scrollTop += item.getBoundingClientRect().top - node.getBoundingClientRect().top - anchor.current.offset;
    }
    anchor.current = undefined;
    bottom.current = false;
    setAwayFromBottom(node.scrollHeight - node.scrollTop - node.clientHeight > 160);
  }, [state, element]);

  useEffect(() => {
    const node = element.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (follow.current && !pageRef.current.has_after) node.scrollTop = node.scrollHeight;
      setAwayFromBottom(node.scrollHeight - node.scrollTop - node.clientHeight > 160);
    });
    observer.observe(node);
    if (node.firstElementChild) observer.observe(node.firstElementChild);
    return () => observer.disconnect();
  }, [element, scope]);

  const onScroll = () => {
    const node = element.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    follow.current = distance < 24;
    setAwayFromBottom(distance > 160);
    if (pending.current) return;
    if (node.scrollTop < 40 && page.has_before) void load("before");
    else if (follow.current && page.has_after) void load("after");
  };
  return { messages: page.items, activeAgentIds: page.active_agent_ids, hasBefore: page.has_before, hasAfter: page.has_after,
    showLatest: state.scope === scope && (awayFromBottom || page.has_after),
    loading, error, onScroll, loadOlder: () => load("before"), loadNewer: () => load("after"),
    loadLatest: () => load("latest") };
}
