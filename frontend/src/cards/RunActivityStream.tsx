import { memo, useState } from "react";
import { LoaderCircle, Square } from "lucide-react";
import { t, useLocale } from "../i18n";
import type { ConversationRunSummary, SavedInvocation } from "../types/world";
import { toolFailed, withoutFinalReply, withSavedThinking, withSavedTools, type RunActivityItem, type RunActivityState } from "../state/runActivity";
import { useSavedThinking } from "../state/useSavedThinking";
import { useSavedInvocations } from "../state/useSavedInvocations";
import { useInvocationDetail } from "../state/useInvocationDetail";
import { ToolActivity as PublicToolActivity } from "./ConversationActivity";
import { ReasoningActivity } from "./RunReasoning";
import { MarkdownMessage } from "./MarkdownMessage";
import { runStatusLabel } from '../state/runPresentation';

function format(value: unknown): string {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2) ?? ""; }
  catch { return t("No additional details"); }
}
const ToolActivity = memo(function ToolActivity({ item, active, runId }: { item: RunActivityItem; active: boolean; runId?: string }) {
  const [open, setOpen] = useState(false);
  return <PublicToolActivity item={item} active={active} runId={runId} open={open}
    onOpen={(_id, expanded) => setOpen(expanded)} />;
});

const ActivityRow = memo(function ActivityRow({ item, active, runId }: { item: RunActivityItem; active: boolean; runId?: string }) {
  if (item.type === "tool_started" || item.type === "tool_completed") return <ToolActivity item={item} active={active} runId={runId} />;
  if (item.kind === "model_reasoning") return <ReasoningActivity item={item} active={active} runId={runId} />;
  return <div className={item.type === "agent_progress" ? "run-activity-progress" : "run-activity-text"}>
    {item.type === "agent_message" ? <MarkdownMessage content={item.text ?? ""} /> : <p>{item.text}</p>}
  </div>;
});

const ModelRequest = memo(function ModelRequest({ item, runId, active }: { item: SavedInvocation; runId: string; active: boolean }) {
  const [open, setOpen] = useState(false);
  const detail = useInvocationDetail(runId, "models", item.id, open, active);
  return <details className="run-model-request" open={open} onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{item.role.replaceAll("_", " ")} #{item.model_request} · {t("Model attempt")} {item.model_attempt}
      {` · ${t("Input tokens")}: ${item.input_tokens ?? t("Unknown")} · ${t("Output tokens")}: ${item.output_tokens ?? t("Unknown")}`}
      {` · ${item.finish_reason ?? item.state}`}</summary>
    {open ? <div className="run-tool-details">
      {detail.row ? <>
        <section><span>{t("Model input")}</span><pre>{format(detail.row.request)}</pre></section>
        {detail.row.response !== undefined ? <section><span>{t("Model response")}</span><pre>{format(detail.row.response)}</pre></section> : null}
        {detail.row.request_complete === false || detail.row.response_complete === false ? <small>{t("Details incomplete")}</small> : null}
      </> : null}
      {detail.error ? <p role="alert">{detail.error} <button onClick={detail.retry}>{t("Retry")}</button></p> : null}
    </div> : null}
  </details>;
});

export function RunActivityStream({ activity, runId, active = false, waiting = false, stopping = false, onStop, showModelRequests = true }: {
  activity?: RunActivityState;
  runId?: string;
  active?: boolean;
  waiting?: boolean;
  stopping?: boolean;
  onStop?: () => void;
  showModelRequests?: boolean;
}) {
  useLocale();
  const saved = useSavedThinking(runId, active);
  const tools = useSavedInvocations(runId, "tools", active);
  const models = useSavedInvocations(showModelRequests ? runId : undefined, "models", active);
  const restored = withSavedTools(withSavedThinking(activity, saved.rows), tools.rows);
  return <div className="run-activity-stream">
    {activity?.truncated ? <small className="run-activity-note">{t("Earlier activity is no longer in this live view")}</small> : null}
    <div className="run-activity-items">
      {restored.items.map(item => <ActivityRow key={item.id} item={item} active={active} runId={runId} />)}
      {saved.error ? <p role="alert">{saved.error}</p> : null}
      {tools.error ? <p role="alert">{tools.error}</p> : null}
      {showModelRequests && models.error ? <p role="alert">{models.error}</p> : null}
    </div>
    {showModelRequests && runId && models.rows.length ? <details className="run-model-requests">
      <summary>{t("Model requests")} · {models.rows.length}</summary>
      {models.rows.map(item => <ModelRequest key={item.id} item={item} runId={runId} active={active} />)}
    </details> : null}
    {active ? <div className="run-activity-footer">
      {onStop ? <button type="button" className="run-stop-button" disabled={stopping} onClick={onStop}>
        <Square size={10} aria-hidden="true" />{stopping ? t("Stopping…") : t("Stop")}
      </button> : null}
      <span role="status">
        {!waiting ? <LoaderCircle size={12} className="run-activity-spinner" aria-hidden="true" /> : null}
        {waiting ? t("Waiting") : t("Working")}
      </span>
    </div> : null}
  </div>;
}

export function RunActivityDetails({ run, activity, finalReply, showModelRequests = true }: {
  run?: ConversationRunSummary;
  activity?: RunActivityState;
  finalReply?: string;
  showModelRequests?: boolean;
}) {
  useLocale();
  const [open, setOpen] = useState(false);
  const detailsActivity = withoutFinalReply(activity, finalReply);
  const count = run?.tool_count ?? activity?.items.filter(item => item.type.startsWith("tool_")).length ?? 0;
  const seconds = run?.started_at && run.finished_at
    ? Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000)) : undefined;
  const importedBeforeFailure = run?.delivery_state === "failed"
    && Object.values(run.structure_outcomes ?? {}).some(outcome => outcome.phase === "confirmed");
  if (!run && detailsActivity && !detailsActivity.items.length && !detailsActivity.truncated && !count) return null;
  return <details className="run-activity-history" open={open} onToggle={event => {
    // Ignore toggle events from nested tool disclosures.
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{run ? `${importedBeforeFailure ? t("Structure verified; final reply failed") : t(runStatusLabel(run.status))} · ` : ''}{seconds === undefined ? t("Execution details") : t("Worked for {v0}s", { v0: seconds })}
      {count ? ` · ${count} ${t("tool calls")}` : ""}</summary>
    {importedBeforeFailure ? <p>{t("Structure write verified; final reply failed. Inspect the current structure before retrying.")}</p> : null}
    {open ? <RunActivityStream activity={detailsActivity} runId={run?.run_id} showModelRequests={showModelRequests} /> : null}
  </details>;
}
