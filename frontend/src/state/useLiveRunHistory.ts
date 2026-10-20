import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { withSavedActivity, type RunActivityItem, type RunActivityState } from './runActivity';
import type { ConversationRunSummary } from '../types/world';

type Page = Awaited<ReturnType<typeof worldApi.getRunActivity>>;
type Earlier = { items: RunActivityItem[]; next: number | null; loading: boolean; error: string };

/** Repair only active Runs. Completed history is read on explicit disclosure. */
export function useLiveRunHistory(runs: ConversationRunSummary[], activities: Map<string, RunActivityState>) {
  const ids = JSON.stringify(runs.filter(run => ['created', 'running', 'waiting'].includes(run.status)).map(run => run.run_id).sort());
  const visibleIds = JSON.stringify(runs.map(run => run.run_id).sort());
  const [saved, setSaved] = useState<Map<string, Page>>(() => new Map());
  const [earlier, setEarlier] = useState<Map<string, Earlier>>(() => new Map());
  const requests = useRef(new Set<string>());
  const visibleRuns = useRef(new Set<string>());
  useEffect(() => {
    visibleRuns.current = new Set(JSON.parse(visibleIds) as string[]);
    setEarlier(previous => new Map([...previous].filter(([id]) => visibleRuns.current.has(id))));
    return () => { visibleRuns.current.clear(); };
  }, [visibleIds]);
  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    const runIds: string[] = JSON.parse(ids);
    async function read() {
      const results = await Promise.allSettled(runIds.map(async id => [id, await worldApi.getRunActivity(id, 0, true)] as const));
      if (!current) return;
      setSaved(previous => new Map(runIds.flatMap((id, index) => {
        const result = results[index];
        return result.status === 'fulfilled' ? [result.value] : previous.has(id) ? [[id, previous.get(id)!] as const] : [];
      })));
      if (runIds.length) timer = setTimeout(() => void read(), 3000);
    }
    void read();
    return () => { current = false; clearTimeout(timer); };
  }, [ids]);
  const loadEarlier = useCallback(async (id: string) => {
    if (!visibleRuns.current.has(id) || requests.current.has(id)) return;
    const previous = earlier.get(id) ?? { items: [], next: 0, loading: false, error: '' };
    if (previous.next === null) return;
    requests.current.add(id);
    setEarlier(rows => new Map(rows).set(id, { ...previous, loading: true, error: '' }));
    try {
      const page = await worldApi.getRunActivity(id, previous.next);
      if (!visibleRuns.current.has(id)) return;
      if (page.next_offset !== null && page.next_offset <= previous.next) throw new Error('Invalid activity page');
      setEarlier(rows => new Map(rows).set(id, { items: [...previous.items, ...page.items], next: page.next_offset, loading: false, error: '' }));
    } catch (cause) {
      if (visibleRuns.current.has(id)) setEarlier(rows => new Map(rows).set(id, { ...previous, loading: false, error: apiErrorMessage(cause) }));
    } finally { requests.current.delete(id); }
  }, [earlier]);
  const restored = useMemo(() => {
    const next = new Map(activities);
    const active = new Set(JSON.parse(ids) as string[]);
    // Paged history without its final reply retains inline execution groups.
    // These completed Runs may read older metadata on request, without polling.
    for (const id of JSON.parse(visibleIds) as string[]) {
      const page = active.has(id) ? saved.get(id) : undefined;
      const older = earlier.get(id);
      if (page || older) {
        const merged = withSavedActivity(next.get(id), [...older?.items ?? [], ...page?.items ?? []], older ? Infinity : undefined);
        // Explicitly loaded pages stay visible while the bounded live tail polls.
        merged.truncated = older ? older.next !== null : page
          ? page.total > merged.items.filter(item => item.type !== 'agent_message').length : merged.truncated;
        next.set(id, merged);
      }
    }
    return next;
  }, [activities, saved, earlier, ids, visibleIds]);
  return { activities: restored, earlier, loadEarlier };
}
