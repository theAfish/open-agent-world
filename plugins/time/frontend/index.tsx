import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Pause, Play, RotateCcw, useLocale, type FrontendPlugin, type PluginViewProps } from '@oaw/plugin-api';
import { calendarDays, dateKey, durationText, localDate, pad, timerValue, type Kind, type TimeValue } from './time';
import { CountEditor, TimeEditor } from './editors';
import { CityPicker } from './cities';
import './style.css';

const titles: Record<Kind, [string, string]> = {
  digital: ['Digital clock', '电子时钟'], clock: ['Analog clock', '圆盘时钟'], calendar: ['Calendar', '日历'],
  countdown: ['Countdown', '倒计时'], stopwatch: ['Stopwatch', '秒表'], pomodoro: ['Pomodoro', '番茄钟'], counter: ['Counter', '计数器'],
};
const segments = ['abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg', 'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg'];

/** Eight physical tubes: six numerals and two colon tubes, all drawn as vectors. */
export function TubeDisplay({ text }: { text: string }) {
  return <div className="time-tubes" role="img" aria-label={text}>{text.split('').map((character, i) =>
    <svg key={i} viewBox="0 0 40 70" className={`time-tube ${character === ':' ? 'is-colon' : ''}`} aria-hidden="true">
      <rect className="time-tube-glass" x="1" y="1" width="38" height="68" rx="18" />
      {character === ':' ? <g className="time-segment is-lit"><circle cx="20" cy="25" r="2"/><circle cx="20" cy="45" r="2"/></g> :
        <g>{Object.entries({ a: '12,13 28,13', b: '29,16 29,32', c: '29,38 29,54', d: '12,57 28,57', e: '10,38 10,54', f: '10,16 10,32', g: '12,35 28,35' }).map(([segment, points]) =>
          <polyline key={segment} points={points} className={`time-segment ${segments[Number(character)]?.includes(segment) ? 'is-lit' : ''}`} />)}</g>}
      <path d="M8 18 Q8 7 20 6" className="time-tube-reflection" />
    </svg>)}</div>;
}

export function Timepiece({ card, host }: PluginViewProps) {
  const { locale } = useLocale(), zh = locale === 'zh-CN';
  const progressId = useId();
  const tr = (en: string, cn: string) => zh ? cn : en;
  const kind = card.type.split('.').pop() as Kind;
  const [value, setValue] = useState<TimeValue | null>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [tick, setTick] = useState(Date.now());
  const [editing, setEditing] = useState<'duration' | 'clock' | 'count' | null>(null);
  const clockOffset = useRef(0), revision = useRef(-1), pending = useRef(false), alive = useRef(true);
  const accept = useCallback((result: { value: unknown; revision: number }) => {
    if (!alive.current || result.revision < revision.current) return;
    revision.current = result.revision;
    const next = result.value as TimeValue;
    if (next.server_now_ms !== undefined) clockOffset.current = next.server_now_ms - Date.now();
    else if (next.updated_at_ms) clockOffset.current = next.updated_at_ms - Date.now();
    setValue(next); setTick(Date.now());
  }, []);
  const load = useCallback(async () => {
    try { accept(await host.documentAction('read', {})); if (alive.current) setError(''); }
    catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); }
  }, [host.documentAction, accept]);
  useEffect(() => {
    alive.current = true; revision.current = -1; setValue(null); void load();
    const unsubscribe = host.onDocumentChange(change => { if (change.nodeId === card.id) void load(); });
    return () => { alive.current = false; unsubscribe(); };
  }, [card.id, load, host.onDocumentChange]);
  const animated = kind === 'digital' || kind === 'clock' || kind === 'calendar'
    || (value?.started_at_ms != null && timerValue(kind, value, tick + clockOffset.current).running);
  useEffect(() => {
    if (!animated) return;
    const timer = window.setInterval(() => setTick(Date.now()), kind === 'calendar' ? 30000 : kind === 'stopwatch' ? 80 : 250);
    return () => window.clearInterval(timer);
  }, [animated, kind]);
  const act = async (operation: string, arguments_: Record<string, unknown> = {}) => {
    if (pending.current || !value) return false;
    pending.current = true; setBusy(true); setError('');
    try { accept(await host.documentAction(operation, arguments_, revision.current)); return true; }
    catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { pending.current = false; if (alive.current) setBusy(false); }
  };
  const now = tick + clockOffset.current;
  const local = value ? localDate(value,now) : new Date(now);
  const today = dateKey(local);
  const timeText = `${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}:${pad(local.getUTCSeconds())}`;
  const timer = value ? timerValue(kind, value, now) : null;
  const title = titles[kind][zh ? 1 : 0];
  const zone = value && <CityPicker timeZone={value.time_zone} now={now+value.offset_ms} busy={busy} zh={zh} onSave={args => act('set_zone', args)}/>;
  const editor = value && editing && editing !== 'count' && <TimeEditor mode={editing === 'clock' ? 'clock' : 'duration'}
    milliseconds={editing === 'clock' ? (local.getUTCHours() * 3600 + local.getUTCMinutes() * 60 + local.getUTCSeconds()) * 1000 : value.duration_ms}
    date={today} busy={busy} zh={zh} onCancel={() => setEditing(null)}
    onUseSystemTime={editing==='clock' && value.offset_ms!==0 ? ()=>act('reset') : undefined}
    onSave={args => act(editing === 'clock' ? 'set_time' : 'set_duration', args)}/>;
  return <section className={`time-piece time-${kind}`} aria-label={title} data-editing={editing || undefined} data-running={timer?.running || undefined} data-finished={timer?.finished && kind !== 'stopwatch' || undefined}>
    {!value ? <div className="time-loading">{error ? tr('Unable to load', '无法加载') : tr('Loading…', '加载中…')}<button onClick={() => void load()}>{tr('Retry', '重试')}</button></div> : <>
      {kind === 'digital' && <><div className="time-instrument-top">{zone}</div>
        {editing === 'clock' ? editor : <button className="time-readout time-digital-readout nodrag nopan" disabled={busy} aria-label={tr('Edit time', '编辑时间')} onClick={() => setEditing('clock')}><TubeDisplay text={timeText}/></button>}
        {editing !== 'clock' && <footer className="time-footer"><button className="nodrag nopan" disabled={busy} aria-label={tr('Edit date and time', '编辑日期与时间')} onClick={() => setEditing('clock')}>{today.replaceAll('-', ' / ')}</button><span>{new Intl.DateTimeFormat(locale, { weekday: 'long', timeZone: 'UTC' }).format(local)}</span></footer>}</>}
      {kind === 'clock' && <><svg className="time-dial" viewBox="0 0 300 300" role="img" aria-label={timeText}>
        {Array.from({ length: 60 }, (_, i) => <line key={i} x1="150" y1={i % 5 === 0 ? 25 : 29} x2="150" y2="34" transform={`rotate(${i * 6} 150 150)`} className={i % 5 === 0 ? 'time-tick-major' : 'time-tick'} />)}
        {[12, 3, 6, 9].map((n, i) => <text key={n} x={[150, 251, 150, 49][i]} y={[54, 155, 257, 155][i]}>{n}</text>)}
        <line className="time-hand-hour" x1="150" y1="156" x2="150" y2="87" transform={`rotate(${(local.getUTCHours() % 12) * 30 + local.getUTCMinutes() / 2} 150 150)`}/>
        <line className="time-hand-minute" x1="150" y1="161" x2="150" y2="57" transform={`rotate(${local.getUTCMinutes() * 6 + local.getUTCSeconds() / 10} 150 150)`}/>
        <line className="time-hand-second" x1="150" y1="171" x2="150" y2="47" transform={`rotate(${local.getUTCSeconds() * 6} 150 150)`}/><circle cx="150" cy="150" r="4" className="time-pivot"/>
      </svg>{editing === 'clock' ? <div className="time-clock-editor">{editor}</div> : <>
        <button className="time-dial-edit nodrag nopan" disabled={busy} aria-label={tr('Edit time', '编辑时间')} onClick={() => setEditing('clock')}/>
        <div className="time-clock-meta"><button className="nodrag nopan" disabled={busy} aria-label={tr('Edit date and time', '编辑日期与时间')} onClick={() => setEditing('clock')}>{today.slice(5).replace('-', ' / ')}</button>
          <div>{zone}</div></div></>}</>}
      {kind === 'calendar' && <><Calendar value={value} today={today} locale={locale} busy={busy} act={act}/><div className="time-calendar-zone">{zone}</div></>}
      {kind === 'counter' && <>{editing === 'count' ? <CountEditor count={value.count} busy={busy} zh={zh} onCancel={() => setEditing(null)} onSave={args => act('set_count', args)}/> :
        <button className="time-readout time-count-readout nodrag nopan" disabled={busy} aria-label={tr('Edit count', '编辑计数')} onClick={() => setEditing('count')}><output className="time-count" aria-label={tr('Current count', '当前计数')}>{value.count.toLocaleString(locale)}</output></button>}
        {editing !== 'count' && <div className="time-counter-controls nodrag nopan"><button disabled={busy || value.count <= -999999999} aria-label={tr('Decrement count', '计数减一')} onClick={() => void act('decrement')}>−</button>
          <button className="time-reset" disabled={busy} aria-label={tr('Reset count', '计数归零')} title={tr('Reset count', '计数归零')} onClick={() => void act('reset')}><RotateCcw size={15} strokeWidth={1.6} aria-hidden="true"/></button>
          <button className="time-primary" disabled={busy || value.count >= 999999999} aria-label={tr('Increment count', '计数加一')} onClick={() => void act('increment')}>+</button></div>}</>}
      {timer && (kind === 'countdown' || kind === 'pomodoro' || kind === 'stopwatch') && <>
        {kind === 'pomodoro' && <svg className="time-progress-ring" viewBox="0 0 300 300" aria-hidden="true">
          <defs>
            <radialGradient id={`${progressId}-groove`} gradientUnits="userSpaceOnUse" cx="150" cy="150" r="134">
              <stop offset=".925" stopColor="var(--time-groove-lip)"/><stop offset=".94" stopColor="var(--time-groove-wall)"/>
              <stop offset=".955" stopColor="var(--time-groove-bed)"/><stop offset=".97" stopColor="var(--time-groove-bed)"/>
              <stop offset=".985" stopColor="var(--time-groove-wall)"/><stop offset="1" stopColor="var(--time-groove-lip)"/>
            </radialGradient>
          </defs>
          <circle className="time-progress-groove" cx="150" cy="150" r="129" stroke={`url(#${progressId}-groove)`}/>
          <g className="time-progress-light" transform="rotate(-90 150 150)" strokeDasharray="1" strokeDashoffset={timer.progress} opacity={timer.finished ? 0 : 1}>
            <circle className="time-progress-halo" cx="150" cy="150" r="129" pathLength="1"/>
            <circle className="time-progress-arc" cx="150" cy="150" r="129" pathLength="1"/>
            <circle className="time-progress-glint" cx="150" cy="150" r="129" pathLength="1"/>
          </g>
        </svg>}
        <div className="time-timer-main">{editing === 'duration' ? editor : <><span className="time-timer-status" role="status">{timer.finished ? tr('Complete', '已完成') : timer.running ? tr('Running', '进行中') : value.elapsed_ms ? tr('Paused', '已暂停') : ''}</span>
          {kind === 'stopwatch' ? <output className="time-digits" role="timer" aria-live="off" aria-label={tr('Timer value', '计时读数')}>{durationText(timer.milliseconds)}<small>.{pad(Math.floor(timer.milliseconds % 1000 / 10))}</small></output> :
            <button className="time-readout nodrag nopan" disabled={busy} aria-label={tr('Edit duration', '编辑时长')} title={tr('Click to set duration', '点击设置时长')} onClick={() => setEditing('duration')}><output className="time-digits" role="timer" aria-live="off" aria-label={tr('Timer value', '计时读数')}>{durationText(timer.milliseconds, true)}</output></button>}</>}
        </div>
        {kind === 'countdown' && editing !== 'duration' && <div className="time-progress-track" aria-hidden="true"><span style={{ width: `${timer.progress * 100}%`, opacity: timer.progress > 0 ? 1 : 0 }}/></div>}
        {editing !== 'duration' && <div className="time-timer-controls nodrag nopan"><button disabled={busy} aria-label={tr('Reset timer', '重置计时')} title={tr('Reset timer', '重置计时')} onClick={() => void act('reset')}><RotateCcw size={16} strokeWidth={1.5} aria-hidden="true"/></button>
          <button className="time-primary" disabled={busy} aria-label={timer.running ? tr('Pause', '暂停') : value.elapsed_ms && !timer.finished ? tr('Resume', '继续') : tr('Start', '开始')}
            title={timer.running ? tr('Pause', '暂停') : tr('Start / resume', '开始 / 继续')} onClick={() => void act(timer.running ? 'pause' : 'start')}>
            {timer.running ? <Pause size={16} strokeWidth={1.5} fill="currentColor" aria-hidden="true"/> : <Play size={16} strokeWidth={1.5} fill="currentColor" aria-hidden="true"/>}</button>
          {kind === 'stopwatch' && <button disabled={busy || !timer.running} aria-label={tr('Lap', '记圈')} title={tr('Lap', '记圈')} onClick={() => void act('lap')}><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 21V4c5-4 7 4 12 0v10c-5 4-7-4-12 0"/></svg></button>}
        </div>}
        {kind === 'pomodoro' && editing !== 'duration' && <div className="time-presets nodrag nopan"><button disabled={busy} onClick={() => void act('set_duration', { seconds: 1500 })}>{tr('Focus 25′', '专注 25′')}</button><button disabled={busy} onClick={() => void act('set_duration', { seconds: 300 })}>{tr('Break 5′', '休息 5′')}</button></div>}
        {kind === 'stopwatch' && <div className="time-laps nodrag nopan nowheel">{[...value.laps].reverse().map((lap, i) => <span key={`${i}:${lap}`}><small>{tr('Lap', '圈')} {value.laps.length - i}</small>{durationText(lap)}.{pad(Math.floor(lap % 1000 / 10))}</span>)}</div>}
      </>}
    </>}
    {error && <div className="time-error nodrag nopan" role="alert">{error}<button disabled={busy} onClick={() => void load()}>{tr('Reload', '重新加载')}</button></div>}
  </section>;
}

function Calendar({ value, today, locale, busy, act }: { value: TimeValue; today: string; locale: string; busy: boolean; act: (op: string, args?: Record<string, unknown>) => Promise<boolean> }) {
  const selected = value.selected_date || today;
  const [month, setMonth] = useState(selected.slice(0, 7));
  const [editingMonth, setEditingMonth] = useState(false);
  useEffect(() => setMonth(selected.slice(0, 7)), [selected]);
  const [year, number] = month.split('-').map(Number), date = new Date(`${month}-01T12:00:00Z`);
  const shift = (n: number) => { const next = new Date(date); next.setUTCMonth(next.getUTCMonth() + n); if (next.getUTCFullYear() >= 1 && next.getUTCFullYear() <= 9999) setMonth(dateKey(next).slice(0, 7)); };
  const zh = locale === 'zh-CN';
  return <div className="time-calendar-content nodrag nopan">
    <div className="time-month">{editingMonth ? <input type="month" aria-label={zh ? '显示月份' : 'Display month'} value={month} min="0001-01" max="9999-12" autoFocus
      onChange={e => {if(e.target.value) setMonth(e.target.value);}} onBlur={() => setEditingMonth(false)} onKeyDown={e => {if(e.key === 'Enter' || e.key === 'Escape') {e.stopPropagation(); setEditingMonth(false);}}}/> :
      <button className="time-month-readout" aria-label={zh ? '选择月份' : 'Choose month'} onClick={() => setEditingMonth(true)}><strong>{new Intl.DateTimeFormat(locale, { month: 'long', timeZone: 'UTC' }).format(date)}</strong><small>{year}</small></button>}<nav>
      <button aria-label={zh ? '上个月' : 'Previous month'} onClick={() => shift(-1)}>‹</button><button aria-label={zh ? '下个月' : 'Next month'} onClick={() => shift(1)}>›</button></nav></div>
    <div className="time-calendar-grid"><div className="time-weekdays">{(zh ? ['一', '二', '三', '四', '五', '六', '日'] : ['M', 'T', 'W', 'T', 'F', 'S', 'S']).map((day, i) => <span key={i}>{day}</span>)}</div>
      {calendarDays(year, number - 1).map(day => { const key = dateKey(day); return <button key={key} disabled={busy || day.getUTCFullYear() < 1 || day.getUTCFullYear() > 9999} aria-label={key} aria-pressed={key === selected}
        className={`${day.getUTCMonth() !== number - 1 ? 'is-outside' : ''} ${key === today ? 'is-today' : ''}`} onClick={() => void act('set_date', { date: key })}>{day.getUTCDate()}</button>; })}
    </div><footer><input type="date" aria-label={zh ? '选择日期' : 'Select date'} value={selected} min="0001-01-01" max="9999-12-31" disabled={busy}
      onChange={e => {if(e.target.value) void act('set_date', {date:e.target.value});}}/><button disabled={busy} onClick={() => { setMonth(today.slice(0, 7)); void act('reset'); }}>{zh ? '今天' : 'Today'}</button></footer>
  </div>;
}

export default { apiVersion: 1, views: { timepiece: Timepiece } } satisfies FrontendPlugin;
