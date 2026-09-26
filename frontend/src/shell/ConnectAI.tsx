import { t, useLocale } from '../i18n';
import { modelRef, type ModelCatalog, type ModelConnection } from '../state/modelConnections';
import { ModelDiscovery } from './ModelDiscovery';

const providers = [
  { id: 'openai', name: 'OpenAI', adapter: 'openai', base_url: 'https://api.openai.com/v1' },
  { id: 'anthropic', name: 'Anthropic', adapter: 'anthropic', base_url: 'https://api.anthropic.com' },
  { id: 'gemini', name: 'Google Gemini', adapter: 'gemini', base_url: '' },
  { id: 'ollama', name: 'Ollama', adapter: 'openai', base_url: 'http://localhost:11434/v1' },
  { id: 'lmstudio', name: 'LM Studio', adapter: 'openai', base_url: 'http://localhost:1234/v1' },
] as const;

export function ConnectAI({ value, onChange, busy, onAdvanced }: {
  value: ModelCatalog; onChange: (value: ModelCatalog) => void; busy: boolean; onAdvanced: () => void;
}) {
  useLocale();
  const connection = value.connections[0];
  const update = (patch: Partial<ModelConnection>) => onChange({ ...value, connections: [{ ...connection, ...patch }] });
  return <fieldset className="connect-ai" disabled={busy}>
    <h3>{t('Connect AI')}</h3>
    <p>{t('Choose your service, connect your account, then pick a model. Your workspace will be ready after saving.')}</p>
    <div className="connect-ai-providers">{providers.map(provider => <button type="button" key={provider.id} className="secondary-button"
      aria-pressed={connection?.name === provider.name} onClick={() => {
        if (connection?.name === provider.name) return;
        onChange({ ...value, default_model: null, connections: [{ id: crypto.randomUUID(), name: provider.name,
          adapter: provider.adapter, base_url: provider.base_url, auth_mode: ['ollama', 'lmstudio'].includes(provider.id) ? 'none' : 'api_key',
          enabled: true, api_key_configured: false, models: [] }] });
      }}>{provider.name}</button>)}</div>
    {connection && <>
      {connection.auth_mode === 'none' ? <p>{t('Start the local model server first. The address refers to the computer running OAW.')}</p>
        : <label className="field-label"><span>{t('API key')}</span><input type="password" autoComplete="new-password" spellCheck={false} value={connection.api_key ?? ''} onChange={e => update({ api_key: e.target.value })} /></label>}
      <ModelDiscovery connection={connection} selectedModelId={connection.models[0]?.model_id} onSelect={model => onChange({ ...value,
        connections: [{ ...connection, models: [model] }], default_model: modelRef(model.id) })} />
      {connection.models[0] && <p role="status">{t('Selected model')}: <strong>{connection.models[0].name}</strong>. {t('Save to finish connecting.')}</p>}
    </>}
    <button type="button" className="text-button" onClick={onAdvanced}>{t('Custom service or manual model setup')}</button>
  </fieldset>;
}
