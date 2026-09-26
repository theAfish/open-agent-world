import { useEffect, useRef, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import type { ConfiguredModel, ModelConnection } from '../state/modelConnections';

export function ModelDiscovery({ connection, onSelect, selectedModelId }: { connection: ModelConnection; onSelect: (model: ConfiguredModel) => void; selectedModelId?: string }) {
  // Credentials/address changes discard results and abort the previous request.
  const identity = JSON.stringify([connection.id, connection.adapter, connection.base_url, connection.api_key, connection.clear_api_key, connection.auth_mode, connection.environment_variable]);
  return <ModelPicker key={identity} connection={connection} onSelect={onSelect} selectedModelId={selectedModelId} />;
}

function ModelPicker({ connection, onSelect, selectedModelId }: { connection: ModelConnection; onSelect: (model: ConfiguredModel) => void; selectedModelId?: string }) {
  useLocale();
  const [result, setResult] = useState<{ models: { id: string; name: string }[]; truncated: boolean }>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const request = useRef<AbortController>();
  useEffect(() => () => request.current?.abort(), []);
  if (!['openai', 'anthropic', 'gemini'].includes(connection.adapter)) return null;
  const discover = async () => {
    if (loading) return;
    const controller = new AbortController();
    request.current = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    setLoading(true); setError(''); setResult(undefined);
    try {
      const found = await worldApi.discoverModels(connection, controller.signal);
      if (!controller.signal.aborted) setResult(found);
    } catch (cause) {
      if (!controller.signal.aborted) setError(apiErrorMessage(cause));
      else setError(t('The request timed out. Check the service and retry.'));
    } finally { clearTimeout(timeout); setLoading(false); }
  };
  return <div className="model-discovery">
    <button type="button" className="secondary-button" disabled={loading} onClick={() => void discover()}>{t(loading ? 'Connecting…' : 'Get available models')}</button>
    <small>{t('Lists models without sending a paid conversation. Choose a model that supports chat and tools.')}</small>
    {error && <p role="alert" className="settings-error">{t(error)}</p>}
    {result && <>
      <label className="field-label"><span>{t('Available models')}</span><select value={selectedModelId ?? ''} disabled={connection.models.length >= 100} onChange={event => {
        const found = result.models.find(model => model.id === event.target.value);
        if (found) onSelect({ id: crypto.randomUUID(), model_id: found.id, name: found.name, enabled: true });
      }}>
        <option value="">{t('Choose a model')}</option>
        {result.models.map(model => <option key={model.id} value={model.id} disabled={connection.models.some(existing => existing.model_id === model.id)}>{model.name === model.id ? model.name : `${model.name} · ${model.id}`}</option>)}
      </select></label>
      {!result.models.length && <p role="status">{t('No models were returned. Install a local model or add a model manually.')}</p>}
      {result.truncated && <p role="status">{t('Showing part of the model list. You can also add a model manually.')}</p>}
    </>}
  </div>;
}
