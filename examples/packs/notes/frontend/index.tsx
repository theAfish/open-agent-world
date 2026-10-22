import { useState } from 'react';
import type { FrontendPlugin, PluginViewProps } from '@oaw/plugin-api';

function Note({ card, host }: PluginViewProps) {
  const [message, setMessage] = useState(String(card.config.message));
  const [error, setError] = useState('');
  return <section className="nodrag nopan" style={{ padding: 12, minWidth: 0 }}>
    <p data-note-output style={{ overflowWrap: 'anywhere' }}>{String(card.config.message)}</p>
    <textarea aria-label="Note message" value={message} onChange={event => setMessage(event.target.value)}
      style={{ width: '100%', boxSizing: 'border-box' }} />
    <button onClick={() => void host.updateConfig({ message }).then(() => setError('')).catch(cause => setError(String(cause)))}>Save note</button>
    {error && <p role="alert">{error}</p>}
  </section>;
}

export default { apiVersion: 1, views: { note: Note } } satisfies FrontendPlugin;
