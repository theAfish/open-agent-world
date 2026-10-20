import { memo, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, Check, ChevronRight, CircleAlert, LoaderCircle, Square } from "lucide-react";
import { t, useLocale } from "../i18n";
import { toolFailed, type RunActivityItem } from "../state/runActivity";
import type { ExecutionGroup } from '../state/conversationTranscript';
import { executionElapsed, toolElapsed } from '../state/activityTiming';
import { MarkdownMessage } from "./MarkdownMessage";
import { hasToolData, ToolData } from './ToolData';
import { toolPresentation } from '../state/toolPresentation';
import { ReasoningActivity } from './RunReasoning';
import { useInvocationDetail } from '../state/useInvocationDetail';

export function Duration({ milliseconds }: { milliseconds?: number }) {
  if (milliseconds === undefined) return null;
  const seconds = milliseconds / 1000;
  const label = seconds < 60 ? t('{seconds}s', { seconds: seconds.toFixed(1) })
    : t('{minutes}m {seconds}s', { minutes: Math.floor(seconds / 60), seconds: Math.floor(seconds % 60) });
  return <span className="conversation-duration" title={t('Elapsed time')}>{label}</span>;
}

function useActivityClock(active: boolean) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export const ToolActivity = memo(function ToolActivity({ item, active, now, open, onOpen, runId }: {
  item: RunActivityItem; active: boolean; now?: number; open: boolean; onOpen: (id: string, open: boolean) => void; runId?: string;
}) {
  useLocale();
  const detail = useInvocationDetail(runId, item.archive_id ? 'tools' : 'activity', item.archive_id ?? item.activity_id, open, active);
  const row = detail.row;
  const observed = { ...item, arguments: row?.effective_arguments ?? row?.arguments ?? item.arguments,
    response: row?.response ?? item.response, error: row?.error ?? item.error };
  const failed = row?.state === 'failed' || row?.state === 'interrupted' || toolFailed(observed);
  const completed = item.type === "tool_completed";
  const pending = !completed && active;
  const status = failed ? t("Failed") : completed ? t("Completed") : pending ? t("Running") : t("Stopped");
  const Icon = failed ? CircleAlert : completed ? Check : pending ? LoaderCircle : Square;
  const clock = useActivityClock(now === undefined && pending);
  const response = observed.response;
  const presentation = toolPresentation(observed);
  const hasInput = hasToolData(presentation.input);
  const hasOutput = hasToolData(response);
  const hasError = hasToolData(observed.error);
  const hasDetails = hasInput || hasOutput || hasError || Boolean(item.archive_id || item.activity_id);
  const className = `run-tool${failed ? ' is-error' : ''}`;
  const heading = <>
      <Icon size={12} className={pending ? "run-activity-spinner" : undefined} aria-label={status} />
      <span className="run-tool-name" title={presentation.name}>{presentation.name}</span>
      <Duration milliseconds={toolElapsed(item, active, now ?? clock)} />
      {hasDetails && <ChevronRight size={12} className="run-tool-chevron" aria-hidden="true" />}
    </>;
  if (!hasDetails) return <div className={className} data-activity-id={item.id}>
    <div className="run-tool-summary">{heading}</div>
  </div>;
  return <details className={className} open={open} data-activity-id={item.id}
    onToggle={event => { if (event.target === event.currentTarget) onOpen(item.id, event.currentTarget.open); }}>
    <summary className="run-tool-summary">{heading}</summary>
    {open ? <div className="run-tool-details">
      {(item.archive_id || item.activity_id) && !row && !detail.error && <p role="status">{t('Loading activity…')}</p>}
      {hasInput && <section aria-label={t('Input')}><span>{t('Input')}</span><ToolData value={presentation.input} /></section>}
      {hasOutput && <section aria-label={t('Output')}><span>{t('Output')}</span><ToolData value={response} /></section>}
      {hasError && <section aria-label={t('Error')}><span>{t('Error')}</span><ToolData value={observed.error} /></section>}
      {(row ? row.truncated || row.response_complete === false : item.truncated) ? <small>{t("Details incomplete")}</small> : null}
      {detail.error ? <p role="alert">{detail.error} <button type="button" className="conversation-activity-action" onClick={detail.retry}>{t("Retry")}</button></p> : null}
    </div> : null}
  </details>;
});

export const RunActivityText = memo(function RunActivityText({ item, runId, active = false }: {
  item: RunActivityItem; runId?: string; active?: boolean;
}) {
  if (item.kind === 'model_reasoning') return <ReasoningActivity item={item} runId={runId} active={active} />;
  return <div className="conversation-message-bubble" data-activity-id={item.id}>
    {item.activity_id && item.preview_truncated ? <ArchivedProgress item={item} runId={runId} active={active} />
      : <div className={item.type === "agent_progress" ? "run-activity-progress" : "run-activity-text"}>
        {item.type === "agent_message" ? <MarkdownMessage content={item.text ?? ""} /> : <p>{item.text}</p>}
      </div>}
  </div>;
});

function ArchivedProgress({ item, runId, active }: { item: RunActivityItem; runId?: string; active: boolean }) {
  useLocale();
  const [open, setOpen] = useState(false);
  const detail = useInvocationDetail(runId, 'activity', item.activity_id, open, active);
  return <div className="run-activity-progress">
    <p>{open ? detail.row?.text ?? item.text : item.text}</p>
    <button type="button" className="conversation-activity-action" aria-expanded={open} onClick={() => setOpen(value => !value)}>{t(open ? 'Collapse' : 'Show full activity')}</button>
    {open && !detail.row && !detail.error && <p role="status">{t('Loading activity…')}</p>}
    {open && detail.error && <p role="alert">{detail.error} <button type="button" className="conversation-activity-action" onClick={detail.retry}>{t('Retry')}</button></p>}
  </div>;
}

export function RunActivityStatus({ waiting = false, stopping = false, onStop }: {
  waiting?: boolean;
  stopping?: boolean;
  onStop?: () => void;
}) {
  useLocale();
  return <div className="run-activity-footer">
      {onStop ? <button type="button" className="run-stop-button" disabled={stopping} onClick={onStop}>
        <Square size={10} aria-hidden="true" />{stopping ? t("Stopping…") : t("Stop")}
      </button> : null}
      <span role="status">
        {!waiting ? <LoaderCircle size={12} className="run-activity-spinner" aria-hidden="true" /> : null}
        {waiting ? t("Waiting") : t("Working")}
      </span>
    </div>;
}

/** A stable execution segment, with independent disclosure and scroll intent. */
export function ConversationExecution({ group, agentName, history = false, earlier }: {
  group: ExecutionGroup; agentName: string; history?: boolean;
  earlier?: { loading: boolean; error: string; load: () => void };
}) {
  useLocale();
  const regionId = useId();
  // Explicit user choice applies to the current phase. A live segment closes
  // once sealed, but remains manually inspectable afterward.
  const [choice, setChoice] = useState<{ phase: boolean; open: boolean }>();
  const phase = history || group.expanded;
  const manual = choice?.phase === phase;
  const open = manual ? choice.open : phase;
  const [openedTools, setOpenedTools] = useState<Set<string>>(() => new Set());
  // The bounded viewport is only an automatic live preview. Explicit reading
  // (opening a group or any tool) uses the main transcript's full height.
  const compact = group.running && !history && !manual && openedTools.size === 0;
  const viewport = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [away, setAway] = useState(false);
  const pending = group.running && group.items.some(item => item.type === 'tool_started');
  const now = useActivityClock(pending);
  const failed = group.items.filter(toolFailed).length;
  const stopped = !group.running && group.items.some(item => item.type === 'tool_started');
  const Icon = pending ? LoaderCircle : failed ? CircleAlert : stopped ? Square : Check;
  const label = pending ? t('Running') : failed ? t('Failed') : stopped ? t('Stopped') : t('Completed');
  useLayoutEffect(() => {
    const node = viewport.current;
    if (open && compact && node && follow.current) node.scrollTop = node.scrollHeight;
  }, [open, compact, group.items]);
  return <div className="conversation-execution" data-execution-id={group.id} data-running={pending || undefined}>
    <button type="button" className="conversation-execution-summary" aria-expanded={open} aria-controls={regionId}
      onClick={() => setChoice({ phase, open: !open })}>
      <Icon size={12} className={pending ? 'run-activity-spinner' : undefined} aria-label={label} />
      <span>{group.items.length} {t('tool calls')}</span>
      <Duration milliseconds={executionElapsed(group.items, group.running, now)} />
      {failed > 0 && <span className="conversation-execution-error">{t('{count} failed', { count: failed })}</span>}
      <ChevronRight size={12} className="conversation-execution-chevron" aria-hidden="true" />
    </button>
    {open && <div className="conversation-execution-body">
      {earlier && <button type="button" className="conversation-activity-action" disabled={earlier.loading} onClick={earlier.load}>
        {t(earlier.loading ? 'Loading activity…' : earlier.error ? 'Retry' : 'Load earlier activity')}
      </button>}
      {earlier?.error && <small className="run-activity-note" role="alert">{earlier.error}</small>}
      <div id={regionId} className={`conversation-execution-scroll${compact ? ' nowheel' : ''}`} data-compact={compact || undefined} ref={viewport} role="region"
        aria-label={t('Execution activity for {name}', { name: agentName })} tabIndex={0}
        onScroll={event => {
          // Nested tool output can scroll independently of the group.
          if (!compact || event.target !== event.currentTarget) return;
          const node = event.currentTarget;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
          setAway(!follow.current);
        }}>
        {group.items.map(item => <ToolActivity key={item.id} item={item} active={group.running} now={now} runId={group.run.run_id}
          open={openedTools.has(item.id)} onOpen={(id, expanded) => setOpenedTools(previous => {
            if (previous.has(id) === expanded) return previous;
            const next = new Set(previous);
            if (expanded) next.add(id); else next.delete(id);
            return next;
          })} />)}
      </div>
      {compact && away && group.running && <button type="button" className="conversation-execution-latest" onClick={() => {
        follow.current = true;
        if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
        setAway(false);
      }}><ArrowDown size={11} />{t('Latest activity')}</button>}
    </div>}
  </div>;
}
