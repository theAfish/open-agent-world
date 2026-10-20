import { describe, expect, it } from 'vitest';
import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot } from '../plugins/executionSources';
import { causalFocus, executionIndex, laneMemory, layoutGraph, statusKind, trackPath } from './executionHistory';
import { graphFixture } from '../../e2e/fixtures/executionGraph';

const node = (id: string, fields: Partial<ExecutionNode> = {}): ExecutionNode => ({ id, label: id, kind: 'agent', status: 'success', ...fields });
const edge = (source: string, target: string, kind: ExecutionEdge['kind'] = 'control'): ExecutionEdge => ({ id: `${source}:${target}:${kind}`, source, target, kind });
const snapshot = (nodes: ExecutionNode[], edges: ExecutionEdge[] = []): ExecutionSnapshot => ({ title: '', phase: 'idle', nodes, edges });
const draw = (value: ExecutionSnapshot) => layoutGraph(executionIndex(value), laneMemory());

describe('Temporal Braid semantics', () => {
  it('allows a serial Agent handoff on one lane while retaining separate actor colors', () => {
    const graph = draw(snapshot([node('a', { actor_id: 'one', workstream_id: 'prepare' }), node('b', { actor_id: 'two', workstream_id: 'validate' })], [edge('a', 'b')]));
    expect(graph.rows[0].lane).toBe(graph.rows[1].lane);
    expect(graph.rows[0].color).not.toBe(graph.rows[1].color);
    const sameStream = draw(snapshot([node('a', { actor_id: 'one', workstream_id: 'shared' }), node('b', { actor_id: 'two', workstream_id: 'shared' })]));
    expect(sameStream.rows[0].lane).toBe(sameStream.rows[1].lane);
    expect(sameStream.tracks[0].kind).toBe('workstream');
    expect(causalFocus(executionIndex(snapshot(sameStream.rows.map(row => row.node))), 'b').upstream.size).toBe(0);
  });

  it('uses observed concurrency even for one Agent and one workstream', () => {
    const graph = draw(snapshot([
      node('a', { actor_id: 'one', workstream_id: 'shared', started_at: '2026-10-15T09:00:00Z', finished_at: '2026-10-15T09:05:00Z' }),
      node('b', { actor_id: 'one', workstream_id: 'shared', started_at: '2026-10-15T09:01:00Z', finished_at: '2026-10-15T09:06:00Z' }),
    ]));
    expect(graph.rows[0].lane).not.toBe(graph.rows[1].lane);
    expect(graph.rows[0].color).toBe(graph.rows[1].color);
    expect(graph.tracks).toEqual([]);
  });

  it('retains all evidence for three-way fan-in, retry, replan and cross-execution inputs', () => {
    const value = graphFixture('braid'), index = executionIndex(value), graph = layoutGraph(index, laneMemory());
    expect(new Set(graph.rows.filter(row => ['build', 'scan', 'baseline'].includes(row.id)).map(row => row.lane)).size).toBe(3);
    const bundle = graph.tracks.find(track => track.source.id === 'retry' && track.target.id === 'combine')!;
    expect(bundle.edges.map(edge => edge.kind)).toEqual(['control', 'data']);
    expect(bundle.edges[1].records[0].artifact_ref).toBe('oaw:artifact:structures:v2');
    expect(index.incoming.get('combine')).toHaveLength(4);
    expect(index.incoming.get('retry')?.[0].kind).toBe('retry');
    expect(index.incoming.get('replan')?.[0].kind).toBe('replan');
    expect(graph.tracks.find(track => track.source.id === 'retry' && track.target.id === 'report')?.reference).toBe(true);
    expect(graph.unavailable).toEqual([expect.objectContaining({ source: 'outside-retention', target: 'report' })]);
    expect(graph.ports.get('failed')?.node.status).toBe('failed');
    expect(graph.ports.get('review')?.node.status).toBe('awaiting_peer_review');
  });

  it('traces both directions transitively without sweeping in sibling branches or stream guides', () => {
    const index = executionIndex(snapshot(['a', 'b', 'c', 'd', 'e', 'f'].map(id => node(id, { workstream_id: 'same' })),
      [edge('a', 'b'), edge('b', 'c'), edge('c', 'd'), edge('a', 'e'), edge('f', 'd')]));
    const focus = causalFocus(index, 'c');
    expect([...focus.upstream]).toEqual(['b', 'a']);
    expect([...focus.downstream]).toEqual(['d']);
    expect(focus.nodes.has('e')).toBe(false); expect(focus.nodes.has('f')).toBe(false);
    expect(focus.edges.size).toBe(3);
    const cycle = causalFocus(executionIndex(snapshot([node('a'), node('b')], [edge('a', 'b'), edge('b', 'a')])), 'a');
    expect([...cycle.upstream]).toEqual(['b']); expect(cycle.edges.size).toBe(2);
  });

  it('keeps chronology when a result returns to an already-started parent and folds the reverse edge', () => {
    const graph = draw(snapshot([node('parent', { sequence: 0 }), node('child', { sequence: 1, parent_id: 'parent' })], [edge('child', 'parent', 'data')]));
    expect(graph.rows.map(row => row.id)).toEqual(['parent', 'child']);
    expect(graph.tracks[0].reference).toBe(true);
    expect(trackPath(graph.tracks[0], n => 24 + n * 24)).toBe('');
    expect(graph.references.get('child')).toEqual([graph.tracks[0]]);
  });

  it('does not reserve lanes for distant data consumers or manufacture actor identities', () => {
    const nodes = Array.from({ length: 30 }, (_, i) => node('n' + i, { workstream_id: 'stream-' + i }));
    const independent = draw(snapshot(nodes)), dependent = draw(snapshot(nodes, [edge('n0', 'n29', 'data')]));
    expect(independent.rows.map(row => row.lane)).toEqual(dependent.rows.map(row => row.lane));
    expect(dependent.rows.every(row => row.color === -1)).toBe(true);
    expect(dependent.references.get('n0')).toHaveLength(1); expect(dependent.references.get('n29')).toHaveLength(1);
    expect(trackPath(dependent.tracks[0], n => n * 24)).toBe('');
  });

  it('keeps assigned positions after append, late backfill, state changes and added edges', () => {
    const value = graphFixture('braid'), memory = laneMemory();
    const first = layoutGraph(executionIndex(value), memory);
    const next = structuredClone(value);
    next.nodes.push(node('late', { started_at: '2026-10-15T09:03:30Z', actor_id: 'new-agent' }));
    next.nodes.push(node('next', { actor_id: 'Nova', started_at: '2026-10-15T10:00:00Z' }));
    next.nodes.find(node => node.id === 'transport')!.status = 'success';
    next.edges!.push(edge('baseline', 'next', 'data'));
    const updated = layoutGraph(executionIndex(next), memory, 'retry', 150);
    for (const row of first.rows) expect(updated.ports.get(row.id)).toMatchObject({ lane: row.lane, color: row.color });
    for (const graph of [first, updated]) for (const track of graph.tracks.filter(track => !track.reference)) {
      const lane = track.route === 'fork' ? track.target.lane : track.source.lane;
      expect(graph.rows.some(row => row.ordinal > track.source.ordinal && row.ordinal < track.target.ordinal && row.lane === lane)).toBe(false);
      expect(trackPath(track, n => 24 + n * 24).match(/M/g)).toHaveLength(1);
    }
  });

  it('has distinct state families without interpreting an unknown state as success', () => {
    expect(['success', 'running', 'waiting', 'failed', 'needs_replanning', 'cancelled', 'pending', 'peer_review'].map(statusKind))
      .toEqual(['complete', 'running', 'waiting', 'failed', 'rework', 'stopped', 'pending', 'custom']);
  });

  it('reuses a released inner rail and makes room for a later fork without moving events', () => {
    const value = graphFixture('compact'), memory = laneMemory();
    const graph = layoutGraph(executionIndex(value), memory);
    const join = graph.tracks.find(track => track.source.id === 'validate' && track.target.id === 'accept-deep')!;
    expect(join.rail?.map(point => point.lane)).toEqual([2, 1]);
    expect(join.rail![1].y).toBeGreaterThan(graph.ports.get('accept-quick')!.y);
    expect(join.rail![1].y).toBeLessThan(graph.ports.get('prepare')!.y);
    const fork = graph.tracks.find(track => track.source.id === 'accept-deep' && track.target.id === 'remote')!;
    expect(fork.rail?.map(point => point.lane)).toEqual([1, 2]);
    expect(fork.rail![1].y).toBeLessThan(graph.ports.get('next')!.y);
    const next = structuredClone(value);
    next.nodes.push(node('later', { sequence: 12 }));
    const appended = layoutGraph(executionIndex(next), memory);
    for (const track of graph.tracks) expect(trackPath(appended.tracks.find(item => item.id === track.id)!, lane => 24 + lane * 24))
      .toBe(trackPath(track, lane => 24 + lane * 24));
    expect(appended.rows.slice(0, graph.rows.length)).toEqual(graph.rows);
  });

  it('keeps an outer cable separate when the inner rail still carries another dependency', () => {
    const value = graphFixture('compact');
    value.edges!.push(edge('quick', 'accept-deep', 'data'));
    const graph = draw(value);
    const join = graph.tracks.find(track => track.source.id === 'validate' && track.target.id === 'accept-deep')!;
    expect(join.reference).toBe(false);
    expect(join.rail).toBeUndefined();
    expect(graph.tracks.find(track => track.source.id === 'quick' && track.target.id === 'accept-deep')?.reference).toBe(false);
  });

  it('indexes the contract limit without quadratic lane scans or recursive traversal', () => {
    const nodes = Array.from({ length: 10_000 }, (_, i) => node('n' + i, { sequence: i, actor_id: 'agent-' + i % 8 }));
    const edges = nodes.flatMap((node, i) => Array.from({ length: Math.min(5, i) }, (_, j) => edge('n' + (i - j - 1), node.id)));
    const start = performance.now();
    const index = executionIndex(snapshot(nodes, edges)), graph = layoutGraph(index, laneMemory());
    const focus = causalFocus(index, 'n9999');
    expect(graph.rows).toHaveLength(10_000); expect(graph.tracks).toHaveLength(edges.length);
    expect(focus.upstream.size).toBe(9_999); expect(focus.edges.size).toBe(edges.length);
    expect(performance.now() - start).toBeLessThan(5000);
  });
});
