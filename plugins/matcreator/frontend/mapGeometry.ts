export type Circle = { x: number; y: number; radius: number };

/** Screen-independent geometry, recomputed from measured node centers on drag. */
export function mapCurve(source: Circle, target: Circle) {
  const dx = target.x - source.x, dy = target.y - source.y;
  const distance = Math.hypot(dx, dy);
  if (distance < 0.001) return null;
  const ux = dx / distance, uy = dy / distance;
  const start = { x: source.x + ux * source.radius, y: source.y + uy * source.radius };
  const end = { x: target.x - ux * target.radius, y: target.y - uy * target.radius };
  const span = Math.max(0, distance - source.radius - target.radius);
  const bend = Math.min(38, span * 0.15);
  const mid = { x: (start.x + end.x) / 2 - uy * bend, y: (start.y + end.y) / 2 + ux * bend };
  const step = span / 6;
  const c1 = { x: start.x + ux * step, y: start.y + uy * step };
  const c2 = { x: mid.x - ux * step, y: mid.y - uy * step };
  const c3 = { x: mid.x + ux * step, y: mid.y + uy * step };
  const c4 = { x: end.x - ux * step, y: end.y - uy * step };
  // Two joined cubics preserve radial endpoint tangents and a smooth midpoint.
  return { start, end, c1, c4, label: mid,
    path: `M ${start.x},${start.y} C ${c1.x},${c1.y} ${c2.x},${c2.y} ${mid.x},${mid.y} C ${c3.x},${c3.y} ${c4.x},${c4.y} ${end.x},${end.y}` };
}
