// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExecutionGraphView, readExecutionHistory, readProjectHistory } from './ExecutionGraph';
import { executionIndex, layoutGraph, laneMemory, trackPath } from './executionHistory';
import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot, ExecutionSourceHost } from '../plugins/executionSources';

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const node = (id: string, extra: Partial<ExecutionNode> = {}): ExecutionNode => ({ id, label: id, kind: 'agent', status: 'success', ...extra });
const edge = (source: string, target: string, kind: ExecutionEdge['kind'] = 'control'): ExecutionEdge => ({ id: source + ':' + target + ':' + kind, source, target, kind });
const history = (nodes: ExecutionNode[] = [node('A'), node('B'), node('C')], edges: ExecutionEdge[] = []): ExecutionSnapshot => ({
  history_id: 'session-history', revision: 1, run_id: 'run', phase: 'running', title: 'Study copper', nodes, edges,
});
function source(value = history()): ExecutionSourceHost {
  return { list: vi.fn().mockResolvedValue({ sources: [{ id: 'a', name: 'Researcher', type: 'agent' }] }),
    read: vi.fn().mockResolvedValue(value), subscribe: vi.fn(() => () => {}) };
}
it('leaves independent events unconnected; chronology does not fabricate threads', async () => {
  const index = executionIndex(history()), graph = layoutGraph(index, laneMemory());
  expect(graph.tracks).toEqual([]);
  expect(graph.height).toBeLessThan(200);
  const view = render(<ExecutionGraphView api={source()} />);
  await screen.findByRole('button', { name: 'A: success' });
  expect(view.container.querySelectorAll('[data-graph-edge]')).toHaveLength(0);
  expect(view.container.querySelectorAll('[data-kind="history"]')).toHaveLength(0);
  const nodes = [node('A'), node('B', { parent_id: 'A', dependency_ids: ['A'] })];
  expect(executionIndex({ ...history(nodes), edges: undefined }).edges.map(edge => edge.kind)).toEqual(['control']);
  expect(executionIndex(history(nodes)).edges).toEqual([]);
});
it('uses global chronology rather than placing all children under an execution', () => {
  const nodes = [
    node('context', { record_type: 'context', sequence: 0 }),
    node('task-a', { sequence: 1 }), node('task-b', { sequence: 2 }),
    node('a-edit', { parent_id: 'task-a', sequence: 4 }),
    node('b-check', { parent_id: 'task-b', sequence: 3 }),
    node('future', { record_type: 'planned', parent_id: 'context' }),
  ];
  expect(executionIndex(history(nodes)).activities.map(node => node.id)).toEqual(['task-a', 'task-b', 'b-check', 'a-edit']);
});
it('draws real forks and joins and keeps distant sources in the same graph', () => {
  const nodes = ['A','B','C','D'].map(id => node(id));
  const edges = [edge('A','B'),edge('A','C'),edge('B','D'),edge('C','D')];
  const graph = layoutGraph(executionIndex(history(nodes, edges)), laneMemory());
  expect(graph.rows[1].lane).not.toBe(graph.rows[2].lane);
  expect(graph.tracks.filter(track => track.edge)).toHaveLength(4);
  expect(graph.tracks.some(track => trackPath(track, lane => 24 + lane * 24).includes('A'))).toBe(true);
  const many = Array.from({ length: 20 }, (_, i) => node('E' + (i + 1)));
  const sparse = layoutGraph(executionIndex(history(many, [edge('E1','E20'),edge('E9','E20'),edge('E17','E20')])), laneMemory());
  expect(sparse.rows).toHaveLength(20);
  expect(sparse.tracks.filter(track => track.edge)).toHaveLength(3);
});
it('keeps ownership colors and lanes through completion, details and late dependencies', () => {
  const nodes = [
    node('a1', { actor_id: 'builder', workstream_id: 'a', status: 'running' }),
    node('b1', { actor_id: 'tester', workstream_id: 'b', status: 'running' }),
    node('a2', { actor_id: 'builder', workstream_id: 'a' }),
    node('b2', { actor_id: 'tester', workstream_id: 'b' }),
  ];
  const memory = laneMemory();
  const first = layoutGraph(executionIndex(history(nodes)), memory);
  const signature = first.rows.map(({ id, lane, color }) => ({ id, lane, color }));
  expect(first.rows[0].lane).toBe(first.rows[2].lane);
  expect(first.rows[0].lane).not.toBe(first.rows[1].lane);
  const update = layoutGraph(executionIndex(history([...nodes.map(item => ({ ...item, status: 'success' })), node('c')], [edge('b2','c')])), memory, 'a1', 200);
  expect(update.rows.slice(0, 4).map(({ id, lane, color }) => ({ id, lane, color }))).toEqual(signature);
});
it('keeps a task continuous through its tool operations before handing off to another task', () => {
  const nodes = [node('a', { workstream_id: 'task-a' }), node('tool', { parent_id: 'a', workstream_id: 'task-a', kind: 'tool' }), node('b', { workstream_id: 'task-b' })];
  const graph = layoutGraph(executionIndex(history(nodes, [edge('a','b')])), laneMemory());
  expect(graph.rows.map(row => row.id)).toEqual(['a', 'b']);
  expect(graph.tracks.find(track => track.edge)?.reference).toBe(false);
});
it('does not reorder sequence-only events when timestamped tools arrive', () => {
  const a = node('A', { sequence: 1 }), b = node('B', { sequence: 2 });
  const original = executionIndex(history([b, a]));
  const updated = executionIndex(history([b,
    node('read', { kind: 'tool', parent_id: 'B', started_at: '2026-10-13T08:00:00Z' }),
    node('edit', { kind: 'tool', parent_id: 'B', started_at: '2026-10-13T08:01:00Z' }), a]));
  expect(updated.activities.map(node => node.id)).toEqual(['A', 'B']);
  expect(layoutGraph(updated, laneMemory()).rows).toEqual(layoutGraph(original, laneMemory()).rows);
});
it('makes 120 task events from 1200 records without merging tasks, retries or orphan tools', () => {
  const nodes = Array.from({ length: 120 }, (_, i) => [node('task' + i, { actor_id: 'same-agent', workstream_id: 'same-task' }),
    ...Array.from({ length: 9 }, (_, j) => node('tool' + i + ':' + j, { kind: 'tool', parent_id: 'task' + i, label: 'Read file' }))]).flat();
  const index = executionIndex(history(nodes));
  expect(index.nodes).toHaveLength(1200);
  expect(index.activities).toHaveLength(120);
  expect(index.details.get('task0')).toHaveLength(9);
  expect(layoutGraph(index, laneMemory(), undefined, 0, 'Read file').matchIds).toHaveLength(120);
  const independent = layoutGraph(executionIndex(history([node('A', { workstream_id: 'task-a' }), node('B', { workstream_id: 'task-b' })])), laneMemory());
  expect(independent.tracks).toEqual([]);
  expect(independent.tracks.every(track => !track.edge)).toBe(true);
  const extra = executionIndex(history([node('failed', { status: 'failed', workstream_id: 'task' }),
    node('retry', { status: 'running', retry_of: 'failed', workstream_id: 'task' }),
    node('nested-agent', { parent_id: 'retry' }), node('orphan', { kind: 'tool', parent_id: 'missing' }),
    node('cycle1', { kind: 'tool', parent_id: 'cycle2' }), node('cycle2', { kind: 'tool', parent_id: 'cycle1' })]));
  expect(extra.activities).toHaveLength(6);
});
it('preserves exact tool-edge evidence while connecting owning task events once', async () => {
  const nodes = [node('A'), node('B'), node('read', { kind: 'tool', parent_id: 'A' }),
    node('edit', { kind: 'tool', parent_id: 'read', result: 'Updated parser.', artifact_refs: ['oaw:artifact:patch:1'] }), node('verify', { kind: 'tool', parent_id: 'B' })];
  const raw = history(nodes, [edge('read','edit'), edge('read','verify','data'), edge('edit','verify','data'), edge('missing','verify')]);
  const index = executionIndex(raw);
  expect(index.rawEdges).toEqual(raw.edges);
  expect(index.edges).toHaveLength(2);
  expect(index.edges[0]).toMatchObject({ source: 'A', target: 'B', kind: 'data', records: raw.edges!.slice(1, 3) });
  expect(index.details.get('A')?.map(node => node.id)).toEqual(['read', 'edit']);
  const combined = await readProjectHistory(source(raw), [{ id: 'one', name: 'One' }, { id: 'two', name: 'Two' }]);
  expect(combined.edges).toHaveLength(8); // Source assembly must not discard raw evidence.
  expect(executionIndex(combined).edges).toHaveLength(4);
  render(<ExecutionGraphView api={source(raw)} />);
  fireEvent.click(await screen.findByRole('button', { name: 'B: success' }));
  expect(screen.getByText('read → verify; edit → verify')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Uses result A' }));
  expect(screen.getByRole('button', { name: 'A: success' }).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText('Tool activity · 2')).toBeTruthy();
  expect(screen.getByText('Updated parser.')).toBeTruthy();
  expect(screen.getByText('oaw:artifact:patch:1')).toBeTruthy();
});
it('updates one event through tool activity and completion without emitting extra events', async () => {
  let value = history([node('Repair parser', { status: 'running', workstream_id: 'repair' })]);
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  const api = source();
  api.read = async () => value;
  api.subscribe = listener => { changed = listener; return () => {}; };
  const view = render(<ExecutionGraphView api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Repair parser: running' }));
  const lane = view.container.querySelector('.execution-vertex-dot')!.getAttribute('data-lane');
  const tools = Array.from({ length: 20 }, (_, i) => node('tool' + i, { kind: 'tool', label: 'Read file', parent_id: 'Repair parser' }));
  value = { ...value, revision: 2, nodes: [...value.nodes, ...tools] };
  act(() => changed('execution'));
  await screen.findByText('×20');
  expect(view.container.querySelectorAll('.execution-vertex')).toHaveLength(1);
  expect(view.container.querySelector('.execution-new-activity')).toBeNull();
  value = { ...value, revision: 3, nodes: [{ ...value.nodes[0], status: 'success', result: 'Parser repaired; checks passed.' }, ...tools] };
  act(() => changed('execution'));
  expect((await screen.findByRole('button', { name: 'Repair parser: success' })).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText('Parser repaired; checks passed.')).toBeTruthy();
  expect(view.container.querySelector('.execution-vertex-dot')!.getAttribute('data-lane')).toBe(lane);
  expect(view.container.querySelector('.execution-vertex')!.getAttribute('data-pending')).toBe('false');
  expect(view.container.querySelector('.execution-new-activity')).toBeNull();
});
it('never folds 1200 operations or draws a shared rail through an unrelated node', () => {
  const nodes = Array.from({ length: 1200 }, (_, i) => node('n' + i, { sequence: i }));
  const edges = nodes.slice(1, 30).flatMap(node => [edge('n0', node.id), edge(node.id, 'n1199')]);
  const graph = layoutGraph(executionIndex(history(nodes, edges)), laneMemory());
  expect(graph.rows).toHaveLength(1200); expect(graph.height).toBeGreaterThan(1200 * 54);
  expect(graph.lanes).toBeLessThanOrEqual(4);
  expect(graph.tracks.filter(track => track.edge)).toHaveLength(edges.length);
  expect(graph.tracks.some(track => track.reference)).toBe(true);
  for (const track of graph.tracks.filter(track => track.edge && !track.reference)) {
    const lane = track.route === 'fork' ? track.target.lane : track.source.lane;
    expect(graph.rows.some(row => row.id !== track.source.id && row.id !== track.target.id && row.lane === lane
      && row.y > track.source.y && row.y < track.target.y)).toBe(false);
  }
});
it('opens details from the whole row and closes with the same click or Escape', async () => {
  const view = render(<ExecutionGraphView api={source(history([node('Edit file', { detail: 'Changed the parser.' })]))} />);
  const button = await screen.findByRole('button', { name: 'Edit file: success' });
  fireEvent.click(button.querySelector('.execution-vertex-dot')!);
  expect(screen.getByText('Changed the parser.')).toBeTruthy();
  fireEvent.click(button.querySelector('strong')!);
  expect(screen.queryByText('Changed the parser.')).toBeNull();
  fireEvent.click(button);
  fireEvent.keyDown(screen.getByLabelText('Execution history'), { key: 'Escape' });
  expect(screen.queryByText('Changed the parser.')).toBeNull();
  expect(view.container.querySelector('.execution-expand, .execution-close-selection, .execution-selection-actions')).toBeNull();
});
it.each([
  { start: '2026-10-15T10:45:35', end: '2026-10-15T10:45:57', duration: '22s', times: ['10:45:35', '10:45:57'] },
  { start: '2026-10-15T23:59:35', end: '2026-10-16T00:00:57', duration: '1m 22s', times: ['23:59:35', '00:00:57'], crossDay: true },
  { start: '2026-10-15T10:45:35', end: '2026-10-18T11:45:35', duration: '3d 1h', times: ['10:45:35', '11:45:35'], crossDay: true },
  { start: '2026-10-15T10:45:35', end: null, status: 'running', times: ['10:45:35'], active: true },
  { start: '2026-10-15T10:45:35', end: null, times: ['10:45:35'] },
  { start: null, end: '2026-10-15T10:45:57', times: ['10:45:57'] },
  { start: 'invalid', end: '2026-10-15T10:45:57', times: ['10:45:57'] },
  { start: '2026-10-15T10:45:57', end: '2026-10-15T10:45:35', times: ['10:45:57', '10:45:35'] },
  { start: 'invalid', end: null, times: [] },
])('shows recorded times without inventing a finished interval: $start / $end', async ({ start, end, duration, times, status = 'success', crossDay, active }) => {
  const view = render(<ExecutionGraphView api={source(history([node('Archive', { started_at: start, finished_at: end, status })]))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Archive: ' + status }));
  const timing = view.container.querySelector('.execution-timing');
  expect([...timing?.querySelectorAll('time') ?? []].map(time => time.textContent)).toEqual(times);
  expect(timing?.querySelector('.execution-duration')?.textContent).toBe(duration);
  expect(timing?.textContent ?? '').not.toContain('Invalid Date');
  if (crossDay) expect(timing?.querySelectorAll('.execution-time-point small')).toHaveLength(2);
  if (active) expect(timing?.textContent).toContain('In progress');
  else expect(timing?.textContent ?? '').not.toContain('In progress');
});
it('retains planned definitions as planning context and reveals actual attempts directly', async () => {
  const nodes = [
    node('plan', { role: 'planning', detail: 'Build and verify.' }),
    node('definition', { label: 'Build', record_type: 'planned', parent_id: 'plan', realized_by: 'attempt' }),
    node('attempt', { label: 'Build implementation', status: 'failed' }),
  ];
  render(<ExecutionGraphView api={source(history(nodes))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'plan: success' }));
  expect(screen.getByText('Planned work')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Build' }));
  expect(screen.getByRole('button', { name: 'Build implementation: failed' }).getAttribute('aria-expanded')).toBe('true');
});
it('shows exact dependency endpoints, including unavailable ones, without a separate focus view', async () => {
  const view = render(<ExecutionGraphView api={source(history([node('A'), node('B')], [edge('A','B','data'), edge('missing','B')]))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'B: success' }));
  expect(screen.getByRole('button', { name: 'Requires missing' }).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Uses result A' }));
  expect(screen.getByRole('button', { name: 'A: success' }).getAttribute('aria-expanded')).toBe('true');
  expect(view.container.querySelectorAll('.execution-tracks')).toHaveLength(1);
});
it('keeps folded descendants off the canvas during hover and selection while retaining navigation', async () => {
  const nodes = ['Planner', 'Sandbox', 'Unrelated', 'Replan', 'Consumer'].map(id => node(id));
  const edges = [edge('Planner', 'Sandbox'), edge('Sandbox', 'Replan'), edge('Replan', 'Consumer'),
    edge('Sandbox', 'Consumer', 'data'), edge('Consumer', 'Replan', 'data'), edge('Sandbox', 'Sandbox', 'data')];
  const view = render(<ExecutionGraphView api={source(history(nodes, edges))} />);
  const planner = await screen.findByRole('button', { name: 'Planner: success' });
  const paths = () => [...view.container.querySelectorAll('path.execution-track')].map(path => [path.getAttribute('data-graph-edge'), path.getAttribute('d')]);
  const before = paths();
  fireEvent.mouseEnter(planner);
  expect(paths()).toEqual(before);
  expect(view.container.querySelectorAll('path[data-highlighted="true"]').length).toBeGreaterThan(0);
  expect(view.container.querySelector('[data-row-id="Unrelated"]')?.getAttribute('data-muted')).toBe('true');
  fireEvent.click(planner);
  fireEvent.mouseEnter(screen.getByRole('button', { name: 'Unrelated: success' }));
  expect(view.container.querySelector('path[data-reference="true"]')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Sandbox: success' }));
  expect(screen.getByRole('button', { name: 'Used by Consumer' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Used by Consumer' }));
  expect(screen.getByRole('button', { name: 'Consumer: success' }).getAttribute('aria-expanded')).toBe('true');
  expect(view.container.querySelector('path[data-reference="true"]')).toBeNull();
});
it('explains and navigates local workstream guides without calling them dependencies', async () => {
  const view = render(<ExecutionGraphView api={source(history([node('A', { workstream_id: 'task' }), node('B', { workstream_id: 'task' })]))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'B: success' }));
  expect(screen.getByText('Same workstream, not a dependency')).toBeTruthy();
  expect(view.container.querySelector('.execution-causal-summary')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Workstream: A' }));
  expect(screen.getByRole('button', { name: 'A: success' }).getAttribute('aria-expanded')).toBe('true');
});
it('assembles all pages atomically and rejects mixed revisions and repeated cursors', async () => {
  const api = source();
  api.read = vi.fn().mockResolvedValueOnce({ ...history([node('A')]), next_cursor: 'page2' }).mockResolvedValueOnce(history([node('B')], [edge('A','B')]));
  const assembled = await readExecutionHistory(api, 'a');
  expect(assembled.nodes).toHaveLength(2); expect(assembled.edges).toHaveLength(1);
  api.read = vi.fn().mockResolvedValueOnce({ ...history(), next_cursor: 'page2' }).mockResolvedValueOnce({ ...history(), revision: 2 });
  await expect(readExecutionHistory(api, 'a')).rejects.toThrow('changed while loading');
  api.read = vi.fn().mockResolvedValue({ ...history(), next_cursor: 'repeated' });
  await expect(readExecutionHistory(api, 'a')).rejects.toThrow('repeated cursor');
});
it('combines connected agents without colliding identities or manufacturing cross-agent edges', async () => {
  const api = source(history([node('a'), node('b')], [edge('a','b')]));
  const result = await readProjectHistory(api, [{ id: 'builder', name: 'Builder' }, { id: 'tester', name: 'Tester' }]);
  expect(new Set(result.nodes.map(node => node.id)).size).toBe(4);
  expect(result.nodes.every(node => !node.actor_id && !node.actor_label)).toBe(true); // An observation source is not proof of who executed each task.
  expect(result.edges).toHaveLength(2);
  expect(result.edges!.every(edge => JSON.parse(edge.source)[0] === JSON.parse(edge.target)[0])).toBe(true);
});
it('preserves opened operation during coalesced updates and new runs in one history', async () => {
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  const pending: ((value: ExecutionSnapshot) => void)[] = [];
  const value = history([node('Edit', { status: 'running', workstream_id: 'task', actor_id: 'builder' })]);
  const api = source(value);
  api.subscribe = listener => { changed = listener; return () => {}; };
  api.read = vi.fn().mockResolvedValueOnce(value).mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  render(<ExecutionGraphView api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit: running' }));
  act(() => changed('execution'));
  await waitFor(() => expect(pending).toHaveLength(1));
  act(() => { changed('execution'); changed('execution'); });
  expect(api.read).toHaveBeenCalledTimes(2);
  const done = { ...value, run_id: 'next-run', revision: 2, nodes: [node('Edit', { status: 'failed', result: 'Validation failed.' }), node('Retry', { retry_of: 'Edit' })] };
  await act(async () => pending[0](done));
  expect((await screen.findByRole('button', { name: 'Edit: failed' })).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText('Validation failed.')).toBeTruthy();
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1](done));
});
it('clears revoked connections and ignores an in-flight response from the previous session', async () => {
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  let finish!: (value: ExecutionSnapshot) => void;
  const api = source();
  api.subscribe = listener => { changed = listener; return () => {}; };
  api.read = vi.fn().mockResolvedValueOnce(history()).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<ExecutionGraphView api={api} />);
  await screen.findByRole('button', { name: 'A: success' });
  act(() => changed('execution'));
  await waitFor(() => expect(api.read).toHaveBeenCalledTimes(2));
  api.list = vi.fn().mockResolvedValue({ sources: [] });
  act(() => changed('connections'));
  await act(async () => finish(history()));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'A: success' })).toBeNull());
  view.rerender(<ExecutionGraphView api={source({ title: '', phase: 'idle', nodes: [] })} />);
  await screen.findByText('No execution yet');
});
