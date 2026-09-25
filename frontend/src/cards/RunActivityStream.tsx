import { memo, useState } from "react";
import { Check, ChevronRight, CircleAlert, LoaderCircle, Square } from "lucide-react";
import { t, useLocale } from "../i18n";
import type { ConversationRunSummary } from "../types/world";
import { toolFailed, withoutFinalReply, type RunActivityItem, type RunActivityState } from "../state/runActivity";
import { MarkdownMessage } from "./MarkdownMessage";

function format(value: unknown): string {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2) ?? ""; }
  catch { return t("No additional details"); }
}
function preview(value: unknown): string {
  if (typeof value === "string") return value.split("\n")[0].slice(0, 100);
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const input = value as Record<string, unknown>;
  const hint = input.path ?? input.command ?? input.query ?? input.filename;
  return typeof hint === "string" ? hint.split("\n")[0].slice(0, 100) : "";
}

const ToolActivity = memo(function ToolActivity({ item, active }: { item: RunActivityItem; active: boolean }) {
  useLocale();
  const [open, setOpen] = useState(false);
  const failed = toolFailed(item);
  const completed = item.type === "tool_completed";
  const pending = !completed && active;
  const status = failed ? t("Failed") : completed ? t("Completed") : pending ? t("Running") : t("Stopped");
  const Icon = failed ? CircleAlert : completed ? Check : pending ? LoaderCircle : Square;
  const response = item.response;
  const hasDetails = item.arguments !== undefined || response !== undefined || item.error !== undefined;
  return <details className={`run-tool${failed ? " is-error" : ""}`} open={open}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <ChevronRight size={12} className="run-tool-chevron" aria-hidden="true" />
      <Icon size={12} className={pending ? "run-activity-spinner" : undefined} aria-label={status} />
      <span className="run-tool-name">{item.name}</span>
      <code className="run-tool-preview">{preview(item.arguments)}</code>
    </summary>
    {open ? <div className="run-tool-details">
      {item.arguments !== undefined ? <section><span>{t("Input")}</span><pre>{format(item.arguments)}</pre></section> : null}
      {response !== undefined ? <section><span>{t("Output")}</span><pre>{format(response)}</pre></section> : null}
      {item.error !== undefined && item.error !== null ? <section><span>{t("Error")}</span><pre>{format(item.error)}</pre></section> : null}
      {!hasDetails ? <p>{t("No additional details")}</p> : null}
      {pending ? <small>{t("Running")}</small> : null}
      {item.truncated ? <small>{t("Details truncated")}</small> : null}
    </div> : null}
  </details>;
});

const ActivityRow = memo(function ActivityRow({ item, active }: { item: RunActivityItem; active: boolean }) {
  if (item.type === "tool_started" || item.type === "tool_completed") return <ToolActivity item={item} active={active} />;
  return <div className={item.type === "agent_progress" ? "run-activity-progress" : "run-activity-text"}>
    {item.type === "agent_message" ? <MarkdownMessage content={item.text ?? ""} /> : <p>{item.text}</p>}
  </div>;
});

export function RunActivityStream({ activity, active = false, waiting = false, stopping = false, onStop }: {
  activity?: RunActivityState;
  active?: boolean;
  waiting?: boolean;
  stopping?: boolean;
  onStop?: () => void;
}) {
  useLocale();
  return <div className="run-activity-stream">
    {activity?.truncated ? <small className="run-activity-note">{t("Earlier activity is no longer in this live view")}</small> : null}
    <div className="run-activity-items">
      {activity?.items.map(item => <ActivityRow key={item.id} item={item} active={active} />)}
    </div>
    {active ? <div className="run-activity-footer">
      {onStop ? <button type="button" className="run-stop-button" disabled={stopping} onClick={onStop}>
        <Square size={10} aria-hidden="true" />{stopping ? t("Stopping…") : t("Stop")}
      </button> : null}
      {waiting || !activity?.items.length ? <span role="status">
        {!waiting ? <LoaderCircle size={12} className="run-activity-spinner" aria-hidden="true" /> : null}
        {waiting ? t("Waiting") : t("Waiting for activity…")}
      </span> : null}
    </div> : null}
  </div>;
}

export function RunActivityDetails({ run, activity, finalReply }: {
  run?: ConversationRunSummary;
  activity?: RunActivityState;
  finalReply?: string;
}) {
  useLocale();
  const [open, setOpen] = useState(false);
  const detailsActivity = withoutFinalReply(activity, finalReply);
  const count = run?.tool_count ?? activity?.items.filter(item => item.type.startsWith("tool_")).length ?? 0;
  const seconds = run?.started_at && run.finished_at
    ? Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000)) : undefined;
  if (detailsActivity && !detailsActivity.items.length && !detailsActivity.truncated && !count) return null;
  return <details className="run-activity-history" open={open} onToggle={event => {
    // Ignore toggle events from nested tool disclosures.
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{seconds === undefined ? t("Execution details") : t("Worked for {v0}s", { v0: seconds })}
      {count ? ` · ${count} ${t("tool calls")}` : ""}</summary>
    {open ? <RunActivityStream activity={detailsActivity} /> : null}
  </details>;
}
