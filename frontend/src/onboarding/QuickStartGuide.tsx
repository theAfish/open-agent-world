import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import { useEffect, useRef, useState } from 'react';
import { apiErrorMessage } from '../api/client';
import { hasModelConfiguration } from '../state/modelConnections';
import { tutorial, useTutorialStore } from './controller';

export function QuickStartGuide() {
  useLocale();
  const setup = useTutorialStore(s => s.quickStart);
  const cards = useWorldStore(s => s.cards);
  const models = useWorldStore(s => s.modelCatalog);
  const settingsOpen = useWorldStore(s => s.settingsOpen);
  const deploying = useTutorialStore(s => s.busy);
  const deploymentError = useTutorialStore(s => s.error);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const attempted = useRef<string>();
  const agent = cards.find(card => card.id === setup?.agentId);
  const needsModel = agent?.type === 'agent' && !hasModelConfiguration(models, agent.config.model);
  const open = async () => {
    if (busy || deploying) return;
    setBusy(true); setError('');
    try { await tutorial.openQuickStart(); }
    catch (cause) { setError(apiErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!setup || !agent || needsModel || settingsOpen || deploying || attempted.current === setup.conversationId) return;
    attempted.current = setup.conversationId;
    void open();
  }, [setup?.conversationId, agent?.id, needsModel, settingsOpen, deploying]);
  if (!setup || !agent) return null;
  return <section className="quick-start-guide" aria-label={t('Quick Start setup')}>
    <strong>{t(needsModel ? 'Connect a model to start chatting' : 'Your first workspace')}</strong>
    <p>{t(needsModel ? 'Your Agent and Conversation are connected. Save a model connection in Settings, then come back to your Conversation.'
      : 'Your workspace is ready. Open it to review and send your first message.')}</p>
    {(error || deploymentError) && <p role="alert">{error || deploymentError}</p>}
    <div className="action-row">
      {needsModel && <button type="button" className="secondary-button" onClick={() => useWorldStore.setState({ settingsOpen: true })}>{t('Connect AI')}</button>}
      {!needsModel && <button type="button" className="primary-button" disabled={busy || deploying} onClick={() => void open()}>{t(busy ? 'Preparing workspace…' : 'Open workspace')}</button>}
      <button type="button" className="onboarding-text-button" disabled={busy || deploying} onClick={() => useTutorialStore.setState({ quickStart: undefined })}>{t(needsModel ? 'Set up later' : 'Got it')}</button>
    </div>
  </section>;
}
