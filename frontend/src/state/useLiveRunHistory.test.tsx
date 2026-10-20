// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useLiveRunHistory } from './useLiveRunHistory';
import type { RunActivityState } from './runActivity';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('repairs thinking after a refresh using bounded metadata and never eagerly loads completed runs or bodies', async () => {
  const list = vi.spyOn(worldApi, 'getRunActivity').mockResolvedValue({ items: [
    { id: 'thinking:p', kind: 'model_reasoning', type: 'agent_progress', provider_message_id: 'p', streaming: true },
  ], total: 500, next_offset: null });
  const detail = vi.spyOn(worldApi, 'getRunReasoningText');
  const active = { run_id: 'active', agent_id: 'agent', status: 'running', tool_count: 500, tool_trace: [] };
  const complete = { ...active, run_id: 'complete', status: 'succeeded' };
  const activities = new Map<string, RunActivityState>();
  const view = renderHook(({ runs }) => useLiveRunHistory(runs, activities), { initialProps: { runs: [active, complete] } });
  await waitFor(() => expect(view.result.current.activities.get('active')?.items[0].kind).toBe('model_reasoning'));
  expect(list).toHaveBeenCalledTimes(1);
  expect(list).toHaveBeenCalledWith('active', 0, true);
  expect(detail).not.toHaveBeenCalled();
  view.rerender({ runs: [complete] });
  expect(view.result.current.activities.has('active')).toBe(false);
  expect(list).toHaveBeenCalledTimes(1);
});

it('loads earlier pages only on request and retains them beyond the live cache limit', async () => {
  const rows = Array.from({ length: 260 }, (_, i) => ({ id: `tool-${i}`, activity_id: `tool-${i}`,
    call_id: String(i), type: 'tool_completed' as const, timestamp: new Date(Date.UTC(2026, 9, 19, 0, 0, i)).toISOString() }));
  const list = vi.spyOn(worldApi, 'getRunActivity').mockImplementation(async (_id, offset = 0, latest = false) => ({
    items: latest ? rows.slice(-50) : rows.slice(offset, offset + 50), total: 260,
    next_offset: latest || offset + 50 >= rows.length ? null : offset + 50,
  }));
  const run = { run_id: 'run', agent_id: 'agent', status: 'running', tool_count: 260, tool_trace: [] };
  const view = renderHook(() => useLiveRunHistory([run], new Map()));
  await waitFor(() => expect(view.result.current.activities.get('run')?.items).toHaveLength(50));
  expect(list).toHaveBeenCalledTimes(1);
  expect(view.result.current.activities.get('run')?.truncated).toBe(true);
  for (let i = 0; i < 6; i++) {
    await act(() => view.result.current.loadEarlier('run'));
    expect(list).toHaveBeenLastCalledWith('run', i * 50);
  }
  expect(view.result.current.activities.get('run')?.items.map(row => row.id)).toEqual(rows.map(row => row.id));
  expect(view.result.current.activities.get('run')?.truncated).toBe(false);
});

it('keeps paged-out final history readable on request without eagerly polling completed Runs', async () => {
  const list = vi.spyOn(worldApi, 'getRunActivity').mockResolvedValue({
    items: [{ id: 'old', type: 'tool_completed', name: 'read' }], total: 1, next_offset: null,
  });
  const run = { run_id: 'run', agent_id: 'agent', status: 'succeeded', final_message_id: 'off-page', tool_count: 1, tool_trace: [] };
  const view = renderHook(({ runs }) => useLiveRunHistory(runs, new Map()), { initialProps: { runs: [run] } });
  expect(list).not.toHaveBeenCalled();
  await act(() => view.result.current.loadEarlier('run'));
  expect(list).toHaveBeenCalledOnce();
  expect(list).toHaveBeenCalledWith('run', 0);
  expect(view.result.current.activities.get('run')?.items[0].id).toBe('old');
  view.rerender({ runs: [] });
  expect(view.result.current.activities.has('run')).toBe(false);
});
