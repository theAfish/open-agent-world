import { useEffect, useState } from 'react';
import { worldApi } from '../api/client';
import { t } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { StateMachineEventCatalog } from './apiTypes';
import { makeId, type MachineAction } from './model';

/** Dispatch choices are projected by the host from the same registrations as triggers. */
export function ActionEditor({ actions, cardId, onChange, onValidityChange }: {
  actions: MachineAction[]; cardId: string; onChange: (actions: MachineAction[]) => void; onValidityChange: (valid: boolean) => void;
}) {
  const cards = useWorldStore(state => state.cards);
  const [sourceId, setSourceId] = useState(cardId);
  const [catalog, setCatalog] = useState<StateMachineEventCatalog>();
  const [error, setError] = useState('');
  const [argumentsText, setArgumentsText] = useState<Record<string, string>>({});
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  useEffect(() => { let active = true; void worldApi.getStateMachineEvents(sourceId).then(result => { if (active) setCatalog(result); }).catch(() => { if (active) setError(t('Connected operations could not be loaded.')); }); return () => { active = false; }; }, [sourceId]);
  useEffect(() => { onValidityChange(!Object.values(invalid).some(Boolean)); }, [invalid, onValidityChange]);
  useEffect(() => () => onValidityChange(true), [onValidityChange]);
  const update = (id: string, patch: Partial<MachineAction>) => onChange(actions.map(action => action.id === id ? { ...action, ...patch } : action));
  const reference = (action: MachineAction, key: 'target' | 'caller') => <label>{t(key === 'target' ? 'Target binding' : 'Caller binding')}<select value={action[key].kind} onChange={event => update(action.id, { [key]: { kind: event.target.value as MachineAction['target']['kind'], ...(event.target.value === 'specific' ? { card_id: cardId } : {}), ...(['associated', 'produced'].includes(event.target.value) ? { index: 0 } : {}) } })}>
    <option value="current">{t('Current object')}</option><option value="specific">{t('Existing object')}</option><option value="associated">{t('Associated with this invocation')}</option><option value="produced">{t('Produced by this invocation')}</option>
  </select>{action[key].kind === 'specific' && <select aria-label={t(key === 'target' ? 'Target object' : 'Caller object')} value={action[key].card_id} onChange={event => update(action.id, { [key]: { kind: 'specific', card_id: event.target.value } })}>{cards.map(card => <option key={card.id} value={card.id}>{card.name}</option>)}</select>}
    {['associated', 'produced'].includes(action[key].kind) && <input aria-label={t('Association index')} type="number" min={0} step={1} value={action[key].index ?? 0} onChange={event => update(action.id, { [key]: { ...action[key], index: Math.max(0, Math.floor(Number(event.target.value))) } })} />}
  </label>;
  return <section aria-label={t('Actions')}>
    {error && <p role="alert" className="sm-error">{error}</p>}
    <label>{t('Action caller / object')}<select value={sourceId} onChange={event => { setSourceId(event.target.value); setCatalog(undefined); }}>{cards.map(card => <option key={card.id} value={card.id}>{card.name}</option>)}</select></label>
    <label>{t('Add action')}<select value="" onChange={event => {
      const operation = catalog?.operations.find(item => `${item.operation_id}:${item.target_card_id}` === event.target.value);
      const next: MachineAction = operation ? { id: makeId('action'), kind: operation.operation_kind === 'capability' ? 'capability' : 'node_action', operation_id: operation.operation_id,
        ...(operation.operation_kind === 'capability' ? { capability: operation.kind } : { action: operation.action }),
        target: { kind: 'specific', card_id: operation.target_card_id }, caller: sourceId === cardId ? { kind: 'current' } : { kind: 'specific', card_id: sourceId }, arguments: {} }
        : { id: makeId('action'), kind: 'run', target: sourceId === cardId ? { kind: 'current' } : { kind: 'specific', card_id: sourceId }, caller: sourceId === cardId ? { kind: 'current' } : { kind: 'specific', card_id: sourceId }, arguments: { prompt: '' } };
      onChange([...actions, next]);
    }}><option value="" disabled>{t('Choose an operation')}</option>
      {catalog?.sources.filter(source => source.kind === 'run').map(source => <option key={source.id} value={source.id}>{t('Start / continue')} {t(source.label)}</option>)}
      {catalog?.operations.map(operation => <option key={`${operation.operation_id}:${operation.target_card_id}`} value={`${operation.operation_id}:${operation.target_card_id}`}>{t(operation.label ?? operation.tool_name ?? operation.action ?? '')} · {operation.target_name}</option>)}
    </select></label>
    {actions.map(action => <div className="sm-effect" key={action.id}>
      <strong>{catalog?.operations.find(operation => action.kind === 'capability' ? operation.kind === action.capability : operation.action === action.action)?.label ?? action.capability ?? action.action ?? t('Agent Run')}</strong>
      {action.kind === 'run' ? <label>{t('Work request')}<textarea value={String(action.arguments.prompt ?? '')} onChange={event => update(action.id, { arguments: { ...action.arguments, prompt: event.target.value } })} /></label> : <label>{t('Arguments (JSON)')}<textarea spellCheck={false} aria-invalid={invalid[action.id] ?? false} value={argumentsText[action.id] ?? JSON.stringify(action.arguments, null, 2)} onChange={event => {
        const text = event.target.value; setArgumentsText(current => ({ ...current, [action.id]: text }));
        try { const value: unknown = JSON.parse(text); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(); update(action.id, { arguments: value as Record<string, unknown> }); setInvalid(current => ({ ...current, [action.id]: false })); }
        catch { setInvalid(current => ({ ...current, [action.id]: true })); }
      }} />{invalid[action.id] && <span role="alert">{t('Enter a JSON object.')}</span>}</label>}
      <details><summary>{t('Bindings')}</summary>{reference(action, 'target')}{reference(action, 'caller')}</details>
      <button className="sm-delete" onClick={() => { onChange(actions.filter(item => item.id !== action.id)); setInvalid(current => ({ ...current, [action.id]: false })); }}>{t('Remove action')}</button>
    </div>)}
  </section>;
}
