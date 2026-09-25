import { useState } from 'react';
import { t, type FrontendPlugin, type PluginViewProps } from '@oaw/plugin-api';

function Greeting({ card, host }: PluginViewProps) {
  const [name, setName] = useState(String(card.config.name ?? 'World'));
  const [error, setError] = useState('');
  return <div className="nodrag nopan" style={{ padding: 16 }}>
    <p data-greeter-output>{String(card.config.greeting ?? 'Hello, World!')}</p>
    <label>{t('Name')}<input aria-label="Greeter name" value={name} onChange={event => setName(event.target.value)} /></label>
    <button onClick={() => void host.updateConfig({ name, greeting: `Hello, ${name}!` }).catch(cause => setError(String(cause)))}>Greet</button>
    {error && <p role="alert">{error}</p>}
  </div>;
}

export default { apiVersion: 1, views: { greeting: Greeting } } satisfies FrontendPlugin;
