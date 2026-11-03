import { Modal, hostApiBase, t } from "@oaw/plugin-api";
import { ChevronRight, CircleAlert, LoaderCircle, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type LogEvent = { at: string; stage: string; message?: string; done?: number; total?: number | null };
type RunInfo = { run_id: string; status: string; created_at: string; error?: string | null };
type Invocation = { id: string; kind: "models" | "tools"; name: string; role?: string; state: string; started_at: string; returned_at?: string;
  model_request?: number; input_tokens?: number | null; output_tokens?: number | null; arguments_preview?: string; error_type?: string };
type Detail = { arguments?: unknown; effective_arguments?: unknown; response?: unknown; error?: unknown };

const LIVE = ["created", "running", "waiting"];
const PAGE = 100;
const REPEAT_WARNING = 3;
const clock = (value?: string) => value ? new Date(value).toLocaleTimeString() : "";
const seconds = (start?: string, end?: string) => start && end ? `${((Date.parse(end) - Date.parse(start)) / 1000).toFixed(1)}s` : "";
const clip = (value: unknown, limit = 3000) => {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
  return text.length > limit ? `${text.slice(0, limit)}\n… (${text.length - limit} more characters)` : text;
};

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${hostApiBase}${path}`);
  if (!response.ok) throw new Error(t("Unable to read the run log"));
  return await response.json() as T;
}

/** The most recent page of one invocation list: the directory is ascending, so read the
 * total first and then the tail. */
async function tail(runId: string, kind: "models" | "tools"): Promise<Invocation[]> {
  let page = await get<{ items: Invocation[]; total: number }>(`/runs/${encodeURIComponent(runId)}/invocations/${kind}?offset=0&limit=${PAGE}`);
  if (page.total > PAGE) page = await get(`/runs/${encodeURIComponent(runId)}/invocations/${kind}?offset=${page.total - PAGE}&limit=${PAGE}`);
  return page.items.map(item => ({ ...item, kind }));
}

/** Consecutive calls to the same tool with the same arguments: the symptom of an agent
 * that cannot use what it was given. */
export function repeatedCall(rows: Invocation[]): { name: string; count: number } | undefined {
  let best: { name: string; count: number } | undefined;
  let streak = 0;
  const calls = rows.filter(row => row.kind === "tools");
  calls.forEach((row, index) => {
    const same = index > 0 && calls[index - 1].name === row.name && calls[index - 1].arguments_preview === row.arguments_preview;
    streak = same ? streak + 1 : 1;
    if (streak >= REPEAT_WARNING && (!best || streak >= best.count)) best = { name: row.name, count: streak };
  });
  return best;
}

function Row({ runId, row }: { runId: string; row: Invocation }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Detail>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || detail) return;
    get<Detail>(`/runs/${encodeURIComponent(runId)}/invocations/${row.kind}/${encodeURIComponent(row.id)}`).then(setDetail, reason => setError(String(reason.message ?? reason)));
  }, [open, detail, runId, row.kind, row.id]);
  const failed = ["failed", "interrupted"].includes(row.state);
  const label = row.kind === "models"
    ? `${t("Model request")} ${row.model_request ?? ""}${row.role && row.role !== "agent" ? ` · ${row.role}` : ""}`
    : row.name;
  const meta = row.kind === "models"
    ? [row.input_tokens != null ? `${row.input_tokens} ${t("in")}` : "", row.output_tokens != null ? `${row.output_tokens} ${t("out")}` : ""].filter(Boolean).join(" · ")
    : (row.arguments_preview ?? "").replace(/"knowledge":\s*"[0-9a-f-]+",?\s*/, "");
  return <li className={`run-log-row${failed ? " is-failed" : ""}`} data-kind={row.kind}>
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <ChevronRight size={12} className="run-log-chevron" aria-hidden="true" />
      <time>{clock(row.started_at)}</time>
      <strong>{label}</strong>
      <span className="run-log-meta" title={meta}>{meta}</span>
      <span className="run-log-state">{row.state === "started" ? <LoaderCircle size={12} className="project-spin" aria-label={t("Running")} /> : failed ? <CircleAlert size={12} aria-label={t("Failed")} /> : seconds(row.started_at, row.returned_at)}</span>
    </button>
    {open && <div className="run-log-detail">
      {!detail && !error && <small role="status">{t("Loading…")}</small>}
      {error && <small role="alert">{error}</small>}
      {detail && row.kind === "tools" && <><b>{t("Input")}</b><pre>{clip(detail.effective_arguments ?? detail.arguments)}</pre></>}
      {detail && row.kind === "models" && detail.response !== undefined && <><b>{t("Reply")}</b><pre>{clip(detail.response)}</pre></>}
      {detail && row.kind === "tools" && detail.response !== undefined && <><b>{t("Output")}</b><pre>{clip(detail.response)}</pre></>}
      {detail && detail.error !== undefined && <><b>{t("Error")}</b><pre>{clip(detail.error)}</pre></>}
    </div>}
  </li>;
}

/** The detailed log of an extraction. An Agent run shows every model request and tool call
 * with its input and output on demand; a host-model run shows the progress it streamed. */
export function RunLogModal({ agentId, runId: preferred, events, stopping, canStop, onStop, onClose }: {
  agentId?: string; runId?: string; events: LogEvent[]; stopping: boolean; canStop: boolean; onStop: () => void; onClose: () => void;
}) {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [runId, setRunId] = useState(preferred ?? "");
  const [rows, setRows] = useState<Invocation[]>([]);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const current = runs.find(run => run.run_id === runId);
  const live = !!current && LIVE.includes(current.status.toLowerCase());

  const refresh = useCallback(async () => {
    try {
      let target = runId;
      if (agentId) {
        const list = (await get<RunInfo[]>(`/runs?agent_id=${encodeURIComponent(agentId)}`)).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);
        if (!alive.current) return;
        setRuns(list);
        if (!target) { target = list.find(run => LIVE.includes(run.status.toLowerCase()))?.run_id ?? list[0]?.run_id ?? ""; setRunId(target); }
      }
      if (!target) return;
      const [models, tools] = await Promise.all([tail(target, "models"), tail(target, "tools")]);
      if (!alive.current) return;
      setRows([...models, ...tools].sort((a, b) => a.started_at.localeCompare(b.started_at)));
      setError("");
    } catch (reason) { if (alive.current) setError(String((reason as Error).message ?? reason)); }
  }, [agentId, runId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const repeated = useMemo(() => repeatedCall(rows), [rows]);
  const stopButton = canStop && <button type="button" className="project-stop" disabled={stopping} onClick={onStop}><Square size={13} />{stopping ? t("Stopping…") : t("Stop extraction")}</button>;

  return <Modal title={t("Extraction log")} onClose={onClose} className="knowledge-modal run-log-modal" actions={<>{stopButton}<button type="button" onClick={onClose}>{t("Close")}</button></>}>
    {agentId && <div className="run-log-run">
      <label>{t("Agent run")} <select value={runId} onChange={event => { setRows([]); setRunId(event.target.value); }}>
        {runs.map(run => <option key={run.run_id} value={run.run_id}>{clock(run.created_at)} · {run.status}</option>)}</select></label>
      {current && <span className={live ? "run-log-live" : ""}>{live ? t("Running") : current.status}</span>}
      {current?.error && <span role="alert">{current.error}</span>}
    </div>}
    {repeated && <p role="alert" className="knowledge-error run-log-repeat">{t("The agent called {v0} {v1} times in a row with the same arguments. It is probably stuck.", { v0: repeated.name, v1: repeated.count })}</p>}
    {error && <p role="alert" className="knowledge-error">{error}</p>}
    {agentId && (rows.length
      ? <ul className="run-log-list" aria-label={t("Model requests and tool calls")}>{rows.map(row => <Row key={`${runId}:${row.kind}:${row.id}`} runId={runId} row={row} />)}</ul>
      : !error && <p className="knowledge-meta" role="status">{runs.length ? t("Nothing recorded for this run yet.") : t("No agent run found.")}</p>)}
    {!!events.length && <section aria-label={t("Host model progress")}>
      {agentId && <h4>{t("Host model progress")}</h4>}
      <ul className="run-log-list">{events.map((event, index) => <li key={index} className="run-log-row"><div className="run-log-event">
        <time>{clock(event.at)}</time><strong>{event.stage}</strong>
        <span className="run-log-meta">{event.message ?? ""}{event.total ? ` ${event.done ?? 0}/${event.total}` : ""}</span></div></li>)}</ul>
    </section>}
    {!agentId && !events.length && <p className="knowledge-meta" role="status">{t("Nothing recorded yet. Progress appears here while a model extracts.")}</p>}
  </Modal>;
}
