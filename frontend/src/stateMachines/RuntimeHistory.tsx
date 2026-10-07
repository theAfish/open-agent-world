import { t } from '../i18n';
import type { MachineEntity } from './model';

export function RuntimeHistory({ diagnostics, actions, entities }: { diagnostics: Record<string, unknown>[]; actions: Record<string, unknown>[]; entities: MachineEntity[] }) {
  const records = diagnostics.filter(record => record.error || ['system_transitions', 'user_transitions'].some(key => Array.isArray(record[key]) && record[key].length));
  return <details className="sm-runtime-history"><summary>{t('Run history')}</summary>
    {!records.length && <p className="sm-help">{t('No state changes yet.')}</p>}
    {records.map((record, index) => {
      const transitions = [...Array.isArray(record.system_transitions) ? record.system_transitions : [], ...Array.isArray(record.user_transitions) ? record.user_transitions : []] as {entity_id: string; from_state: string; state_id: string; reason?: string}[];
      return <article className="sm-runtime-record" key={index}>
        {record.created_at ? <time>{new Date(String(record.created_at)).toLocaleString()}</time> : null}
        {transitions.map((change, i) => {
          const entity = entities.find(item => item.id === change.entity_id);
          const label = (id: string) => t(entity?.states.find(state => state.id === id)?.label ?? id);
          const reason = entity?.projection?.find(item => item.event === change.reason)?.label;
          return <p key={i}>{label(change.from_state)} → {label(change.state_id)}{reason ? ` · ${t(reason)}` : ''}</p>;
        })}
        {record.error ? <p role="alert">{t('Execution failed')}</p> : null}
        <details><summary>{t('Technical details')}</summary><pre>{JSON.stringify(record, null, 2)}</pre></details>
      </article>;
    })}
    {!!actions.length && <details><summary>{t('Action details')}</summary><pre>{JSON.stringify(actions, null, 2)}</pre></details>}
  </details>;
}
