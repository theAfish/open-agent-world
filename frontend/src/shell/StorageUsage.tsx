import { useState } from 'react';
import { worldApi, type StorageUsage as Usage } from '../api/client';
import { t } from '../i18n';

const bytes = (value: number) => `${(value / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 1 })} MiB`;

export function StorageUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState<string | null>(null);
  const inspect = async () => {
    setBusy(true); setError('');
    try { setUsage(await worldApi.inspectStorage()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t('Could not inspect storage.')); }
    finally { setBusy(false); }
  };
  const remove = async (name: string) => {
    setBusy(true); setError('');
    try {
      await worldApi.deleteUpdateBackup(name);
      setConfirm(null);
      setUsage(await worldApi.inspectStorage());
    } catch (cause) { setError(cause instanceof Error ? cause.message : t('Could not remove backup.')); }
    finally { setBusy(false); }
  };
  return <section aria-label={t('Storage usage')}>
    <button className="secondary-button" type="button" disabled={busy} onClick={() => void inspect()}>{busy ? t('Inspecting storage…') : t('Analyze storage')}</button>
    {error && <p role="alert">{error}</p>}
    {usage && <>
      <dl>{([
        ['user_data', t('User data')], ['managed', t('Managed environments and cache')],
        ['temporary', t('Temporary files')], ['backups', t('Update backups')],
      ] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{bytes(usage.categories[key])}</dd></div>)}</dl>
      <p>{t('Reclaimable space')}: {bytes(usage.reclaimable_bytes)}</p>
      <p>{t('Backups are kept until you remove them. The latest update backup is protected for recovery.')}</p>
      <ul>{usage.backups.map(backup => <li key={backup.name}>
        <code>{backup.path}</code> — {bytes(backup.size_bytes)}{' '}
        {backup.removable && (confirm === backup.name ? <>
          <button type="button" disabled={busy} className="secondary-button" onClick={() => void remove(backup.name)}>{t('Confirm deletion')}</button>{' '}
          <button type="button" disabled={busy} className="secondary-button" onClick={() => setConfirm(null)}>{t('Cancel')}</button>
        </> : <button type="button" disabled={busy} className="secondary-button" onClick={() => setConfirm(backup.name)}>{t('Delete backup')}</button>)}
      </li>)}</ul>
    </>}
  </section>;
}
