import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import type { ConversationMessagePage, RuntimeEvent } from "../types/world";
import { activeConversationAgentIds, activeConversationRuns } from "./conversationActivity";

export const CONVERSATION_WINDOW_SIZE = 150;
const emptyPage: ConversationMessagePage = { items: [], has_before: false, has_after: false };
type Direction = "latest" | "before" | "after" | "refresh";

/** REST is authoritative. Keep a bounded contiguous window, including after reconnect. */
export function useConversationTimeline(conversationId: string, sessionId: string | undefined,
  refresh: string | undefined, socketLive: boolean, element: RefObject<HTMLDivElement>, events: RuntimeEvent[] = []) {
  const scope = `${conversationId}/${sessionId ?? ""}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const [state, setState] = useState<{ scope: string; page: ConversationMessagePage; activityBoundary?: string }>({ scope, page: emptyPage });
  const latestEvent = useRef<string>();
  latestEvent.current = events[0]?.id;
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
    // Only events received after this request may override its snapshot.
    // Capture before awaiting so events arriving in flight remain visible.
    const activityBoundary = latestEvent.current;
    pending.current = true;
    if (direction !== "refresh") setLoading(true);
    const current = pageRef.current;
    const cursor = direction === "before" ? { before: current.items[0]?.sequence }
      : direction === "after" ? { after: current.items.at(-1)?.sequence } : {};
    // Refresh the tail itself: streaming snapshots update existing IDs and
    // sequences, so an exclusive `after` cursor would never retrieve them.
    try {
      const incoming = await worldApi.getConversationTimeline(conversationId, sessionId, cursor);
      if (version !== generation.current || currentScope.current !== scope) return;
      if (direction === "refresh" && !follow.current) {
        const next = { ...current,
          active_agent_ids: incoming.active_agent_ids,
          active_runs: incoming.active_runs,
          deliveries: incoming.deliveries,
          run_summaries: incoming.run_summaries,
          has_after: current.has_after || (incoming.items.at(-1)?.sequence ?? 0) > (current.items.at(-1)?.sequence ?? 0) };
        pageRef.current = next;
        setState({ scope, page: next, activityBoundary });
        setError(undefined);
        return;
      }
      const node = element.current;
      const visible = node && [...node.querySelectorAll<HTMLElement>("[data-message-id]")]
        .find((item) => item.getBoundingClientRect().bottom > node.getBoundingClientRect().top);
      anchor.current = visible ? { id: visible.dataset.messageId!, offset: visible.getBoundingClientRect().top - node!.getBoundingClientRect().top } : undefined;
      bottom.current = direction === "latest" || ((direction === "after" || direction === "refresh") && follow.current);
      const tailGap = direction === "refresh" && incoming.items.length > 0 && current.items.length > 0
        && !incoming.items.some(item => current.items.some(existing => existing.id === item.id));
      const merged = direction === "latest" || tailGap ? incoming.items
        : [...new Map([...current.items, ...incoming.items].map((item) => [item.id, item])).values()]
          .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
      const trimmed = merged.length > CONVERSATION_WINDOW_SIZE;
      const items = direction === "before" ? merged.slice(0, CONVERSATION_WINDOW_SIZE) : merged.slice(-CONVERSATION_WINDOW_SIZE);
      const next = {
        active_agent_ids: incoming.active_agent_ids,
        active_runs: incoming.active_runs,
        deliveries: incoming.deliveries,
        run_summaries: incoming.run_summaries,
        items,
        has_before: direction === "before" || direction === "latest" || tailGap ? incoming.has_before : current.has_before || trimmed,
        has_after: direction === "before" ? current.has_after || trimmed : incoming.has_after,
      };
      pageRef.current = next;
      setState({ scope, page: next, activityBoundary });
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
    // While the websocket is healthy, invalidations arrive as events; keep only
    // a slow repair poll. Fall back to fast polling when the socket is down.
    const timer = window.setInterval(refreshTail, socketLive ? 30000 : 3000);
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
  const boundaryIndex = state.scope === scope && state.activityBoundary
    ? events.findIndex((event) => event.id === state.activityBoundary) : -1;
  const activityEvents = boundaryIndex < 0 ? events : events.slice(0, boundaryIndex);
  const activeRuns = activeConversationRuns(
    activityEvents, conversationId, sessionId, page.active_runs ?? [],
  );
  const activeAgentIds = activeRuns.length
    ? [...new Set(activeRuns.map((run) => run.agent_id))]
    : activeConversationAgentIds(activityEvents, conversationId, sessionId, page.active_agent_ids);
  return {
    messages: page.items,
    activeAgentIds,
    activeRuns,
    deliveries: page.deliveries ?? [],
    runSummaries: page.run_summaries ?? {},
    hasBefore: page.has_before,
    hasAfter: page.has_after,
    showLatest: state.scope === scope && (awayFromBottom || page.has_after),
    loading,
    error,
    onScroll,
    loadOlder: () => load("before"),
    loadNewer: () => load("after"),
    loadLatest: () => load("latest"),
  };
}
