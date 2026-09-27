import { worldApi } from '../api/client';
import type { BoardSnapshot, BoardSummary } from './TaskBoard';

type Kind = 'summary' | 'full';
type Snapshot = { board?: BoardSnapshot; summary?: BoardSummary; error: string; readErrors: Partial<Record<Kind, string>> };
export type BoardCacheEntry = {
  snapshot: Snapshot;
  listeners: Set<() => void>;
  pending: Map<string, Promise<void>>;
  stamps: Partial<Record<Kind, { token: string; at: number }>>;
  generations: Record<Kind, number>;
};
// Only fetched documents are bounded; unsaved drafts are owned by nodeSurfaces.
const cache = new Map<string, BoardCacheEntry>();
const MAX_INACTIVE_BOARDS = 48;
const FRESH_MS = 3000;

export function boardCacheEntry(key: string): BoardCacheEntry {
  let entry = cache.get(key);
  if (!entry) entry = { snapshot: { error: '', readErrors: {} }, listeners: new Set(), pending: new Map(), stamps: {}, generations: { summary: 0, full: 0 } };
  cache.delete(key); cache.set(key, entry);
  // Subscriptions attach after React commits. Trimming here could evict a board
  // rendered earlier in the same batch, before its subscriber has attached.
  return entry;
}
function trim() {
  let inactive = [...cache.values()].filter(entry => !entry.listeners.size).length;
  for (const [key, entry] of cache) {
    if (inactive <= MAX_INACTIVE_BOARDS) break;
    if (!entry.listeners.size && !entry.pending.size) { cache.delete(key); inactive--; }
  }
}
function publish(entry: BoardCacheEntry, snapshot: Snapshot) {
  entry.snapshot = snapshot;
  entry.listeners.forEach(listener => listener());
}
export function subscribeBoard(entry: BoardCacheEntry, listener: () => void) {
  entry.listeners.add(listener);
  return () => { entry.listeners.delete(listener); trim(); };
}
export function boardError(entry: BoardCacheEntry, error: string) {
  publish(entry, { ...entry.snapshot, error });
}
export function acceptBoard(entry: BoardCacheEntry, next: BoardSummary | BoardSnapshot, token: string) {
  const current = entry.snapshot;
  if (next.revision < (current.summary?.revision ?? -1)) return;
  entry.generations.summary++;
  const stamp = { token, at: Date.now() };
  entry.stamps.summary = stamp;
  if ('value' in next) { entry.stamps.full = stamp; entry.generations.full++; }
  publish(entry, {
    board: 'value' in next ? next : current.board,
    summary: { revision: next.revision, summary: next.summary }, error: '',
    readErrors: 'value' in next ? {} : { ...current.readErrors, summary: undefined },
  });
}
export function loadBoard(entry: BoardCacheEntry, id: string, session: string | null, kind: Kind, token: string, force = false): Promise<void> {
  const stamp = entry.stamps[kind];
  const usable = kind === 'summary' ? !!entry.snapshot.summary : !!entry.snapshot.board
    && entry.snapshot.board.revision === entry.snapshot.summary?.revision;
  if (!force && usable && stamp?.token === token && Date.now() - stamp.at < FRESH_MS) return Promise.resolve();
  // A full read also supplies the preview. Concurrent surfaces share that read.
  const pending = entry.pending.get(`full:${token}`) ?? entry.pending.get(`${kind}:${token}`);
  if (pending) return pending;
  const key = `${kind}:${token}`;
  const generation = ++entry.generations[kind];
  if (entry.snapshot.readErrors[kind]) publish(entry, { ...entry.snapshot, readErrors: { ...entry.snapshot.readErrors, [kind]: undefined } });
  const request = (kind === 'summary' ? worldApi.getNodeDocumentSummary(id, session) : worldApi.getNodeDocument(id, session))
    .then(value => acceptBoard(entry, value as unknown as BoardSummary | BoardSnapshot, token))
    .catch(error => {
      if (entry.generations[kind] === generation) publish(entry, { ...entry.snapshot,
        readErrors: { ...entry.snapshot.readErrors, [kind]: error instanceof Error ? error.message : String(error) } });
    })
    .finally(() => { entry.pending.delete(key); trim(); });
  entry.pending.set(key, request);
  return request;
}

/** Test isolation; production cache lifetime is the current application page. */
export function clearTaskBoardCache() { cache.clear(); }

export function forgetTaskBoardCache(nodeIds: string | string[]) {
  const ids = new Set(typeof nodeIds === 'string' ? [nodeIds] : nodeIds);
  for (const [key] of cache) if (ids.has((JSON.parse(key) as string[])[0])) cache.delete(key);
}
