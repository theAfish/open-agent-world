import { useRef, useState } from 'react';
import { apiErrorMessage } from '../api/client';
import { Modal } from '../components/Modal';
import { t, useLocale } from '../i18n';
import { restartApplication } from '../state/applicationRestart';
import { currentProfile } from '../state/profileStorage';

export function PackRestartPrompt({ onClose, portalHost }: { onClose: () => void; portalHost?: Element }) {
  useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const supported = currentProfile()?.restart_supported !== false;
  const restart = async () => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await restartApplication(); }
    catch (cause) { setError(apiErrorMessage(cause)); lock.current = false; setBusy(false); }
  };
  return <Modal title={t('Restart OAW now?')} portalHost={portalHost} busy={busy} onClose={onClose} actions={<>
    <button type="button" data-autofocus className="secondary-button" disabled={busy} onClick={onClose}>{t('Restart later')}</button>
    {supported && <button type="button" className="primary-button" disabled={busy} onClick={() => void restart()}>{t(busy ? 'Restarting…' : 'Restart now')}</button>}
  </>}>
    <p>{t('Installation complete. Restart OAW to activate the new cards.')}</p>
    <p>{t(supported ? 'Save your work before restarting. Running tasks will stop, and this page will refresh automatically.'
      : 'Automatic restart is unavailable. Restart OAW using its launcher.')}</p>
    {busy && <p role="status">{t('Restarting OAW. This page will refresh when ready.')}</p>}
    {error && <p role="alert" className="ui-error">{t(error)}</p>}
  </Modal>;
}
