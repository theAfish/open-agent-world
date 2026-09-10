export type Point = { x: number; y: number };
type Link = { source: string; target: string };
const DIAMETER = 110;
const GAP = 42;

/** Settle once on topology changes. Saved/dragged nodes stay fixed during expansion. */
export function graphLayout(ids: string[], edges: Link[], saved = new Map<string, Point>()): Map<string, Point> {
  const ordered = [...new Set(ids)].sort();
  const neighbors = new Map(ordered.map(id => [id, new Set<string>()]));
  for (const edge of edges) {
    if (edge.source === edge.target || !neighbors.has(edge.source) || !neighbors.has(edge.target)) continue;
    neighbors.get(edge.source)!.add(edge.target);
    neighbors.get(edge.target)!.add(edge.source);
  }
  const seen = new Set<string>();
  const groups: string[][] = [];
  for (const id of ordered) {
    if (seen.has(id)) continue;
    const group = [id]; seen.add(id);
    for (let i = 0; i < group.length; i++) for (const next of neighbors.get(group[i])!) {
      if (!seen.has(next)) { seen.add(next); group.push(next); }
    }
    groups.push(group);
  }
  groups.sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]));
  const result = new Map<string, Point>();
  const rowWidth = Math.max(700, Math.sqrt(ordered.length) * 260);
  let x = 0, y = 0, rowHeight = 0;
  for (const group of groups) {
    const anchored = group.some(id => saved.has(id));
    const points = new Map<string, Point>();
    group.forEach((id, i) => {
      const anchor = [...neighbors.get(id)!].map(n => saved.get(n)).find(Boolean);
      const angle = i * Math.PI * (3 - Math.sqrt(5));
      const radius = anchored ? 170 : 100 * Math.sqrt(i);
      points.set(id, saved.has(id) ? { ...saved.get(id)! } : {
        x: (anchor?.x ?? 0) + Math.cos(angle) * radius,
        y: (anchor?.y ?? 0) + Math.sin(angle) * radius,
      });
    });
    for (let step = 0; step < 180; step++) {
      const forces = new Map(group.map(id => [id, { x: 0, y: 0 }]));
      for (let i = 0; i < group.length; i++) for (let j = i + 1; j < group.length; j++) {
        const a = points.get(group[i])!, b = points.get(group[j])!;
        const dx = b.x - a.x || 0.01, dy = b.y - a.y;
        const distance = Math.hypot(dx, dy);
        const connected = neighbors.get(group[i])!.has(group[j]);
        const repulsion = Math.min(40, 9000 / (distance * distance));
        const collision = Math.max(0, DIAMETER + GAP - distance) * 0.6;
        const pull = connected ? (distance - 185) * 0.065 : 0;
        const force = pull - repulsion - collision;
        const fx = dx / distance * force, fy = dy / distance * force;
        forces.get(group[i])!.x += fx; forces.get(group[i])!.y += fy;
        forces.get(group[j])!.x -= fx; forces.get(group[j])!.y -= fy;
      }
      for (const id of group) {
        if (saved.has(id)) continue;
        const p = points.get(id)!, f = forces.get(id)!;
        const cooling = 1 - step / 220;
        p.x += Math.max(-24, Math.min(24, f.x)) * cooling;
        p.y += Math.max(-24, Math.min(24, f.y)) * cooling;
      }
    }
    const left = Math.min(...[...points.values()].map(p => p.x));
    const top = Math.min(...[...points.values()].map(p => p.y));
    const width = Math.max(...[...points.values()].map(p => p.x)) - left + DIAMETER;
    const height = Math.max(...[...points.values()].map(p => p.y)) - top + DIAMETER;
    if (!anchored && x > 0 && x + width > rowWidth) { x = 0; y += rowHeight + 220; rowHeight = 0; }
    // New disconnected groups sit beyond the saved graph, leaving it undisturbed.
    const offset = saved.size ? Math.max(...[...saved.values()].map(p => p.x)) + 330 : 0;
    for (const [id, p] of points) result.set(id, anchored ? p : { x: p.x - left + x + offset, y: p.y - top + y });
    if (!anchored) { x += width + 220; rowHeight = Math.max(rowHeight, height); }
  }
  return result;
}
