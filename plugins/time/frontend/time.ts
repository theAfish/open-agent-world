export type Kind = 'digital' | 'clock' | 'calendar' | 'countdown' | 'stopwatch' | 'pomodoro' | 'counter';
export interface TimeValue {
  time_zone: string;
  offset_minutes: number; offset_ms: number; selected_date: string;
  duration_ms: number; elapsed_ms: number; started_at_ms: number | null;
  updated_at_ms: number; count: number; laps: number[]; server_now_ms?: number;
}
const zoneFormatters = new Map<string, Intl.DateTimeFormat>();
/** A UTC Date containing the chosen city's wall-clock fields, for the dial and calendar. */
export function localDate(value: Pick<TimeValue,'time_zone'|'offset_minutes'|'offset_ms'>, now: number) {
  const stamp = now + value.offset_ms;
  if (!value.time_zone) return new Date(stamp + value.offset_minutes * 60000);
  let formatter = zoneFormatters.get(value.time_zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-GB', {timeZone:value.time_zone, year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
    zoneFormatters.set(value.time_zone,formatter);
  }
  const parts = Object.fromEntries(formatter.formatToParts(stamp).map(part=>[part.type,part.value]));
  return new Date(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}.000Z`);
}
export const pad = (value: number) => String(value).padStart(2, '0');
export function timerValue(kind: Kind, value: TimeValue, now: number) {
  const elapsed = value.elapsed_ms + (value.started_at_ms === null ? 0 : Math.max(0, now - value.started_at_ms));
  const finished = kind !== 'stopwatch' && elapsed >= value.duration_ms;
  return { milliseconds: kind === 'stopwatch' ? elapsed : Math.max(0, value.duration_ms - elapsed),
    running: value.started_at_ms !== null && !finished, finished,
    progress: Math.min(1, elapsed / value.duration_ms) };
}
export function durationText(ms: number, ceil = false) {
  const seconds = Math[ceil ? 'ceil' : 'floor'](ms / 1000);
  const hours = Math.floor(seconds / 3600);
  return hours ? `${pad(hours)}:${pad(Math.floor(seconds / 60) % 60)}:${pad(seconds % 60)}` : `${pad(Math.floor(seconds / 60))}:${pad(seconds % 60)}`;
}
export function calendarDays(year: number, month: number) {
  // Date.UTC treats years 0–99 as 1900–1999; setUTCFullYear preserves authored dates.
  const first = new Date(0); first.setUTCFullYear(year, month, 1); first.setUTCHours(0, 0, 0, 0);
  const start = first.getTime() - ((first.getUTCDay() + 6) % 7) * 86400000;
  return Array.from({ length: 42 }, (_, i) => new Date(start + i * 86400000));
}
export const dateKey = (date: Date) => date.toISOString().slice(0, 10);
