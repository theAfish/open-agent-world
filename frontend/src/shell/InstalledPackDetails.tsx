import { useState } from 'react';
import { ArrowLeft, Archive, Trash2 } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import { useCardLibrary, type LibrarySnapshot } from '../state/cardLibrary';
import type { PackInstallations, PackInventoryItem, PackUninstallPlan } from '../types/packs';
import { PackGuide } from './PackCreator';
import { installedPackStatus, packIssue, type InstalledPack } from './installedPacks';
import { PackRuntimeDetails } from './PackDependencies';

export function InstalledPackDetails({ pack, versions, inventory, registered, failures = [], snapshot, onBack, onBrowse, onChanged }: {
  pack: LibrarySnapshot['packs'][string]; versions: InstalledPack[]; registered: boolean; snapshot: LibrarySnapshot;
  inventory?: PackInventoryItem; failures?: NonNullable<PackInstallations['failures']>;
  onBack: () => void; onBrowse: (id: string) => void; onChanged: (value: PackInstallations) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const [removalPlan, setRemovalPlan] = useState<PackUninstallPlan>();
  const library = useCardLibrary();
  const selected = versions.find(item => item.selected);
  const item = selected ?? versions.find(row => row.loaded) ?? versions[0];
  const source = inventory && inventory.source !== 'installed' ? inventory : undefined;
  const removableId = selected?.id ?? (source?.enrolled !== false ? source?.id : undefined);
  const environmentErrors = [...new Set([...failures.map(failure => failure.error),
    ...versions.flatMap(row => row.environment?.state === 'environment_failed' ? [row.environment.error || 'Environment failed'] : []),
    ...(inventory?.environments.flatMap(environment => environment.state === 'failed' ? [environment.error || 'Environment failed'] : []) ?? [])])];
  const issue = environmentErrors.length ? { kind: 'error', label: 'Environment failed' } : packIssue(pack, snapshot, versions);
  const status = source?.enrolled === false ? source.loaded ? 'Uninstall pending restart' : 'Pack uninstalled' : issue?.label ?? (versions.length ? installedPackStatus(versions)
    : inventory ? !inventory.enabled ? 'Pack disabled' : inventory.loaded ? 'Loaded' : 'Not loaded' : 'Installed');
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
  const previewRemoval = async () => {
    if (!removableId) return;
    setBusy(true); setError(''); setRemovalPlan(undefined); setConfirmRemoval(true);
    try { setRemovalPlan(await worldApi.getPackUninstallPlan(removableId)); }
    catch (cause) { setError(apiErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  return <section className="installed-pack-detail" data-pack-issue={issue?.kind} aria-label={t('Pack details')}>
    <button className="library-text-button pack-back-button" onClick={onBack}><ArrowLeft size={15} />{t('Back to packs')}</button>
    <header className="installed-pack-heading"><div className="installed-pack-icon"><Archive size={28} /></div>
      <div><h3>{pack.definition.name}</h3><span>{item?.version ?? inventory?.version} · {t(status)}</span></div></header>
    {(item?.creator?.description || pack.definition.description) && <p>{item?.creator?.description || pack.definition.description}</p>}
    {(status === 'Restart required' || status === 'Uninstall pending restart') && <p className="pack-restart-note">{t('Restart OAW to activate Pack changes.')}</p>}
    <div className="pack-detail-actions">
      {registered && pack.opened && <button className="secondary-button" onClick={() => onBrowse(pack.definition.id)}>{t('View cards')}</button>}
      {!issue && registered && !pack.opened && snapshot.available_pack_ids.includes(pack.definition.id) && <button className="secondary-button" disabled={busy || library.busy} onClick={() => void open()}>{t('Open pack')}</button>}
      {removableId && !confirmRemoval && <button className="library-text-button pack-uninstall-button" disabled={busy} onClick={() => void previewRemoval()}><Trash2 size={14} />{t('Uninstall Pack')}</button>}
      {source?.enrolled === false && <button className="secondary-button" disabled={busy} onClick={() => void mutate(`${encodeURIComponent(source.id)}/restore`, 'POST')}>{t('Restore Pack')}</button>}
    </div>
    {confirmRemoval && removableId && <div className="pack-uninstall-confirm" aria-label={t('Uninstall plan')}>
      <p>{t('Uninstall {name}?', { name: pack.definition.name })}</p>
      {busy && !removalPlan && <p role="status">{t('Loading...')}</p>}
      {removalPlan?.restart_required && <p>{t('Restart OAW to finish removing unused dependencies.')}</p>}
      {removalPlan?.source !== 'installed' && removalPlan && <p>{t('Source files stay in your checkout. You can restore this Pack later.')}</p>}
      {!!removalPlan?.blockers.length && <ul role="alert">{removalPlan.blockers.map(message => <li key={message}>{message}</li>)}</ul>}
      {!!removalPlan?.resources.length && <ul className="pack-removal-resources">{removalPlan.resources.map(resource => <li key={`${resource.kind}:${resource.id}`}>
        <strong>{t(resource.action === 'remove' ? 'Remove' : resource.action === 'retain' ? 'Retain' : 'After restart')}</strong>{' '}{resource.id}<small>{resource.reason}</small>
      </li>)}</ul>}
      <div className="pack-detail-actions"><button className="secondary-button" disabled={busy || !removalPlan?.can_uninstall || !!removalPlan.blockers.length} onClick={() => void mutate(encodeURIComponent(removableId), 'DELETE')}>{t('Confirm uninstall')}</button>
        <button className="library-text-button" disabled={busy} onClick={() => setConfirmRemoval(false)}>{t('Cancel')}</button></div>
    </div>}
    {error && <p className="pack-action-error" role="alert">{error}</p>}
    {environmentErrors.length > 0 && <div className="pack-problem">
      {environmentErrors.map(message => <p key={message} role="alert" className="pack-action-error">{message}</p>)}
      <button className="secondary-button" disabled={busy} onClick={() => void mutate(source ? `${encodeURIComponent(source.id)}/restore` : 'environment/retry', 'POST')}>{t('Retry environment preparation')}</button>
      <p>{t('Repair or reinstall this Pack, then restart OAW to retry. Saved cards and connections are retained.')}</p>
    </div>}
    {inventory && <details className="pack-detail-section"><summary>{t('Pack dependencies')}</summary><PackRuntimeDetails inventory={inventory} /></details>}
    {item?.creator && <details className="pack-detail-section"><summary>{t('Pack guide')}</summary><PackGuide creator={item.creator} /></details>}
    {versions.some(row => !row.selected) && <details className="pack-detail-section"><summary>{t('Other versions')}</summary>
      {versions.filter(row => !row.selected).map(row => <div className="pack-version-row" key={row.version}>
        <span>{row.version} · {t(row.loaded ? 'Loaded' : 'Retained version')}</span>
        <button className="library-text-button" disabled={busy} onClick={() => void mutate(`${encodeURIComponent(row.id)}/activate`, 'POST', { version: row.version })}>{t('Use this version on restart')}</button>
        {!row.loaded && <button className="library-text-button" disabled={busy} onClick={() => void mutate(`${encodeURIComponent(row.id)}/versions/${encodeURIComponent(row.version)}`, 'DELETE')}>{t('Remove retained version')}</button>}
      </div>)}
    </details>}
  </section>;
}
