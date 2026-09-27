// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { buildCardDraft } from '../state/helpers';
import { useWorldStore } from '../state/worldStore';
import type { RuntimeEvent } from '../types/world';
import { useNodeActivity } from './useNodeActivity';

afterEach(cleanup);

it('ignores unrelated edits and output while keeping member and aggregate activity live', () => {
  const group = { id: 'group', ...buildCardDraft('legion', { x: 0, y: 0 }) };
  const member = { id: 'member', ...buildCardDraft('agent', { x: 0, y: 0 }), parent_id: group.id };
  const unrelated = { id: 'other', ...buildCardDraft('text', { x: 0, y: 0 }) };
  useWorldStore.setState({ cards: [group, member, unrelated], events: [] });
  let renders = 0;
  const single = renderHook(() => { renders++; return useNodeActivity(member); });
  const aggregate = renderHook(() => useNodeActivity(group, true));
  const initial = renders;
  const event = (id: string, type: string): RuntimeEvent => ({ id: 'event', node_id: id, type, timestamp: new Date().toISOString(), payload: {} });
  act(() => useWorldStore.setState(s => ({ cards: s.cards.map(c => c.id === 'other' ? { ...c, name: 'Changed' } : c), events: [event('other', 'run_failed')] })));
  expect(renders).toBe(initial);
  expect(aggregate.result.current.phase).toBe('idle');
  act(() => useWorldStore.setState({ events: [event(member.id, 'run_failed')] }));
  expect(single.result.current.phase).toBe('failed');
  expect(aggregate.result.current.phase).toBe('failed');
  act(() => useWorldStore.setState(s => ({ cards: s.cards.map(c => c.id === member.id ? { ...c, status: 'running' } : c) })));
  expect(aggregate.result.current).toEqual({ phase: 'running', running: 1, waiting: 0 });
  act(() => useWorldStore.setState(s => ({ cards: s.cards.map(c => c.id === member.id ? { ...c, parent_id: null } : c) })));
  expect(aggregate.result.current.phase).toBe('idle');
});
