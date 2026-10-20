import { useEffect, useState } from "react";
import { worldApi, apiErrorMessage } from "../api/client";
import { t, useLocale } from "../i18n";
import { useWorldStore } from "../state/worldStore";
import { mergeRunActivity } from "../state/runActivity";
import type { ConversationRunSummary } from "../types/world";
import { RunActivityDetails, RunActivityStream } from "./RunActivityStream";

export function AgentRunHistory({ agentId, active }: { agentId: string; active: boolean }) {
  useLocale();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<{ agentId: string; runs: ConversationRunSummary[] }>();
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(10);
  const events = useWorldStore(state => state.events);
  useEffect(() => {
    if (!open) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const records = await worldApi.agentRuns(agentId);
        const runs: ConversationRunSummary[] = records.map(row => ({
          run_id: row.run_id, agent_id: row.agent_id, status: row.status,
          started_at: row.started_at, finished_at: row.finished_at,
          tool_count: Number(row.lifecycle.tool_count ?? 0),
          tool_trace: Array.isArray(row.lifecycle.tool_trace) ? row.lifecycle.tool_trace : [],
          tool_trace_truncated: Boolean(row.lifecycle.tool_trace_truncated),
        }));
        if (current) { setHistory({ agentId, runs }); setError(""); }
      } catch (cause) { if (current) setError(apiErrorMessage(cause)); }
      finally { if (current && active) timer = setTimeout(() => void read(), 2000); }
    }
    void read();
    return () => { current = false; clearTimeout(timer); };
  }, [agentId, active, open]);
  return <details open={open} onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{t("Execution details")}</summary>
    {error ? <p role="alert">{error}</p> : null}
    {open && history?.agentId === agentId ? history.runs.slice(0, visible).map(run => {
      const activity = mergeRunActivity(undefined, run, events);
      return <div key={run.run_id}>
        {run.status === "running" || run.status === "waiting"
          ? <RunActivityStream activity={activity} runId={run.run_id} active waiting={run.status === "waiting"} />
          : <RunActivityDetails run={run} activity={activity} />}
      </div>;
    }) : null}
    {open && history?.agentId === agentId && history.runs.length > visible
      ? <button onClick={() => setVisible(value => value + 10)}>{t("Load more")}</button> : null}
  </details>;
}
