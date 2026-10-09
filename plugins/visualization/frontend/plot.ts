import type { Dataset } from '@oaw/plugin-api';

export type Kind = 'graph' | 'line' | 'bar' | 'scatter' | 'histogram';
export interface Selection { x: string; y: string; series: string; aggregate: string }
export interface Point { x: number; y: number; label: string; series: string }
export interface Plot { points: Point[]; categories: string[]; dateX: boolean; nodes: {id: string; name: string; type?: string}[];
  edges: {source: string; target: string; type?: string}[]; skipped: number }
export const numeric = (value: unknown): number | null => {
  if (value == null || typeof value === 'boolean' || (typeof value !== 'number' && typeof value !== 'string') || String(value).trim() === '') return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
};

export function preparePlot(data: Dataset, kind: Kind, config: Selection): Plot {
  const plot: Plot = {points: [], categories: [], dateX: false, nodes: [], edges: [], skipped: 0};
  if (data.kind === 'graph') return {...plot, nodes: data.nodes ?? [], edges: data.edges ?? []};
  const columns = data.columns ?? [], rows = data.rows ?? [];
  const xi = columns.indexOf(config.x), yi = columns.indexOf(config.aggregate === 'none' ? config.y : data.value_column ?? 'value');
  const si = columns.indexOf(config.series);
  if (kind === 'graph') {
    const nodes = new Map<string, Plot['nodes'][number]>();
    for (const row of rows) {
      if (row[xi] == null || row[yi] == null) { plot.skipped++; continue; }
      const source = String(row[xi]), target = String(row[yi]);
      nodes.set(source, {id: source, name: source}); nodes.set(target, {id: target, name: target});
      plot.edges.push({source, target, type: si < 0 ? '' : String(row[si] ?? '')});
    }
    plot.nodes = [...nodes.values()]; return plot;
  }
  if (kind === 'histogram') {
    const values = rows.map(row => numeric(row[yi])).filter((v): v is number => v !== null);
    plot.skipped = rows.length - values.length;
    if (!values.length) return plot;
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= .5; max += .5; }
    const count = Math.min(60, Math.max(5, Math.ceil(Math.sqrt(values.length))));
    const step = (max - min) / count, bins = Array<number>(count).fill(0);
    for (const value of values) bins[Math.min(count - 1, Math.floor((value - min) / step))]++;
    plot.points = bins.map((y, i) => ({x: min + (i + .5) * step, y, label: `${format(min + i * step)}–${format(min + (i + 1) * step)}`, series: ''}));
    return plot;
  }
  const numericX = rows.every(row => row[xi] == null || numeric(row[xi]) !== null);
  const dates = !numericX && rows.every(row => row[xi] == null || (typeof row[xi] === 'string' && /^\d{4}-\d{2}/.test(String(row[xi])) && Number.isFinite(Date.parse(String(row[xi])))));
  plot.dateX = dates && kind !== 'bar';
  const categories = new Map<string, number>();
  const occurrences = new Map<string, number>();
  for (const row of rows) {
    const y = numeric(row[yi]), label = String(row[xi] ?? '');
    if (y === null || row[xi] == null) { plot.skipped++; continue; }
    const series = si < 0 ? '' : String(row[si] ?? '');
    const pair = JSON.stringify([label, series]);
    const occurrence = occurrences.get(pair) ?? 0;
    occurrences.set(pair, occurrence + 1);
    const key = kind === 'bar' ? JSON.stringify([label, occurrence]) : label;
    if (!categories.has(key)) { categories.set(key, categories.size); plot.categories.push(label); }
    const x = kind === 'bar' || (!numericX && !dates) ? categories.get(key)! : dates ? Date.parse(label) : numeric(row[xi]);
    if (x === null) { plot.skipped++; continue; }
    plot.points.push({x, y, label, series});
  }
  if (kind !== 'bar' && (numericX || dates)) plot.categories = [];
  return plot;
}

export function format(value: number): string {
  return new Intl.NumberFormat(undefined, {notation: Math.abs(value) >= 10000 ? 'compact' : 'standard', maximumFractionDigits: 3}).format(value);
}

/** Per-pixel extrema preserve spikes in dense line series without thousands of strokes. */
export function envelope(points: Point[], width: number): Point[] {
  if (points.length <= width * 4 || width < 1) return points;
  const sorted = [...points].sort((a, b) => a.x - b.x);
  const span = sorted[sorted.length - 1].x - sorted[0].x || 1;
  const buckets = new Map<number, Point[]>();
  for (const point of sorted) {
    const key = Math.floor((point.x - sorted[0].x) / span * width);
    const bucket = buckets.get(key);
    if (!bucket) buckets.set(key, [point, point, point, point]);
    else { if (point.y < bucket[1].y) bucket[1] = point; if (point.y > bucket[2].y) bucket[2] = point; bucket[3] = point; }
  }
  return [...buckets.values()].flatMap(bucket => [...new Set(bucket)].sort((a, b) => a.x - b.x));
}
