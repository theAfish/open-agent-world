import { useEffect, useRef, useState } from 'react';
import { worldApi } from '../api/client';
import { t } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { PluginCatalog, WorldCard } from '../types/world';
import type { StateMachineEventDescriptor, StateMachineEventSource } from './apiTypes';
import { eventLabel } from './eventLabels';
import type { MachineEvent, StateMachine } from './model';

export interface EventPickerProps {
  value: MachineEvent;
  machine: StateMachine;
  cards: WorldCard[];
  catalog: PluginCatalog;
  events: StateMachineEventDescriptor[];
  advanced?: boolean;
  onChange: (value: MachineEvent) => void;
}

export function sourceMatches(source: StateMachineEventSource, value: MachineEvent): boolean {
  return (source.capability ?? undefined) === value.capability
    && (source.operation_id ?? undefined) === value.operation_id
    && (source.target_card_id ?? undefined) === value.target_card_id
    && source.events.some(event => event.key === value.event);
}

/** No operation names, source IDs or lifecycle prefixes are interpreted in the client. */
export function EventPicker({ value, machine, cards, catalog, events, advanced = false, onChange }: EventPickerProps) {
  const edges = useWorldStore(state => state.edges);
  const source = machine.entities.find(entity => entity.id === value.entity_id);
  const cardId = source?.card_id ?? machine.references?.find(reference => reference.entity_id === value.entity_id)?.card_id;
  const requestKey = JSON.stringify([cardId, edges.map(edge => [edge.id, edge.source, edge.target, edge.relationship, edge.direction]),
    cards.map(card => [card.id, card.revision]), catalog.plugins]);
  const [available, setAvailable] = useState<{ key: string; sources: StateMachineEventSource[] }>();
  const remembered = useRef<StateMachineEventSource[]>([]);
  const [failedKey, setFailedKey] = useState<string>();
  const [retry, setRetry] = useState(0);
  const loading = available?.key !== requestKey && failedKey !== requestKey;
  const sources = available?.key === requestKey ? available.sources : [];
  const selected = sources.find(item => sourceMatches(item, value));
  const prior = remembered.current.find(item => sourceMatches(item, value));
  const selectedKey = selected?.id ?? prior?.id ?? (value.event === 'unconfigured' ? '' : `unavailable:${value.event}`);
  const phases = selected?.events ?? prior?.events ?? [];
  const label = (item: StateMachineEventSource) => item.target_name ? `${t(item.label)} \u00b7 ${item.target_name}` : t(item.label);

  useEffect(() => {
    let active = true;
    void worldApi.getStateMachineEvents(cardId ?? undefined).then(response => {
      if (!Array.isArray(response.sources)) throw new Error('Event source catalog unavailable');
      if (active) {
        remembered.current = [...response.sources, ...remembered.current];
        setAvailable({ key: requestKey, sources: response.sources });
      }
    }).catch(() => { if (active) setFailedKey(requestKey); });
    return () => { active = false; };
  }, [cardId, requestKey, retry]);

  return <div className="sm-event-picker">
    {advanced && (machine.entities.length > 1 || !source) && <label>{t('Trigger object')}<select value={value.entity_id} onChange={event => onChange({ entity_id: event.target.value, event: 'unconfigured' })}>
      {!source && <option value={value.entity_id}>{t('Referenced object')}</option>}
      {machine.entities.map(entity => <option key={entity.id} value={entity.id}>{entity.label}</option>)}
    </select></label>}
    <label>{t('Trigger / interface')}<select value={selectedKey} onChange={event => {
      const item = sources.find(item => item.id === event.target.value);
      if (item) onChange({ entity_id: value.entity_id, event: item.default_event,
        ...(item.capability ? { capability: item.capability } : {}),
        ...(item.operation_id ? { operation_id: item.operation_id } : {}),
        ...(item.target_card_id ? { target_card_id: item.target_card_id } : {}) });
    }}>
      <option value="" disabled>{t('Choose a trigger')}</option>
      {sources.map(item => <option key={item.id} value={item.id}>{label(item)}</option>)}
      {!selected && value.event !== 'unconfigured' && <option value={selectedKey} disabled>{prior ? label(prior) : eventLabel(value.event, events)}</option>}
    </select></label>
    {value.event !== 'unconfigured' && <label>{t('Trigger phase')}<select value={value.event} onChange={event => onChange({ ...value, event: event.target.value })}>
      {phases.map(phase => <option key={phase.key} value={phase.key}>{t(phase.label)}</option>)}
      {!phases.some(phase => phase.key === value.event) && <option value={value.event} disabled>{eventLabel(value.event, events)}</option>}
    </select></label>}
    {selected?.kind === 'user_state' && <label>{t('Entered state')}<select value={value.state_id ?? ''} onChange={event => { const { state_id: _, ...rest } = value; onChange(event.target.value ? { ...rest, state_id: event.target.value } : rest); }}><option value="">{t('Any state')}</option>{source?.states.map(state => <option key={state.id} value={state.id}>{state.label}</option>)}</select></label>}
    {loading && <p className="sm-help" role="status">{t('Loading connected operations...')}</p>}
    {failedKey === requestKey && <div className="sm-error" role="alert">{t('Connected operations could not be loaded.')}<button type="button" className="sm-text-button" onClick={() => { setFailedKey(undefined); setRetry(current => current + 1); }}>{t('Retry')}</button></div>}
    {value.event !== 'unconfigured' && !loading && failedKey !== requestKey && !selected && <p className="sm-help">{t('This operation is no longer available. Check its connection.')}</p>}
  </div>;
}
