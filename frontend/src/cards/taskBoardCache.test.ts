// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { acceptBoard, boardCacheEntry, clearTaskBoardCache, forgetTaskBoardCache, loadBoard, subscribeBoard } from './taskBoardCache';
import type { BoardSnapshot, BoardSummary } from './TaskBoard';

const board = (revision: number): BoardSnapshot => ({ revision, summary: { total: 1, done: 0, ready_ids: ['a'] },
  value: { tasks: [{ id: 'a', title: `Revision ${revision}`, description: '', status: 'todo', note: '', depends_on: [] }],
    execution: { default_executor_id: null, max_parallel: 1, pause_on_failure: true } } });
const key = (session: string) => JSON.stringify(['board', 'session', session, false]);
beforeEach(() => clearTaskBoardCache());
afterEach(() => vi.restoreAllMocks());

it('shares a pending full read and does not let its old response overwrite a completed mutation', async () => {
  let finish!: (value: BoardSnapshot) => void;
  const read = vi.spyOn(worldApi, 'getNodeDocument').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const summary = vi.spyOn(worldApi, 'getNodeDocumentSummary');
  const entry = boardCacheEntry(key('a'));
  const first = loadBoard(entry, 'board', 'a', 'full', 'initial');
  const preview = loadBoard(entry, 'board', 'a', 'summary', 'initial');
  expect(read).toHaveBeenCalledTimes(1);
  expect(summary).not.toHaveBeenCalled();
  acceptBoard(entry, board(3), 'edited');
  finish(board(1));
  await Promise.all([first, preview]);
  expect(entry.snapshot.board?.revision).toBe(3);
  expect(entry.snapshot.summary?.revision).toBe(3);
});

it('deduplicates summary remounts and rereads after a world invalidation', async () => {
  const read = vi.spyOn(worldApi, 'getNodeDocumentSummary').mockResolvedValue(board(1) as BoardSummary);
  const entry = boardCacheEntry(key('a'));
  await Promise.all([loadBoard(entry, 'board', 'a', 'summary', 'event-1'), loadBoard(entry, 'board', 'a', 'summary', 'event-1')]);
  await loadBoard(boardCacheEntry(key('a')), 'board', 'a', 'summary', 'event-1');
  expect(read).toHaveBeenCalledTimes(1);
  read.mockResolvedValue(board(2) as BoardSummary);
  await loadBoard(entry, 'board', 'a', 'summary', 'event-2');
  expect(read).toHaveBeenCalledTimes(2);
  expect(entry.snapshot.summary?.revision).toBe(2);
});

it('keeps sessions separate and deletion cannot resurrect an old in-flight cache entry', async () => {
  let finish!: (value: BoardSnapshot) => void;
  vi.spyOn(worldApi, 'getNodeDocument').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const a = boardCacheEntry(key('a'));
  const b = boardCacheEntry(key('b'));
  const read = loadBoard(a, 'board', 'a', 'full', 'initial');
  acceptBoard(b, board(8), 'b');
  expect(a.snapshot.board).toBeUndefined();
  forgetTaskBoardCache('board');
  const restored = boardCacheEntry(key('a'));
  finish(board(1));
  await read;
  expect(restored.snapshot.board).toBeUndefined();
  expect(restored).not.toBe(a);
  expect(boardCacheEntry(key('b'))).not.toBe(b);
});

it('keeps a large render batch shared until subscriptions attach, then bounds inactive entries', () => {
  const entries = Array.from({ length: 80 }, (_, index) => boardCacheEntry(key(String(index))));
  const unsubscribe = entries.map(entry => subscribeBoard(entry, () => {}));
  entries.forEach((entry, index) => expect(boardCacheEntry(key(String(index)))).toBe(entry));
  unsubscribe.forEach(stop => stop());
  expect(boardCacheEntry(key('0'))).not.toBe(entries[0]);
  expect(boardCacheEntry(key('79'))).toBe(entries[79]);
});

it.each(['before', 'after'])('retains full-read failures when a concurrent summary succeeds %s the failure', async order => {
  let finishSummary!: (value: BoardSummary) => void;
  let failFull!: (reason: Error) => void;
  vi.spyOn(worldApi, 'getNodeDocumentSummary').mockImplementation(() => new Promise(resolve => { finishSummary = resolve; }));
  vi.spyOn(worldApi, 'getNodeDocument').mockImplementation(() => new Promise((_resolve, reject) => { failFull = reject; }));
  const entry = boardCacheEntry(key('a'));
  const summary = loadBoard(entry, 'board', 'a', 'summary', 'initial');
  const full = loadBoard(entry, 'board', 'a', 'full', 'initial');
  if (order === 'before') { finishSummary({ revision: 1, summary: board(1).summary }); await summary; }
  failFull(new Error('Document unavailable'));
  await full;
  if (order === 'after') { finishSummary({ revision: 1, summary: board(1).summary }); await summary; }
  expect(entry.snapshot.summary?.revision).toBe(1);
  expect(entry.snapshot.readErrors.full).toBe('Document unavailable');
  expect(entry.snapshot.readErrors.summary).toBeUndefined();
});
