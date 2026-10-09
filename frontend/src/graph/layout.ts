import { MultiDirectedGraph, UndirectedGraph } from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import louvain from 'graphology-communities-louvain';
import { contourDensity } from 'd3-contour';
import { hash, seed } from './model';
import type { Island, LayoutRequest, LayoutResult, Point } from './types';

/** Runs only in the module Worker (also callable by the algorithm tests). */
export function computeLayout(input: LayoutRequest): LayoutResult {
  const started = performance.now();
  const graph = new MultiDirectedGraph();
  const saved = new Map(input.positions), previous = new Map(input.communities);
  const nodes = [...input.nodes].sort((a, b) => a.id.localeCompare(b.id));
  nodes.forEach((node, index) => graph.addNode(node.id, { ...(saved.get(node.id) ?? seed(node.id, index)), size: 3, fixed: saved.has(node.id) }));
  for (const edge of input.edges) if (graph.hasNode(edge.source) && graph.hasNode(edge.target) && !graph.hasEdge(edge.id)) {
    graph.addDirectedEdgeWithKey(edge.id, edge.source, edge.target, { weight: 1 });
  }
  let random = 42;
  // Community proximity ignores direction; the domain graph keeps it intact.
  const proximity = new UndirectedGraph();
  nodes.forEach(node => proximity.addNode(node.id));
  graph.forEachEdge((_id, _attrs, source, target) => {
    if (source === target) return;
    if (proximity.hasEdge(source, target)) proximity.updateEdgeAttribute(source, target, 'weight', w => w + 1);
    else proximity.addEdge(source, target, { weight: 1 });
  });
  const communities: Record<string, number> = proximity.size ? louvain(proximity, {
    rng: () => { random = Math.imul(1664525, random) + 1013904223 | 0; return (random >>> 0) / 4294967296; },
  }) : Object.fromEntries(nodes.map((node, index) => [node.id, index]));
  const canonical = new Map<number, string>();
  for (const node of nodes) if (!canonical.has(communities[node.id])) canonical.set(communities[node.id], node.id);
  // Tags name inferred communities; kind never participates in their assignment.
  const byId = new Map(nodes.map(node => [node.id, node]));
  const groups = new Map<string, { label: string; members: string[] }>();
  for (const node of nodes) {
    const explicit = node.topic?.trim();
    const id = explicit ? `topic:${explicit}` : previous.get(node.id) ?? `community:${canonical.get(communities[node.id])}`;
    if (!groups.has(id)) groups.set(id, { label: explicit || '', members: [] });
    groups.get(id)!.members.push(node.id);
  }
  for (const group of groups.values()) {
    // A high-degree title names inferred communities, without inventing a knowledge type.
    if (!group.label) {
      const representative = [...group.members].sort((a, b) => graph.degree(b) - graph.degree(a) || a.localeCompare(b))[0];
      const counts = new Map<string, number>();
      for (const id of group.members) for (const tag of byId.get(id)?.tags ?? []) {
        const key = tag.toLocaleLowerCase(); counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      const label = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
      group.label = label && label[1] > 1 ? label[0] : byId.get(representative)?.label ?? '';
    }
  }
  // Topics describe the resulting network; they never impose rows, cells or
  // community-sized boxes on its coordinates. Only saved neighbors anchor new nodes.
  for (const node of nodes) if (!saved.has(node.id)) {
    const anchors = graph.neighbors(node.id).flatMap(id => saved.has(id) ? [saved.get(id)!] : []);
    if (!anchors.length) continue;
    const center = anchors.reduce((p, anchor) => ({ x: p.x + anchor.x / anchors.length, y: p.y + anchor.y / anchors.length }), { x: 0, y: 0 });
    const offset = seed(node.id, 0);
    graph.mergeNodeAttributes(node.id, { x: center.x + offset.x, y: center.y + offset.y });
  }
  if (nodes.some(n => !saved.has(n.id)) && graph.order > 1 && graph.size > 0) forceAtlas2.assign(graph, {
    iterations: graph.order > 3000 ? 100 : 160,
    settings: { ...forceAtlas2.inferSettings(graph), barnesHutOptimize: true, barnesHutTheta: .6,
      gravity: .3, scalingRatio: 2, slowDown: 1, linLogMode: false, adjustSizes: false },
  });
  const positions: [string, Point][] = nodes.map(n => {
    const p = graph.getNodeAttributes(n.id);
    return [n.id, saved.get(n.id) ?? (Number.isFinite(p.x) && Number.isFinite(p.y) ? { x: p.x, y: p.y } : seed(n.id, hash(n.id) % 100))];
  });
  const points = new Map(positions), islands: Island[] = [];
  for (const [id, group] of groups) {
    const cloud = group.members.map(key => points.get(key)!);
    const center = cloud.reduce((p, n) => ({ x: p.x + n.x / cloud.length, y: p.y + n.y / cloud.length }), { x: 0, y: 0 });
    if (cloud.length === 1) {
      islands.push({ id, label: group.label, members: group.members, center, labelPosition: center, rings: [] });
      continue;
    }
    let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
    for (const p of cloud) { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); ymin = Math.min(ymin, p.y); ymax = Math.max(ymax, p.y); }
    const scale = 160 / Math.max(1, xmax - xmin, ymax - ymin), pad = 32;
    const samples = [...cloud], members = new Set(group.members);
    for (const edge of input.edges) if (members.has(edge.source) && members.has(edge.target)) {
      const a = points.get(edge.source)!, b = points.get(edge.target)!;
      const steps = Math.min(16, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) * scale / 18));
      for (let step = 1; step < steps; step++) samples.push({ x: a.x + (b.x - a.x) * step / steps, y: a.y + (b.y - a.y) * step / steps });
    }
    const density = contourDensity<Point>().x(p => (p.x - xmin) * scale + pad).y(p => (p.y - ymin) * scale + pad)
      .size([224, 224]).bandwidth(cloud.length > 100 ? 5 : 12).cellSize(4).thresholds([.00015]);
    const contour = density(samples)[0];
    const rings = contour?.coordinates.map(polygon => polygon.map(ring => ring.map(([x, y]) => [(x - pad) / scale + xmin, (y - pad) / scale + ymin]))) ?? [];
    islands.push({ id, label: group.label, members: group.members, center, labelPosition: { x: center.x, y: ymax }, rings });
  }
  return { positions, islands, duration: performance.now() - started };
}
