import { useEffect, useState, type FormEvent } from 'react';
import { LockKeyhole, LogOut } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { configureWorkspaceApi, normalizeCard } from '../api/client';
import { WorkspaceWindow } from '../legions/LegionWorkspace';
import { WorkspaceAccess, type PluginDeploymentAccess } from '../workspace/WorkspaceAccess';
import { useWorldStore } from '../state/worldStore';
import { ToastStack } from '../shell/ToastStack';
import type { PluginCatalog, WorldCard } from '../types/world';
import { deploymentRequest } from './api';
import './deployment.css';

type PublishedWorkspace = {
  id: string; name: string; cards: WorldCard[]; legion: WorldCard;
  catalog: PluginCatalog; permissions: Record<string, string[]>;
  plugin_access?: Record<string, PluginDeploymentAccess>;
};

/** Authentication and scoped data bootstrap; all workspace rendering stays shared. */
export function RuntimeApp({ name }: { name: string }) {
  const locale = useLocale(state => state.locale);
  const theme = useWorldStore(state => state.theme);
  const [app, setApp] = useState<PublishedWorkspace>();
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => { document.documentElement.lang = locale; document.title = name; }, [locale, name]);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const clear = () => {
    setApp(undefined);
    useWorldStore.setState({ cards: [], edges: [], events: [], toasts: [], sandboxInfo: {}, sandboxErrors: {} });
  };
  const load = async () => {
    const value = await deploymentRequest<PublishedWorkspace>('/runtime-app');
    configureWorkspaceApi(true);
    const legion = normalizeCard(value.legion);
    useWorldStore.setState({ cards: [legion, ...value.cards.map(normalizeCard)], catalog: value.catalog,
      edges: [], events: [], socketState: 'closed', syncState: 'online' });
    setApp({ ...value, legion });
  };
  useEffect(() => {
    let active = true;
    void load().catch(() => {}).finally(() => { if (active) setReady(true); });
    const expired = () => { clear(); setError(t('Your session expired. Sign in again.')); };
    window.addEventListener('oaw-session-expired', expired);
    return () => { active = false; window.removeEventListener('oaw-session-expired', expired); };
  }, []);
  useEffect(() => {
    if (!app) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void deploymentRequest<PublishedWorkspace>('/runtime-app', { signal: controller.signal }).then(value => {
        if (controller.signal.aborted) return;
        const byId = new Map(value.cards.map(card => [card.id, card]));
        useWorldStore.setState(state => ({ cards: state.cards.map(card => {
          const live = byId.get(card.id);
          return live ? { ...card, status: live.status, config: { ...card.config, ...live.config } } : card;
        }) }));
      }).catch(() => {});
    }, 3000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [app?.id]);
  const login = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await deploymentRequest('/deployment/session', { method: 'POST', body: JSON.stringify({ password }) });
      setPassword(''); await load();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  if (!ready) return <main className="deployment-login">{t('Connecting…')}</main>;
  if (!app) return <main className="deployment-login"><form onSubmit={event => { void login(event); }}>
    <LockKeyhole size={28} /><h1>{name}</h1><p>{t('Enter the access password provided by your administrator.')}</p>
    <label>{t('Access password')}<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required maxLength={1024} /></label>
    {error && <p className="deployment-error" role="alert">{error}</p>}
    <button className="primary-button" disabled={busy}>{busy ? t('Signing in…') : t('Open application')}</button>
  </form></main>;
  return <WorkspaceAccess.Provider value={{ deployed: true, permissions: app.permissions, plugin_access: app.plugin_access }}>
    <WorkspaceWindow key={app.id} card={app.legion} locked actions={<button className="secondary-button" onClick={() => {
      void deploymentRequest('/runtime-app/session', { method: 'DELETE' }).then(clear)
        .catch(reason => useWorldStore.getState().pushToast({ tone: 'error', title: reason.message }));
    }}><LogOut size={14} />{t('Sign out')}</button>} />
    <ToastStack />
  </WorkspaceAccess.Provider>;
}
