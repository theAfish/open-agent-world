import { useEffect, useRef, useState } from "react";
import { worldApi, apiErrorMessage, type RunSummary } from "../api/client";
import { t, useLocale } from "../i18n";
import { useWorldStore } from "../state/worldStore";
import { mergeRunActivity } from "../state/runActivity";
import type { ConversationRunSummary } from "../types/world";
import { RunActivityDetails, RunActivityStream } from "./RunActivityStream";

function conversationSummary(row: RunSummary): ConversationRunSummary {
  return {
    run_id: row.run_id, agent_id: row.agent_id, status: row.status,
    started_at: row.started_at, finished_at: row.finished_at,
    tool_count: Number(row.lifecycle.tool_count ?? 0),
    tool_trace: Array.isArray(row.lifecycle.tool_trace) ? row.lifecycle.tool_trace : [],
    tool_trace_truncated: Boolean(row.lifecycle.tool_trace_truncated),
  };
}

export function AgentRunHistory({ agentId, active }: { agentId: string; active: boolean }) {
  useLocale();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<{ agentId: string; runs: ConversationRunSummary[]; more: boolean }>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const loadMore = useRef((_before: string) => {});
  const events = useWorldStore(state => state.events);
  const displayed = useRef(history);
  displayed.current = history;
  useEffect(() => {
    if (!open) return;
    let current = true;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read(before?: string) {
      if (inFlight || document.hidden) return;
      clearTimeout(timer);
      inFlight = true;
      setLoading(true);
      try {
        const records = await worldApi.agentRuns(agentId, before);
        if (!current) return;
        const runs = records.map(conversationSummary);
        const prior = displayed.current?.agentId === agentId ? displayed.current.runs : [];
        // A long-running execution can move beyond the recent page. Refresh
        // only displayed nonterminal rows, without downloading their bodies.
        const headIds = new Set(records.map(row => row.run_id));
        const olderActive = before || document.hidden ? [] : prior.filter(row =>
          ["created", "running", "waiting"].includes(row.status) && !headIds.has(row.run_id));
        const updated = new Map((await Promise.all(olderActive.map(row => worldApi.runSummary(row.run_id))))
          .map(row => [row.run_id, conversationSummary(row)]));
        if (current) {
          setHistory(previous => {
            const old = previous?.agentId === agentId
              ? previous.runs.map(row => updated.get(row.run_id) ?? row) : [];
            // A nonoverlapping recent page has an unknown gap before the old
            // window. Restart its cursor instead of silently skipping that gap.
            const overlap = runs.some(run => old.some(row => row.run_id === run.run_id));
            if (!before && !overlap) return { agentId, runs, more: records.length === 10 };
            const combined = before ? [...old, ...runs] : [...runs, ...old];
            const seen = new Set<string>();
            const merged = combined.filter(run => { if (seen.has(run.run_id)) return false; seen.add(run.run_id); return true; });
            // Keep the explicitly loaded window bounded. Dropped older rows
            // remain reachable even after a previously exhausted history grows.
            const retained = before ? merged : merged.slice(0, Math.max(10, old.length));
            return { agentId, runs: retained,
              more: merged.length > retained.length || (before || old.length <= 10 ? records.length === 10 : previous!.more) };
          });
          setError("");
        }
      } catch (cause) { if (current) setError(apiErrorMessage(cause)); }
      finally {
        inFlight = false;
        if (current) setLoading(false);
        if (current && active && !document.hidden) timer = setTimeout(() => void read(), 2000);
      }
    }
    loadMore.current = before => void read(before);
    const visibility = () => { if (document.hidden) clearTimeout(timer); else void read(); };
    document.addEventListener("visibilitychange", visibility);
    void read();
    return () => { current = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [agentId, active, open]);
  return <details open={open} onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{t("Execution details")}</summary>
    {error ? <p role="alert">{error}</p> : null}
    {open && history?.agentId === agentId ? history.runs.map(run => {
      const activity = mergeRunActivity(undefined, run, events);
      return <div key={run.run_id}>
        {run.status === "running" || run.status === "waiting"
          ? <RunActivityStream activity={activity} runId={run.run_id} active waiting={run.status === "waiting"} />
          : <RunActivityDetails run={run} activity={activity} />}
      </div>;
    }) : null}
    {open && history?.agentId === agentId && history.more
      ? <button disabled={loading} onClick={() => loadMore.current(history.runs.at(-1)!.run_id)}>{t("Load more")}</button> : null}
  </details>;
}
