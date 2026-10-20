import { describe, expect, it } from 'vitest';
import { conversationTranscript } from './conversationTranscript';
import { mergeRunActivity, withSavedActivity, type RunActivityItem } from './runActivity';
import type { ConversationMessage, ConversationRunSummary, RuntimeEvent } from '../types/world';

const time = (second: number) => `2026-10-13T00:00:0${second}Z`;
const message = (id: string, second: number, sequence: number): ConversationMessage => ({
  id, conversation_id: 'room', session_id: 'session', sender_kind: 'user', sender_name: 'You',
  content: id, mention_agent_ids: [], created_at: time(second), sequence,
});
const run: ConversationRunSummary = { run_id: 'run', agent_id: 'agent', status: 'running',
  started_at: time(1), tool_count: 1, tool_trace: [] };
const event = (id: string, type: string, second: number, payload: Record<string, unknown>): RuntimeEvent => ({
  id, type, timestamp: time(second), run_id: run.run_id, agent_id: run.agent_id, payload,
});

describe('conversation arrival order', () => {
  it('excludes empty/internal restored progress before grouping, while retaining lazy thinking metadata', () => {
    const rows: RunActivityItem[] = [
      { id: 'a', type: 'tool_completed', timestamp: time(1) },
      { id: 'status', type: 'agent_progress', kind: 'status', timestamp: time(2) },
      { id: 'space', type: 'agent_progress', text: ' \n\t', timestamp: time(2) },
      { id: 'internal', type: 'agent_progress', kind: 'context_usage', text: 'usage', timestamp: time(2) },
      { id: 'b', type: 'tool_completed', timestamp: time(3) },
      { id: 'thinking', type: 'agent_progress', kind: 'model_reasoning', provider_message_id: 'p', timestamp: time(4) },
    ];
    const raw = { items: rows, seen: [], truncated: false };
    const merged = withSavedActivity(raw, rows);
    expect(merged.items.map(row => row.id)).toEqual(['a', 'b', 'thinking']);
    // The projection also protects old in-memory states and paged archives.
    for (const state of [raw, merged]) {
      const entries = conversationTranscript([], [run], new Map([[run.run_id, state]]));
      expect(entries.map(entry => entry.kind)).toEqual(['execution', 'activity']);
      expect(entries[0]).toMatchObject({ items: [{ id: 'a' }, { id: 'b' }] });
      expect(entries[1]).toMatchObject({ item: { id: 'thinking' } });
    }
  });

  it('keeps narration before a user interruption inline while a final reply owns its contiguous work log', () => {
    const before = { ...message('before', 1, 1), sender_kind: 'agent' as const, run_id: run.run_id };
    const after = { ...message('after', 3, 3), sender_kind: 'agent' as const, run_id: run.run_id };
    const final = { ...message('final', 4, 4), sender_kind: 'agent' as const, run_id: run.run_id };
    const entries = conversationTranscript([before, message('followup', 2, 2), after, final],
      [{ ...run, status: 'succeeded', final_message_id: 'final' }], new Map());
    expect(entries.map(entry => entry.id)).toEqual(['before', 'followup', 'archive:run', 'final']);
    expect(entries[2]).toMatchObject({ kind: 'archive', messages: [after], timestamp: after.created_at });
  });

  it('orders each archive before its recorded final reply when timestamps tie, independently of Run input order', () => {
    const reply = (id: string, sequence: number, runId: string): ConversationMessage => ({
      ...message(id, 4, sequence), sender_kind: 'agent', run_id: runId, is_final: true,
    });
    const messages = [message('user', 4, 1), reply('answer-a', 2, 'a'), message('followup', 4, 3),
      reply('answer-b', 4, 'b'), reply('later-a', 5, 'a')];
    const a = { ...run, run_id: 'a', status: 'succeeded', final_message_id: 'answer-a', finished_at: time(6) };
    const b = { ...a, run_id: 'b', final_message_id: 'answer-b' };
    for (const runs of [[a, b], [b, a]]) {
      const streamed = { items: [{ id: 'streamed-final', type: 'agent_message' as const, text: 'answer-a', timestamp: time(4) }], seen: [], truncated: false };
      for (const cache of [new Map(), new Map([['a', streamed]])]) {
        const entries = conversationTranscript(messages, runs, cache);
        expect(entries.map(entry => entry.id)).toEqual(['user', 'archive:a', 'answer-a', 'followup', 'archive:b', 'answer-b', 'later-a']);
        expect(entries[1]).toMatchObject({ messages: [] });
      }
    }
  });

  it('keeps another Run\'s live activity between narration and the final reply when folding', () => {
    const other = { ...run, run_id: 'other', agent_id: 'other-agent' };
    const live = { items: [{ id: 'other-tool', type: 'tool_started' as const, timestamp: time(2), name: 'read' }], seen: [], truncated: false };
    const intro = { ...message('intro', 1, 1), sender_kind: 'agent' as const, run_id: run.run_id };
    const final = { ...intro, ...message('final', 3, 2), sender_kind: 'agent' as const };
    const entries = conversationTranscript([intro, final], [{ ...run, status: 'succeeded', final_message_id: 'final' }, other],
      new Map([['other', live]]));
    expect(entries.map(entry => entry.id)).toEqual(['intro', 'other:other-tool', 'archive:run', 'final']);
    expect(entries[2]).toMatchObject({ messages: [] });
  });

  it('folds separate replies from the same Agent independently without changing the message sequence', () => {
    const reply = (id: string, sequence: number, runId: string): ConversationMessage => ({
      ...message(id, 4, sequence), sender_kind: 'agent', run_id: runId,
    });
    const messages = [reply('intro-a', 1, 'a'), reply('answer-a', 2, 'a'), reply('intro-b', 3, 'b'), reply('answer-b', 4, 'b')];
    const runs = ['a', 'b'].map(id => ({ ...run, run_id: id, status: 'succeeded', final_message_id: `answer-${id}` }));
    const entries = conversationTranscript(messages, runs, new Map());
    expect(entries.map(entry => entry.id)).toEqual(['archive:a', 'answer-a', 'archive:b', 'answer-b']);
    expect(entries[0]).toMatchObject({ messages: [messages[0]] });
    expect(entries[2]).toMatchObject({ messages: [messages[2]] });
  });

  it('retains visible process when the recorded final reply is on another page or belongs to another Run', () => {
    const activity = mergeRunActivity(undefined, run, [event('tool', 'tool_completed', 3, { name: 'read', call_id: 'read' })]);
    const narration = { ...message('narration', 2, 2), sender_kind: 'agent' as const, run_id: run.run_id };
    const terminal = { ...run, status: 'succeeded', final_message_id: 'final', finished_at: time(6) };
    const cache = new Map([[run.run_id, activity]]);
    expect(conversationTranscript([narration, message('followup', 4, 3)], [terminal], cache, { hasBefore: true, hasAfter: true })
      .map(entry => entry.id)).toEqual(['narration', 'run:tool', 'followup']);
    const foreign = { ...message('final', 5, 4), sender_kind: 'agent' as const, run_id: 'another-run' };
    expect(conversationTranscript([narration, foreign], [terminal], cache).map(entry => entry.id))
      .toEqual(['narration', 'run:tool', 'final']);
  });

  it('places legacy and interrupted history at its recorded start without treating the last narration as a final reply', () => {
    const narration = { ...message('narration', 2, 2), sender_kind: 'agent' as const, run_id: run.run_id };
    for (const status of ['succeeded', 'failed', 'interrupted']) {
      const terminal = { ...run, status, finished_at: time(6) };
      const entries = conversationTranscript([message('user', 0, 1), narration], [terminal], new Map());
      expect(entries.map(entry => entry.id)).toEqual(['user', 'archive:run', 'narration']);
      expect(entries[1]).toMatchObject({ timestamp: run.started_at, messages: [] });
    }
    const activity = mergeRunActivity(undefined, run, [event('tool', 'tool_completed', 3, { name: 'read' })]);
    expect(conversationTranscript([narration, message('followup', 4, 3)], [{ ...run, status: 'succeeded' }],
      new Map([[run.run_id, activity]]), { hasBefore: true }).map(entry => entry.id))
      .toEqual(['narration', 'run:tool', 'followup']);
  });
  it('keeps distinct execution segments when specialists reuse a provider call ID', () => {
    const activity = mergeRunActivity(undefined, run, [
      event('builder', 'tool_started', 3, { name: 'read', call_id: 'same', role: 'builder' }),
      event('handoff', 'agent_progress', 2, { kind: 'status', text: 'Build from the plan' }),
      event('planner', 'tool_started', 1, { name: 'read', call_id: 'same', role: 'planner' }),
    ]);
    const entries = conversationTranscript([], [run], new Map([[run.run_id, activity]]));
    const groups = entries.filter(entry => entry.kind === 'execution');
    expect(groups).toHaveLength(2);
    expect(new Set(groups.map(group => group.id)).size).toBe(2);
    expect(groups.map(group => group.items[0].role)).toEqual(['planner', 'builder']);
  });
  it('interleaves followups with individual activity and keeps a streaming bubble at its first position', () => {
    const events = [event('reply', 'agent_message', 2, { text: 'Checking', provider_message_id: 'reply' }),
      event('b', 'tool_started', 3, { name: 'b', call_id: 'b' })];
    const initial = mergeRunActivity(undefined, run, [...events].reverse());
    const messages = [message('first', 0, 1), message('followup', 4, 2)];
    const before = conversationTranscript(messages, [run], new Map([[run.run_id, initial]]));
    expect(before.map(item => item.id)).toEqual(['first', 'run:reply', 'run:b', 'followup']);
    const updated = mergeRunActivity(initial, run, [
      event('c', 'tool_started', 6, { name: 'c', call_id: 'c' }),
      event('updated', 'agent_message', 5, { text: 'Checking resources', provider_message_id: 'reply' }),
      event('b-done', 'tool_completed', 5, { name: 'b', call_id: 'b', response: 'done' }),
    ]);
    expect(conversationTranscript(messages, [run], new Map([[run.run_id, updated]])).map(item => item.id))
      .toEqual(['first', 'run:reply', 'run:b', 'followup', 'run:c']);
  });

  it('reconciles REST and websocket by message identity and retains the order on reload', () => {
    const messages = [message('first', 0, 1), { ...message('reply', 2, 2), sender_kind: 'agent' as const },
      message('followup', 4, 3)];
    const activity = mergeRunActivity(undefined, run, [event('snapshot', 'agent_message', 5,
      { text: 'Checking', provider_message_id: 'provider-reply', conversation_message_id: 'reply' })]);
    expect(conversationTranscript(messages, [run], new Map([[run.run_id, activity]])).map(item => item.id))
      .toEqual(['first', 'reply', 'followup']);
    expect(conversationTranscript(messages, [], new Map()).map(item => item.id)).toEqual(['first', 'reply', 'followup']);
  });

  it('groups only adjacent same-Run tools and retains segments after completion and reload', () => {
    const trace = [
      event('a', 'tool_started', 1, { call_id: 'a', name: 'read' }),
      event('b', 'tool_started', 2, { call_id: 'b', name: 'read' }),
      event('a-done', 'tool_completed', 3, { call_id: 'a', name: 'read' }),
      event('b-done', 'tool_completed', 3, { call_id: 'b', name: 'read' }),
      event('plan', 'agent_progress', 4, { kind: 'reasoning_summary', text: 'Compare the files' }),
      event('c', 'tool_started', 5, { call_id: 'c', name: 'compare' }),
      event('c-done', 'tool_completed', 6, { call_id: 'c', name: 'compare' }),
    ];
    const active = mergeRunActivity(undefined, run, [...trace].reverse());
    const messages = [message('user', 0, 1)];
    const live = conversationTranscript(messages, [run], new Map([[run.run_id, active]]));
    expect(live.map(entry => entry.kind)).toEqual(['message', 'execution', 'activity', 'execution']);
    expect(live[1]).toMatchObject({ id: 'run:a', expanded: false, items: [{ id: 'a' }, { id: 'b' }] });
    expect(live[3]).toMatchObject({ id: 'run:c', expanded: true });
    const terminal = { ...run, status: 'succeeded', final_message_id: 'answer', tool_trace: trace.map(item => ({ ...item.payload,
      id: item.id, timestamp: item.timestamp, type: item.type })) };
    const restored = mergeRunActivity(undefined, terminal, []);
    const final = { ...message('answer', 7, 2), sender_kind: 'agent' as const, run_id: run.run_id };
    const ended = conversationTranscript([...messages, final], [terminal], new Map([[run.run_id, restored]]));
    expect(ended.map(entry => entry.id)).toEqual(['user', 'archive:run', 'answer']);
    expect(conversationTranscript([...messages, final], [terminal], new Map()).map(entry => entry.id))
      .toEqual(['user', 'archive:run', 'answer']);
  });

  it('splits for user followups and other Runs without hiding unfinished calls', () => {
    const other = { ...run, run_id: 'other', agent_id: 'other-agent' };
    const a = mergeRunActivity(undefined, run, [event('c', 'tool_started', 5, { name: 'c' }),
      event('a', 'tool_started', 1, { name: 'a' })]);
    const b = mergeRunActivity(undefined, other, [{ ...event('b', 'tool_started', 3, { name: 'b' }), run_id: 'other' }]);
    const result = conversationTranscript([message('followup', 2, 1)], [run, other], new Map([['run', a], ['other', b]]));
    expect(result.map(entry => entry.id)).toEqual(['run:a', 'followup', 'other:b', 'run:c']);
    expect(result.filter(entry => entry.kind === 'execution').every(entry => entry.expanded)).toBe(true);
  });

  it('restricts whole-Run checkpoints to the visible chronological page', () => {
    const activity = mergeRunActivity(undefined, run, [event('old', 'tool_started', 1, { name: 'old' }),
      event('visible', 'tool_started', 3, { name: 'visible' }), event('new', 'tool_started', 6, { name: 'new' })]);
    const entries = conversationTranscript([message('first', 2, 1), message('last', 4, 2)], [run],
      new Map([['run', activity]]), { hasBefore: true, hasAfter: true });
    expect(entries.map(entry => entry.id)).toEqual(['first', 'run:visible', 'last']);
  });

  it('keeps the latest work visible during the next narration until new execution or Run completion', () => {
    const activity = mergeRunActivity(undefined, run, [event('done', 'tool_completed', 2, { name: 'read', call_id: 'read' }),
      event('start', 'tool_started', 1, { name: 'read', call_id: 'read' })]);
    const entries = conversationTranscript([{ ...message('narration', 3, 1), sender_kind: 'agent' }], [run], new Map([['run', activity]]));
    expect(entries[0]).toMatchObject({ kind: 'execution', expanded: true });
    const stopped = conversationTranscript([], [{ ...run, status: 'succeeded' }], new Map([['run', activity]]));
    expect(stopped[0]).toMatchObject({ kind: 'archive', id: 'archive:run' });
  });

  it('retains microsecond order and tool identity when the checkpoint repairs a missing start', () => {
    const reply = { ...message('reply', 1, 1), created_at: '2026-10-13T00:00:01.000600Z' };
    const start = { ...event('start', 'tool_started', 1, { call_id: 'c', name: 'read' }), timestamp: '2026-10-13T00:00:01.000100+00:00' };
    const done = { ...event('done', 'tool_completed', 1, { call_id: 'c', name: 'read' }), timestamp: '2026-10-13T00:00:01.000900Z' };
    const early = mergeRunActivity(undefined, run, [done]);
    const before = conversationTranscript([reply], [run], new Map([['run', early]]));
    const repaired = mergeRunActivity(early, run, [start]);
    const after = conversationTranscript([reply], [run], new Map([['run', repaired]]));
    expect(before.map(entry => entry.id)).toEqual(['reply', 'run:done']);
    expect(after.map(entry => entry.id)).toEqual(['run:done', 'reply']);
  });
});
