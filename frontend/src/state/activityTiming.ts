import type { RunActivityItem } from './runActivity';

export function compareTimestamp(a: string, b: string): number {
  const milliseconds = (Date.parse(a) || 0) - (Date.parse(b) || 0);
  if (milliseconds) return milliseconds;
  // Backend records carry microseconds; Date.parse only retains milliseconds.
  const fraction = (value: string) => (value.match(/\.(\d+)(?:Z|[+-]\d\d:\d\d)$/)?.[1] ?? '').padEnd(6, '0');
  return fraction(a).localeCompare(fraction(b));
}

export function elapsedMilliseconds(start?: string | null, end?: string | number | null): number | undefined {
  if (!start || end == null) return undefined;
  const duration = (typeof end === 'number' ? end : Date.parse(end)) - Date.parse(start);
  return Number.isFinite(duration) && duration >= 0 ? duration : undefined;
}

export function toolElapsed(item: RunActivityItem, running: boolean, now: number): number | undefined {
  return elapsedMilliseconds(item.started_at, item.finished_at ?? (running && item.type === 'tool_started' ? now : undefined));
}

/** Wall-clock span, not a sum: parallel calls must not double-count time.
 * An incomplete timestamp pair cannot yield a reliable completed duration. */
export function executionElapsed(items: RunActivityItem[], running: boolean, now: number): number | undefined {
  if (!items.length || items.some(item => toolElapsed(item, running, now) === undefined)) return undefined;
  const start = Math.min(...items.map(item => Date.parse(item.started_at!)));
  const end = Math.max(...items.map(item => item.finished_at ? Date.parse(item.finished_at) : now));
  return end - start;
}
