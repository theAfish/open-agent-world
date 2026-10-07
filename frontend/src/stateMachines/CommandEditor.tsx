import { useEffect, useState } from 'react';
import { t } from '../i18n';
import type { MachineEntity, TransitionRule } from './model';

export function CommandEditor({ request, group, onChange, onValidityChange }: {
  request: NonNullable<TransitionRule['command']>; group: MachineEntity;
  onChange: (request: NonNullable<TransitionRule['command']>) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const [input, setInput] = useState(JSON.stringify(request.arguments, null, 2));
  const [error, setError] = useState('');
  const commands = group.commands?.filter(command => command.outcomes.includes(request.state_id)) ?? [];
  const command = commands.find(command => command.id === request.command_id);
  useEffect(() => { setInput(JSON.stringify(request.arguments, null, 2)); setError(''); onValidityChange(true); }, [request.command_id]);
  return <div className="sm-command-editor">
    <strong>{t('Request')} {group.label} / {group.states.find(state => state.id === request.state_id)?.label}</strong>
    <label>{t('Via')}<select aria-label={t('Via')} value={request.command_id} onChange={event => onChange({...request, command_id: event.target.value, arguments: commands.find(item => item.id === event.target.value)?.kind === 'run' ? {prompt: ''} : {}})}>
      {commands.map(command => <option key={command.id} value={command.id}>{command.label}</option>)}
    </select></label>
    {command?.kind === 'run' ? <label>{t('Input')}<textarea value={String(request.arguments.prompt ?? '')} placeholder={t('Prompt')} onChange={event => onChange({...request, arguments: {...request.arguments, prompt: event.target.value}})} /></label>
      : <label>{t('Input')}<textarea value={input} onChange={event => { setInput(event.target.value); try { const args: unknown = JSON.parse(event.target.value); if (!args || typeof args !== 'object' || Array.isArray(args)) throw Error('Enter a JSON object.'); setError(''); onValidityChange(true); onChange({...request, arguments: args as Record<string, unknown>}); } catch { setError(t('Enter a JSON object.')); onValidityChange(false); } }} /></label>}
    {error && <p role="alert">{error}</p>}
    <p className="sm-help">{t('Requests an operation. The state changes only when the runtime observes it.')}</p>
    <details className="sm-more"><summary>{t('Operation details')}</summary><code>{command?.operation_id}</code><p>{command?.authorization.join(' · ')}</p><pre>{JSON.stringify(command?.input_schema, null, 2)}</pre></details>
  </div>;
}
