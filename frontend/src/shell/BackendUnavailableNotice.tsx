import { useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { t, useLocale } from "../i18n";
import { RefreshCw, ServerOff } from "lucide-react";
import { useWorldStore } from "../state/worldStore";
import { hostedConnectionState, isHosted, subscribeHostedConnection, verifyHostedSession } from '../auth/hostedSession';
import { overlayHost } from '../components/Modal';

export function BackendUnavailableNotice() {
  useLocale();
  const syncState = useWorldStore((state) => state.syncState);
  const syncError = useWorldStore((state) => state.syncError);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const connection = useSyncExternalStore(subscribeHostedConnection, hostedConnectionState);
  const hosted = isHosted();
  const reconnecting = hosted && connection === 'reconnecting';

  if (syncState !== "offline" && !reconnecting) return null;

  return createPortal(
    <aside className={`backend-unavailable${hosted ? ' is-hosted' : ''}`} role={hosted ? 'status' : 'alert'}>
      <ServerOff size={17} aria-hidden="true" />
      <div>
        <strong>{t(hosted ? 'Connection interrupted' : 'Local backend unavailable')}</strong>
        {hosted ? <p>{t('Your workspace remains open. Reconnecting automatically…')}</p>
          : <p>{t("Cannot reach the local backend. Start")} <code>./scripts/dev.ps1</code> {t("in a terminal, then retry.")}</p>}
        {!reconnecting && syncError ? <small>{syncError}</small> : null}
      </div>
      <button type="button" onClick={() => { void (reconnecting ? verifyHostedSession() : refreshWorld()); }} title={t("Retry backend connection")}>
        <RefreshCw size={15} aria-hidden="true" /> {t("Retry")} </button>
    </aside>, overlayHost()
  );
}
