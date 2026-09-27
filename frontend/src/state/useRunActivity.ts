import { useLayoutEffect, useMemo, useRef } from "react";
import type { ConversationRunSummary, RuntimeEvent } from "../types/world";
import { mergeRunActivity, type RunActivityState } from "./runActivity";

/** Session-local display cache, not a second conversation history or scheduler. */
export function useRunActivity(conversationId: string, sessionId: string | undefined,
  events: RuntimeEvent[], active: ConversationRunSummary[], summaries: Record<string, ConversationRunSummary>) {
  const scope = `${conversationId}/${sessionId ?? ""}`;
  const retained = useRef({ scope, runs: new Map<string, RunActivityState>() });
  const runs = useMemo(() => {
    const next = new Map(retained.current.scope === scope ? retained.current.runs : []);
    const scoped = events.filter(event =>
      (event.conversation_id ?? event.payload.conversation_id) === conversationId &&
      (event.session_id ?? event.payload.session_id) === sessionId);
    const records = new Map([...Object.values(summaries), ...active].map(run => [run.run_id, run]));
    for (const event of scoped) {
      const id = event.run_id ?? event.payload.run_id;
      if (typeof id === "string" && !records.has(id)) {
        // Keep the tail between Run terminal and the durable final-message read.
        records.set(id, { run_id: id, agent_id: event.agent_id ?? "", status: "", tool_count: 0, tool_trace: [] });
      }
    }
    for (const [id, run] of records) next.set(id, mergeRunActivity(next.get(id), run, scoped));
    const activeIds = new Set(active.map(run => run.run_id));
    for (const id of next.keys()) {
      if (next.size <= 64) break;
      if (!activeIds.has(id)) next.delete(id);
    }
    return next;
  }, [scope, conversationId, sessionId, events, active, summaries]);
  // Commit after render so abandoned/StrictMode renders do not mutate the cache.
  useLayoutEffect(() => { retained.current = { scope, runs }; }, [scope, runs]);
  return runs;
}
