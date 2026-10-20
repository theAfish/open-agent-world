import type { ConversationMessage, ConversationRunSummary } from '../types/world';
import { visibleActivity, withoutFinalReply, type RunActivityItem, type RunActivityState } from './runActivity';
import { compareTimestamp } from './activityTiming';

type MessageEntry = { kind: 'message'; id: string; timestamp: string; message: ConversationMessage };
type ActivityEntry = { kind: 'activity'; id: string; timestamp: string; run: ConversationRunSummary; item: RunActivityItem };
type ArchiveEntry = { kind: 'archive'; id: string; timestamp: string; run: ConversationRunSummary; messages: ConversationMessage[] };
export type ExecutionGroup = {
  kind: 'execution'; id: string; timestamp: string; run: ConversationRunSummary;
  items: RunActivityItem[]; running: boolean; expanded: boolean; truncated: boolean;
};
export type TranscriptEntry = MessageEntry | ActivityEntry | ExecutionGroup | ArchiveEntry;
export type TranscriptWindow = { hasBefore?: boolean; hasAfter?: boolean; archiveCompleted?: boolean };
const isRunning = (run: ConversationRunSummary) => ['created', 'running', 'waiting'].includes(run.status);

/** One projection for live and restored history. Groups are presentation only:
 * adjacent tool calls in the same Run, separated by every message/other actor.
 * Completion updates the original call; it never moves activity to the reply. */
export function conversationTranscript(messages: ConversationMessage[], runs: ConversationRunSummary[],
  activities: Map<string, RunActivityState>, window: TranscriptWindow = {}): TranscriptEntry[] {
  const ids = new Set(messages.map(message => message.id));
  const finals = new Map<string, ConversationRunSummary>();
  const entries: (MessageEntry | ActivityEntry | ArchiveEntry)[] = messages.map(message => ({ kind: 'message', id: message.id,
    timestamp: message.created_at, message }));
  const inWindow = (timestamp: string) => !messages.length ||
    ((!window.hasBefore || compareTimestamp(timestamp, messages[0].created_at) >= 0) &&
      (!window.hasAfter || compareTimestamp(timestamp, messages.at(-1)!.created_at) <= 0));
  for (const run of runs) {
    const replies = messages.filter(message => message.run_id === run.run_id && message.sender_kind === 'agent');
    const final = replies.find(message => message.id === run.final_message_id);
    const activity = activities.get(run.run_id);
    // Legacy providers lack identity; only their final snapshot can be matched
    // by content, and only after the Run ends.
    const source = isRunning(run) ? activity : withoutFinalReply(activity,
      final?.content ?? (!run.final_message_id && replies.at(-1)?.is_final !== false ? replies.at(-1)?.content : undefined));
    const completed = window.archiveCompleted !== false
      && ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.status);
    let archived = false;
    if (completed && final) {
      finals.set(final.id, run);
      archived = true;
    } else if (completed && !run.final_message_id) {
      // Legacy/stopped Runs have no final relation. Keep their ordinary
      // messages inline and their durable history at the recorded Run start.
      const timestamp = run.started_at || replies[0]?.created_at || run.finished_at || '';
      if (inWindow(timestamp)) {
        entries.unshift({ kind: 'archive', id: `archive:${run.run_id}`, timestamp, run, messages: [] });
        archived = true;
      }
    }
    for (const item of source?.items ?? []) {
      if (!visibleActivity(item)) continue;
      if (archived && item.type !== 'agent_message') continue;
      if (item.conversation_message_id && ids.has(item.conversation_message_id)) continue;
      const timestamp = item.timestamp || run.started_at || replies[0]?.created_at || '';
      // Run summaries cover whole Runs, whereas the message window is paged.
      if (!inWindow(timestamp)) continue;
      entries.push({ kind: 'activity', id: `${run.run_id}:${item.id}`, timestamp, run, item });
    }
  }
  entries.sort((a, b) => {
    if (a.kind === 'message' && b.kind === 'message' && a.message.sequence !== undefined && b.message.sequence !== undefined) {
      return a.message.sequence - b.message.sequence;
    }
    return compareTimestamp(a.timestamp, b.timestamp);
  });
  const result: TranscriptEntry[] = [];
  const latestExecution = new Map<string, ExecutionGroup>();
  for (const entry of entries) {
    const completed = entry.kind === 'message' ? finals.get(entry.id) : undefined;
    if (completed) {
      const folded: ConversationMessage[] = [];
      let previous = result.at(-1);
      // Fold in place after sorting. Other messages, actors, and execution
      // groups stay boundaries; the resulting Work log is never re-sorted.
      while (completed.status === 'succeeded' && previous?.kind === 'message'
        && previous.message.sender_kind === 'agent' && previous.message.run_id === completed.run_id) {
        folded.unshift(previous.message);
        result.pop();
        previous = result.at(-1);
      }
      result.push({ kind: 'archive', id: `archive:${completed.run_id}`, timestamp: folded[0]?.created_at ?? entry.timestamp,
        run: completed, messages: folded });
    }
    if (entry.kind !== 'activity' || !entry.item.type.startsWith('tool_')) {
      result.push(entry);
      continue;
    }
    const previous = result.at(-1);
    if (previous?.kind === 'execution' && previous.run.run_id === entry.run.run_id) {
      previous.items.push(entry.item);
      previous.expanded ||= previous.running && entry.item.type === 'tool_started';
    } else {
      const running = isRunning(entry.run);
      const group: ExecutionGroup = { kind: 'execution', id: entry.id, timestamp: entry.timestamp, run: entry.run,
        items: [entry.item], running, expanded: running && entry.item.type === 'tool_started',
        truncated: !latestExecution.has(entry.run.run_id) && Boolean(activities.get(entry.run.run_id)?.truncated) };
      result.push(group);
      latestExecution.set(entry.run.run_id, group);
    }
  }
  // Keep the latest work open while the Agent narrates or awaits its next turn.
  for (const group of latestExecution.values()) if (group.running) group.expanded = true;
  return result;
}
