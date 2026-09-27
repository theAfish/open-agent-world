import { useState } from 'react';
import { ArrowLeft, Archive, Trash2 } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import { useCardLibrary, type LibrarySnapshot } from '../state/cardLibrary';
import type { PackInstallations } from '../types/packs';
import { PackGuide } from './PackCreator';
import { installedPackStatus, type InstalledPack } from './installedPacks';

export function InstalledPackDetails({ pack, versions, registered, snapshot, onBack, onBrowse, onChanged }: {
  pack: LibrarySnapshot['packs'][string]; versions: InstalledPack[]; registered: boolean; snapshot: LibrarySnapshot;
  onBack: () => void; onBrowse: (id: string) => void; onChanged: (value: PackInstallations) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const library = useCardLibrary();
  const selected = versions.find(item => item.selected);
  const item = selected ?? versions.find(row => row.loaded) ?? versions[0];
  const loaded = versions.find(row => row.loaded);
  const status = installedPackStatus(versions);
  const mutate = async (path: string, method: string, body?: unknown) => {
    setBusy(true); setError('');
    try { onChanged(await worldApi.managePack(path, method, body)); setConfirmRemoval(false); }
    catch (cause) { setError(apiErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  const open = async () => {
    const result = await library.edit({ action: 'open_pack', id: pack.definition.id });
    if (result?.packs[pack.definition.id]?.opened) onBrowse(pack.definition.id);
  };
  return <section className="installed-pack-detail" aria-label={t('Pack details')}>
    <button className="library-text-button pack-back-button" onClick={onBack}><ArrowLeft size={15} />{t('Back to packs')}</button>
    <header className="installed-pack-heading"><div className="installed-pack-icon"><Archive size={28} /></div>
      <div><h3>{pack.definition.name}</h3><span>{item?.version} · {t(status)}</span></div></header>
    {(item?.creator?.description || pack.definition.description) && <p>{item?.creator?.description || pack.definition.description}</p>}
    {(status === 'Restart required' || status === 'Uninstall pending restart') && <p className="pack-restart-note">{t('Restart OAW to activate Pack changes.')}</p>}
    <div className="pack-detail-actions">
      {registered && pack.opened && <button className="secondary-button" onClick={() => onBrowse(pack.definition.id)}>{t('View cards')}</button>}
      {registered && !pack.opened && snapshot.available_pack_ids.includes(pack.definition.id) && <button className="secondary-button" disabled={busy || library.busy} onClick={() => void open()}>{t('Open pack')}</button>}
      {selected && !confirmRemoval && <button className="library-text-button pack-uninstall-button" disabled={busy} onClick={() => setConfirmRemoval(true)}><Trash2 size={14} />{t('Uninstall Pack')}</button>}
    </div>
    {confirmRemoval && selected && <div className="pack-uninstall-confirm">
      <p>{t('Uninstall {name}? This takes effect after restart.', { name: pack.definition.name })}</p>
      <div className="pack-detail-actions"><button className="secondary-button" disabled={busy} onClick={() => void mutate(encodeURIComponent(selected.id), 'DELETE')}>{t('Confirm uninstall')}</button>
        <button className="library-text-button" disabled={busy} onClick={() => setConfirmRemoval(false)}>{t('Cancel')}</button></div>
    </div>}
    {error && <p className="pack-action-error" role="alert">{error}</p>}
    {item?.creator && <details className="pack-detail-section"><summary>{t('Pack guide')}</summary><PackGuide creator={item.creator} /></details>}
    {loaded?.environment?.state === 'environment_failed' && <div className="pack-detail-section">
      <p role="alert" className="pack-action-error">{loaded.environment.error}</p>
      <button className="secondary-button" disabled={busy} onClick={() => void mutate('environment/retry', 'POST')}>{t('Retry environment preparation')}</button>
    </div>}
    {versions.some(row => !row.selected) && <details className="pack-detail-section"><summary>{t('Other versions')}</summary>
      {versions.filter(row => !row.selected).map(row => <div className="pack-version-row" key={row.version}>
        <span>{row.version} · {t(row.loaded ? 'Loaded' : 'Retained version')}</span>
        <button className="library-text-button" disabled={busy} onClick={() => void mutate(`${encodeURIComponent(row.id)}/activate`, 'POST', { version: row.version })}>{t('Use this version on restart')}</button>
        {!row.loaded && <button className="library-text-button" disabled={busy} onClick={() => void mutate(`${encodeURIComponent(row.id)}/versions/${encodeURIComponent(row.version)}`, 'DELETE')}>{t('Remove retained version')}</button>}
      </div>)}
    </details>}
  </section>;
}
