import { useRef, useState } from 'react';
import { Modal } from './Modal';
import { apiErrorMessage } from '../api/client';
import { t } from '../i18n';

export interface Confirmation {
  title: string; description?: string; items?: string[]; action: string; run: () => Promise<void>;
}
export function ConfirmDialog({ confirmation, onClose }: { confirmation: Confirmation; onClose: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const lock = useRef(false);
  async function confirm() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await confirmation.run(); onClose(); }
    catch (reason) { setError(apiErrorMessage(reason)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <Modal title={confirmation.title} busy={busy} onClose={onClose} actions={<>
    <button type="button" data-autofocus className="secondary-button" disabled={busy} onClick={onClose}>{t('Cancel')}</button>
    <button type="button" className="primary-button ui-destructive" disabled={busy} onClick={() => void confirm()}>{busy ? t('Working…') : confirmation.action}</button>
  </>}>
    {confirmation.description && <p>{confirmation.description}</p>}
    {confirmation.items?.length ? <ul className="ui-confirm-items">{confirmation.items.map((item, index) => <li key={index}>{item}</li>)}</ul> : null}
    {error && <p role="alert" className="ui-error">{error}</p>}
  </Modal>;
}
