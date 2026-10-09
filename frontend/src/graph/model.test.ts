import { describe, expect, it } from 'vitest';
import { createGraph, reconcile, rememberPositions, restorePositions } from './model';
import { computeLayout } from './layout';
import type { LayoutRequest, NetworkData } from './types';

const data: NetworkData = {
  nodes: [{ id: 'a', label: 'Alpha', kind: 'capability', topic: 'Structure' }, { id: 'b', label: 'Beta', kind: 'memory', topic: 'Structure' }, { id: 'c', label: 'Gamma', kind: 'capability', topic: 'Diffraction' }],
  edges: [{ id: 'ab', source: 'a', target: 'b', label: 'requires' }, { id: 'ab2', source: 'a', target: 'b', label: 'refines' }, { id: 'aa', source: 'a', target: 'a', label: 'self' }, { id: 'bc', source: 'b', target: 'c' }],
};
describe('relationship map data contract', () => {
  it('preserves opaque IDs, parallel edges, loops and direction without mutating the source', () => {
    const original = structuredClone(data), graph = createGraph();
    reconcile(graph, data, new Map());
    expect(graph.nodes()).toEqual(['a', 'b', 'c']);
    expect(graph.edges('a', 'b')).toEqual(['ab2', 'ab']);
    expect(graph.source('bc')).toBe('b'); expect(graph.target('bc')).toBe('c');
    expect(graph.hasEdge('aa')).toBe(true); expect(data).toEqual(original);
  });
  it('updates metadata in place and only removes obsolete topology, without touching coordinates', () => {
    const graph = createGraph(), positions = new Map([['a', { x: 321, y: 123 }]]);
    reconcile(graph, data, positions);
    const original = graph.getNodeAttributes('b');
    expect(reconcile(graph, { ...data, nodes: data.nodes.map(n => n.id === 'a' ? { ...n, label: 'Edited' } : n) }, positions)).toEqual({ topology: false, groups: false });
    expect(graph.getNodeAttributes('b')).toBe(original);
    expect(graph.getNodeAttribute('a', 'x')).toBe(321);
    graph.mergeEdgeAttributes('ab', { type: 'curved', curvature: .4 });
    reconcile(graph, { ...data, edges: data.edges.map(e => e.id === 'ab' ? { ...e, label: 'Updated relationship' } : e) }, positions);
    expect(graph.getEdgeAttributes('ab')).toMatchObject({ label: 'Updated relationship', type: 'curved', curvature: .4 });
    reconcile(graph, { nodes: data.nodes.slice(0, 2), edges: data.edges }, positions);
    expect(graph.hasNode('c')).toBe(false); expect(graph.hasEdge('bc')).toBe(false);
    expect(data.edges).toHaveLength(4);
  });
  it('restores bounded session layouts as copies, without sharing mutable maps', () => {
    rememberPositions('test', new Map([['a', { x: 12, y: 14 }]]));
    restorePositions('test').clear(); expect(restorePositions('test').get('a')).toEqual({ x: 12, y: 14 });
    for (let i = 0; i < 7; i++) rememberPositions(`test${i}`, new Map());
    expect(restorePositions('test').size).toBe(0);
  });
});
describe('worker layout and semantic groups', () => {
  const input: LayoutRequest = { ...data, positions: [], communities: [] };
  it('is deterministic and separates topics from knowledge types', () => {
    const a = computeLayout(input), b = computeLayout({ ...input, nodes: [...input.nodes].reverse() });
    expect(a.positions).toEqual(b.positions);
    expect(a.islands.find(i => i.label === 'Structure')!.members).toEqual(['a', 'b']);
    expect(a.islands.find(i => i.label === 'Diffraction')!.members).toEqual(['c']);
    expect(a.islands[0].rings.length).toBeGreaterThan(0);
    for (const [, p] of a.positions) expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
  });
  it('keeps saved and dragged nodes exactly fixed when adding, filtering or expanding', () => {
    const first = computeLayout(input);
    const next = computeLayout({ ...input, nodes: [...input.nodes, { id: 'new', label: 'New', topic: 'Structure' }],
      edges: [...input.edges, { id: 'new-a', source: 'new', target: 'a' }], positions: first.positions });
    for (const [id, p] of first.positions) expect(new Map(next.positions).get(id)).toEqual(p);
    const filtered = computeLayout({ ...input, nodes: [input.nodes[0]], positions: next.positions });
    expect(filtered.positions[0]).toEqual(first.positions[0]);
  });
  it('lets relationships determine coordinates independently of topic grouping', () => {
    const original = computeLayout(input);
    const regrouped = computeLayout({ ...input, nodes: input.nodes.map(n => ({ ...n, topic: 'One topic' })) });
    expect(regrouped.islands).toHaveLength(1);
    expect(regrouped.positions).toEqual(original.positions);
  });
  it('handles empty graphs, isolated nodes, dangling relations and single-node loops', () => {
    expect(computeLayout({ nodes: [], edges: [], positions: [], communities: [] }).positions).toEqual([]);
    const output = computeLayout({ ...input, nodes: [input.nodes[0]] });
    expect(output.positions).toHaveLength(1); expect(output.islands).toHaveLength(1);
    const unlinked = computeLayout({ nodes: Array.from({ length: 500 }, (_, i) => ({ id: String(i), label: `Unlinked ${i}` })), edges: [], positions: [], communities: [] });
    expect(unlinked.islands).toHaveLength(500);
    expect(unlinked.islands.every(island => island.rings.length === 0)).toBe(true);
  });
});
