import { useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import type { ConversationMessage, ConversationRunSummary } from '../types/world';
import type { RunActivityItem } from '../state/runActivity';
import { ConversationExecution, Duration, RunActivityText } from './ConversationActivity';
import { elapsedMilliseconds } from '../state/activityTiming';
import { conversationTranscript } from '../state/conversationTranscript';
import { MarkdownMessage } from './MarkdownMessage';

const NO_MESSAGES: ConversationMessage[] = [];
function HistoryPage({ run, messages }: { run: ConversationRunSummary; messages: ConversationMessage[] }) {
  const [rows, setRows] = useState<RunActivityItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [incomplete, setIncomplete] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError('');
    worldApi.getRunActivity(run.run_id, offset).then(page => {
      if (!current) return;
      if (page.next_offset !== null && page.next_offset <= offset) throw new Error('Invalid activity page');
      setRows(previous => [...new Map([...previous, ...page.items].map(row => [row.id, row])).values()]);
      setNext(page.next_offset);
      setIncomplete(Boolean(page.incomplete));
    }).catch(cause => { if (current) setError(apiErrorMessage(cause)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [run.run_id, offset, retry]);
  const entries = conversationTranscript(messages, [run],
    new Map([[run.run_id, { items: rows, seen: [], truncated: false }]]), { archiveCompleted: false });
  return <div className="conversation-history-content">
    {incomplete && <small className="run-activity-note">{t('Some older activity was not recorded.')}</small>}
    {entries.map(entry => entry.kind === 'execution'
      ? <ConversationExecution key={entry.id} group={entry} agentName={run.agent_id} history />
      : entry.kind === 'activity' ? <RunActivityText key={entry.id} item={entry.item} runId={run.run_id} active={false} />
      : entry.kind === 'message' ? <div key={entry.id} className="conversation-message-bubble"><MarkdownMessage content={entry.message.content} /></div> : null)}
    {loading && <small className="run-activity-note" role="status">{t('Loading activity…')}</small>}
    {error && <small className="run-activity-note" role="alert">{error} <button type="button" className="conversation-activity-action" onClick={() => setRetry(value => value + 1)}>{t('Retry')}</button></small>}
    {!loading && !error && !entries.length && <small className="run-activity-note">{t('No saved activity for this run.')}</small>}
    {!loading && !error && next !== null && <button type="button" className="conversation-activity-action" onClick={() => setOffset(next)}>{t('Load more activity')}</button>}
  </div>;
}

/** Metadata pages and individual bodies have separate, explicit read intent. */
export function ConversationRunHistory({ run, messages = NO_MESSAGES }: { run: ConversationRunSummary; messages?: ConversationMessage[] }) {
  useLocale();
  const [open, setOpen] = useState(false);
  return <details className="conversation-history" open={open} data-history-run-id={run.run_id}
    onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
    <summary><span>{t('Work log')}</span>
      <Duration milliseconds={elapsedMilliseconds(run.started_at, run.finished_at)} />
      <ChevronRight size={12} className="conversation-history-chevron" aria-hidden="true" />
    </summary>
    {open && <HistoryPage key={run.run_id} run={run} messages={messages} />}
  </details>;
}
