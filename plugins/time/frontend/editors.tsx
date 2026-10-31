import { useState, type FormEvent } from 'react';
import { pad } from './time';

type Save = (arguments_: Record<string, unknown>) => Promise<boolean>;
interface EditorProps { busy: boolean; zh: boolean; onCancel: () => void; onSave: Save }

function EditorActions({ busy, zh, onCancel }: Pick<EditorProps, 'busy' | 'zh' | 'onCancel'>) {
  return <div className="time-edit-actions">
    <button type="button" disabled={busy} aria-label={zh ? '取消编辑' : 'Cancel editing'} onClick={onCancel}>×</button>
    <button className="time-edit-apply" disabled={busy} aria-label={zh ? '应用设置' : 'Apply changes'}>✓</button>
  </div>;
}

/** Edit the readout in place; drafts never change the saved instrument until applied. */
export function TimeEditor({ mode, milliseconds, date, busy, zh, onCancel, onSave, onUseSystemTime }: EditorProps & {
  mode: 'duration' | 'clock'; milliseconds: number; date?: string; onUseSystemTime?: () => Promise<boolean>;
}) {
  const seconds = Math.floor(milliseconds / 1000);
  const [parts, setParts] = useState([pad(Math.floor(seconds / 3600)), pad(Math.floor(seconds / 60) % 60), pad(seconds % 60)]);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const total = Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]);
    if (mode === 'duration' && (total < 1 || total > 604800)) {
      setError(zh ? '时长须在 1 秒至 7 天之间' : 'Choose a duration from 1 second to 7 days'); return;
    }
    const localTime = `${new FormData(event.currentTarget).get('date')}T${parts.map(part => pad(Number(part))).join(':')}`;
    if (await onSave(mode === 'duration' ? { seconds: total } : { local_time: localTime })) onCancel();
  };
  return <form className="time-inline-editor nodrag nopan nowheel" aria-label={zh ? '编辑时间' : 'Edit time values'} onSubmit={e => void submit(e)}
    onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel(); } }}>
    <div className="time-edit-segments">{parts.map((part, i) => <label key={i}>
      <input type="number" inputMode="numeric" min="0" max={i ? 59 : mode === 'clock' ? 23 : 168} step="1" required
        aria-label={(zh ? ['小时', '分钟', '秒'] : ['Hours', 'Minutes', 'Seconds'])[i]} value={part} disabled={busy} autoFocus={i === (mode === 'duration' ? 1 : 0)}
        onFocus={e => e.currentTarget.select()} onChange={e => { setParts(parts.map((old, n) => n === i ? e.target.value : old)); setError(''); }}
        onBlur={() => { if (part !== '') setParts(parts.map((old, n) => n === i ? pad(Number(old)) : old)); }}/>
      <span>{(zh ? ['时', '分', '秒'] : ['HR', 'MIN', 'SEC'])[i]}</span>
    </label>)}</div>
    {mode === 'clock' && <input className="time-edit-date" type="date" name="date" aria-label={zh ? '日期' : 'Date'} required
      min="1970-01-02" max="2240-01-01" defaultValue={date} disabled={busy}/>}
    {error && <span className="time-edit-error" role="alert">{error}</span>}
    <EditorActions busy={busy} zh={zh} onCancel={onCancel}/>
    {onUseSystemTime && <button className="time-use-system" type="button" disabled={busy} onClick={async()=>{if(await onUseSystemTime()) onCancel();}}>{zh?'使用系统时间':'Use system time'}</button>}
  </form>;
}

export function CountEditor({ count, busy, zh, onCancel, onSave }: EditorProps & {count: number}) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const count = Number(new FormData(event.currentTarget).get('count'));
    if (await onSave({value:count})) onCancel();
  };
  return <form className="time-count-editor nodrag nopan" onSubmit={e => void submit(e)} onKeyDown={e => {
    if(e.key === 'Escape') {e.preventDefault(); e.stopPropagation(); onCancel();}
  }}><input name="count" aria-label={zh ? '设置计数' : 'Set count'} type="number" step="1" min="-999999999" max="999999999" required
    defaultValue={count} disabled={busy} autoFocus onFocus={e => e.currentTarget.select()}/>
    <EditorActions busy={busy} zh={zh} onCancel={onCancel}/>
  </form>;
}

