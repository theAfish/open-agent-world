import { MultiDirectedGraph } from 'graphology';
import type { NetworkData, NetworkNode, Point } from './types';

export const kindColors: Record<string, string> = {
  capability: '#3984e6', procedure: '#b05bbb', heuristic: '#d6aa20', limitation: '#dc6068', memory: '#a8adb5',
};
const palette = [...Object.values(kindColors), '#319e91'];
export function hash(value: string) {
  let n = 2166136261;
  for (let i = 0; i < value.length; i++) n = Math.imul(n ^ value.charCodeAt(i), 16777619);
  return n >>> 0;
}
export const nodeColor = (kind = '') => kindColors[kind.toLowerCase()] ?? palette[hash(kind) % palette.length];
export const seed = (id: string, index: number): Point => {
  const angle = hash(id) / 0xffffffff * Math.PI * 2;
  return { x: Math.cos(angle) * (25 + 12 * Math.sqrt(index)), y: Math.sin(angle) * (25 + 12 * Math.sqrt(index)) };
};
export type ViewGraph = MultiDirectedGraph;
export function createGraph(): ViewGraph { return new MultiDirectedGraph({ allowSelfLoops: true }); }

/** Diff domain data in place. No clear/import on selection, filters, or metadata changes. */
export function reconcile(graph: ViewGraph, data: NetworkData, positions: Map<string, Point>) {
  const nodes = new Map(data.nodes.map(n => [n.id, n]));
  const edges = new Map(data.edges.filter(e => nodes.has(e.source) && nodes.has(e.target)).map(e => [e.id, e]));
  let topology = false, groups = false;
  graph.forEachEdge((id, attrs) => {
    if (attrs.aggregate) return;
    const edge = edges.get(id);
    if (!edge || graph.source(id) !== edge.source || graph.target(id) !== edge.target) { graph.dropEdge(id); topology = true; }
  });
  graph.forEachNode((id, attrs) => {
    if (!attrs.aggregate && !nodes.has(id)) { graph.dropNode(id); topology = true; }
  });
  let index = 0;
  for (const node of nodes.values()) {
    const signature = JSON.stringify([node.topic ?? '', [...(node.tags ?? [])].sort()]);
    const attrs = { label: node.label, kind: node.kind ?? '', color: nodeColor(node.kind), grouping: signature,
      size: 3.2, zIndex: 0, aggregate: false };
    if (!graph.hasNode(node.id)) {
      graph.addNode(node.id, { ...attrs, ...(positions.get(node.id) ?? seed(node.id, index)) });
      topology = true;
    } else {
      const old = graph.getNodeAttributes(node.id);
      if (old.grouping !== signature) groups = true;
      const patch = Object.fromEntries(Object.entries(attrs).filter(([key, value]) => old[key] !== value));
      if (Object.keys(patch).length) graph.mergeNodeAttributes(node.id, patch);
    }
    index++;
  }
  for (const edge of edges.values()) {
    const attrs = { label: edge.label ?? '', color: '#a0aab4', size: .6, type: 'arrow', aggregate: false };
    if (!graph.hasEdge(edge.id)) { graph.addDirectedEdgeWithKey(edge.id, edge.source, edge.target, attrs); topology = true; }
    else if (graph.getEdgeAttribute(edge.id, 'label') !== attrs.label) graph.setEdgeAttribute(edge.id, 'label', attrs.label);
  }
  return { topology, groups };
}

// Session-only view cache: bounded, disposable, never added to a document or exported.
const cache = new Map<string, Map<string, Point>>();
export function restorePositions(key: string) { return new Map(cache.get(key) ?? []); }
export function rememberPositions(key: string, positions: Map<string, Point>) {
  cache.delete(key);
  cache.set(key, new Map([...positions].slice(-12000)));
  while (cache.size > 6) cache.delete(cache.keys().next().value!);
}
export const describeNode = (n: NetworkNode) => `${n.kind ? `${n.kind}: ` : ''}${n.label}`;
