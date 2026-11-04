import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Search } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import type { PackInstallations, StoreDetail, StorePack, StorePage, StoreState, StoreVersion } from '../types/packs';
import { packBlockerMessage, PackPlanDetails } from './PackDependencies';
import { packOperationPending, PackOperations } from './PackOperations';
import { StorePackPreview } from './StorePackPreview';
import './packPresentation.css';
import './packStore.css';

function InstallationStatus({ state }: { state: StoreState }) {
  if (!state.installed_version && !state.update_available) return null;
  return <span className="store-installation" role="status">
    {state.installed_version && <span className="pack-tag">{t('Installed')}{state.restart_required ? ` · ${t('Restart required')}` : ''}</span>}
    {state.update_available && <span className="pack-tag" data-tone="accent">{t('Update available')}</span>}
  </span>;
}

export function PackStore({ installations, onInstalled, readOnly = false }: {
  installations?: PackInstallations; onInstalled?: (state: StoreState) => void; readOnly?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState<StorePage>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string>();
  const [detail, setDetail] = useState<StoreDetail>();
  const [version, setVersion] = useState<StoreVersion>();
  const [versionChoice, setVersionChoice] = useState<string>();
  const [detailError, setDetailError] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);
  const [installError, setInstallError] = useState('');
  const [installing, setInstalling] = useState<string>();
  const catalogRequest = useRef<AbortController>();
  const installRequest = useRef<AbortController>();
  const [installed, setInstalled] = useState<Record<string, StoreState>>({});
  useEffect(() => () => { catalogRequest.current?.abort(); installRequest.current?.abort(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    catalogRequest.current?.abort(); catalogRequest.current = controller;
    setPage(undefined); setError(''); setLoading(true);
    const timer = window.setTimeout(() => {
      void worldApi.getStorePacks(query, undefined, controller.signal).then(result => {
        if (!controller.signal.aborted) setPage(result);
      }).catch(cause => { if (!controller.signal.aborted) setError(apiErrorMessage(cause)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, revision]);

  useEffect(() => {
    setDetail(undefined); setVersion(undefined); setVersionChoice(undefined); setDetailError(''); setInstallError('');
    if (!selected) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const result = await worldApi.getStorePack(selected, controller.signal);
        if (controller.signal.aborted) return;
        setDetail(result);
        const candidate = result.available_version ?? result.latest_version;
        if (candidate) setVersionChoice(candidate);
      } catch (cause) { if (!controller.signal.aborted) setDetailError(apiErrorMessage(cause)); }
    })();
    return () => controller.abort();
  }, [selected, detailRevision]);

  useEffect(() => {
    setVersion(undefined); setInstallError('');
    if (!selected || detail?.id !== selected || !versionChoice) return;
    const controller = new AbortController();
    setDetailError('');
    worldApi.getStoreVersion(selected, versionChoice, controller.signal).then(metadata => {
      if (!controller.signal.aborted) {
        setVersion(metadata);
        setInstalled(current => { const next = { ...current }; delete next[selected]; return next; });
      }
    }).catch(cause => { if (!controller.signal.aborted) setDetailError(apiErrorMessage(cause)); });
    return () => controller.abort();
  }, [selected, detail?.id, versionChoice, detailRevision]);

  const loadMore = async () => {
    if (!page?.next_cursor || loading) return;
    const controller = new AbortController();
    catalogRequest.current?.abort(); catalogRequest.current = controller;
    setLoading(true); setError('');
    try {
      const result = await worldApi.getStorePacks(query, page.next_cursor, controller.signal);
      if (!controller.signal.aborted) setPage(current => ({ ...result,
        items: [...new Map([...(current?.items ?? []), ...result.items].map(item => [item.id, item])).values()] }));
    } catch (cause) { if (!controller.signal.aborted) setError(apiErrorMessage(cause)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  };
  const install = async (pack: StorePack) => {
    if (readOnly || !pack.available_version || installRequest.current) return;
    const controller = new AbortController(); installRequest.current = controller;
    setInstalling(pack.id); setInstallError('');
    try {
      const result = await worldApi.installStorePack(pack.id, pack.available_version, controller.signal);
      if (!controller.signal.aborted) {
        setInstalled(current => ({ ...current, [pack.id]: result }));
        onInstalled?.(result);
      }
    } catch (cause) { if (!controller.signal.aborted) setInstallError(apiErrorMessage(cause)); }
    finally { if (!controller.signal.aborted) { setInstalling(undefined); installRequest.current = undefined; } }
  };
  const merged = (pack: StorePack): StorePack => {
    const recent = installed[pack.id];
    if (!recent) return pack;
    if (recent.available_version === pack.available_version) return { ...pack, ...recent };
    return { ...pack, installed_version: recent.installed_version, loaded_version: recent.loaded_version,
      restart_required: recent.restart_required };
  };
  const action = (pack: StorePack) => {
    if (readOnly) return <p>{t('Pack installation is managed by your hosting administrator.')}</p>;
    const pending = installations?.operations?.find(item => item.pack_id === pack.id && item.version === pack.available_version && packOperationPending(item));
    const selectedVersion = installations?.versions.some(item => item.id === pack.id && item.version === pack.available_version && item.selected)
      || pack.installed_version === pack.available_version && !pack.can_install;
    const blockers = version?.plan?.blockers ?? [];
    const unavailable = !selectedVersion && (!pack.can_install || blockers.length > 0);
    const waiting = !selectedVersion && (Boolean(installing) || Boolean(pending));
    const downloading = installing === pack.id && !pending && !selectedVersion;
    const message = installing && installing !== pack.id ? t('Another Pack is installing. Wait for it to finish.')
      : downloading ? t('Getting the Pack ({size} MB)…', { size: ((version?.size_bytes ?? 0) / 1024 / 1024).toFixed(1) })
      : waiting ? t('Preparing environments')
      : unavailable && !blockers.length ? t('This version cannot be installed on this host.') : '';
    return <>
      <button className="secondary-button" disabled={Boolean(waiting || selectedVersion || unavailable)}
        aria-describedby={blockers.length || message ? 'store-install-reason' : undefined}
        onClick={() => void install(pack)}>{downloading ? t('Getting Pack') : waiting ? t('Installing') : selectedVersion ? t('Installed') : unavailable ? t('Unavailable') : pack.update_available ? t('Update') : t('Get')}</button>
      {(blockers.length > 0 || message) && <div id="store-install-reason">
        {blockers.length > 0 && <ul className="pack-action-error" role="alert">{blockers.map(reason => <li key={reason}>{packBlockerMessage(reason)}</li>)}</ul>}
        {message && <p role="status">{message}</p>}
      </div>}
    </>;
  };
  const shown = detail && merged({ ...detail, ...version });
  const versionOptions = detail ? [...new Set([detail.available_version ?? detail.latest_version, ...detail.versions,
    ...installations?.versions.filter(item => item.id === detail.id).map(item => item.version) ?? []].filter((value): value is string => Boolean(value)))] : [];

  return <section className="pack-store" aria-label={t('Pack Store')}>
    <PackOperations operations={installations?.operations} />
    {selected ? <>
      <button className="library-text-button" onClick={() => setSelected(undefined)}><ArrowLeft size={15} />{t('Back to Store')}</button>
      {shown && <article className="store-detail pack-detail-layout" aria-label={t('Pack details')}>
        <header className="pack-detail-header">
          <div className="pack-detail-preview"><StorePackPreview pack={{ ...shown,
            latest_version: version?.version ?? shown.available_version ?? shown.latest_version,
            preview: version ? version.preview : shown.preview,
          }} label={t('Pack details')} onClick={() => {}} /></div>
          <div className="pack-detail-heading"><h3>{shown.name}</h3>
            <div className="pack-detail-meta"><code>{shown.id}</code></div>
            {shown.summary && <p className="pack-detail-summary">{shown.summary}</p>}
          </div>
        </header>
        <aside className="pack-detail-panel" aria-label={t(readOnly ? 'Pack details' : 'Install Pack')}>
          <div className="pack-detail-panel-heading"><h4>{t(readOnly ? 'Pack details' : 'Install Pack')}</h4><span className="pack-tag">.oawpack</span></div>
          {versionOptions.length > 1 && <label className="field-label"><span>{t('Version')}</span><select aria-label={t('Version')} value={versionChoice ?? ''} onChange={event => { setVersion(undefined); setVersionChoice(event.target.value); }}>
            {versionOptions.map(value => <option key={value} value={value}>v{value}</option>)}
          </select></label>}
          {version && <><dl className="pack-detail-facts">
            {versionOptions.length <= 1 && <><dt>{t('Version')}</dt><dd><span className="pack-tag pack-version-tag">v{version.version}</span></dd></>}
            <dt>{t('OAW compatibility')}</dt><dd><span className="pack-tag">{version.manifest.compatibility.oaw}</span></dd>
          </dl><InstallationStatus state={shown} />{action(shown)}</>}
          {!version && !detailError && shown.latest_version && <p role="status">{t('Loading…')}</p>}
          {!shown.latest_version && <p>{t('No version available')}</p>}
          {installError && <p role="alert" className="pack-action-error">{t(installError)}</p>}
        </aside>
        <div className="pack-detail-body">
          {shown.description && <section className="pack-detail-section"><h4>{t('About this Pack')}</h4><p className="pack-detail-description">{shown.description}</p></section>}
          {version?.plan && <div className="pack-detail-section"><PackPlanDetails plan={version.plan} showBlockers={false} /></div>}
          {version && !version.plan && <div className="pack-detail-requirements">
            <section className="pack-detail-section"><h4>{t('Pack dependencies')}</h4><div className="pack-value-tags">{version.manifest.dependencies.packs.length
              ? version.manifest.dependencies.packs.map(item => <span className="pack-tag" key={item.id}>{item.id} {item.version}</span>) : <span className="pack-empty-value">{t('None')}</span>}</div></section>
            <section className="pack-detail-section"><h4>{t('Sandbox Python requirements')}</h4><div className="pack-value-tags">{version.manifest.runtime.sandbox.python.length
              ? version.manifest.runtime.sandbox.python.map(item => <span className="pack-tag" key={item}>{item}</span>) : <span className="pack-empty-value">{t('None')}</span>}</div></section>
          </div>}
        </div>
      </article>}
      {!detail && !detailError && <p role="status">{t('Loading…')}</p>}
      {detailError && <div className="library-error" role="alert">{t(detailError)}<button className="secondary-button" onClick={() => setDetailRevision(v => v + 1)}>{t('Retry')}</button></div>}
    </> : <>
      <div className="library-section-heading"><div><h3>{t('Store')}</h3><p>{t('Discover new Packs for your world.')}</p></div>
        <label className="library-search"><Search size={15} /><input maxLength={200} aria-label={t('Search packs')} placeholder={t('Search packs')} value={query} onChange={event => setQuery(event.target.value)} /></label>
      </div>
      <div className="store-pack-grid pack-catalog-grid">{page?.items.map(item => {
        const pack = merged(item);
        return <article key={pack.id} className="store-pack" data-store-pack-id={pack.id}>
          <StorePackPreview pack={pack} onClick={() => setSelected(pack.id)} caption={<div className="pack-catalog-copy">
            <h4><button className="pack-catalog-link" onClick={() => setSelected(pack.id)}>{pack.name}</button></h4>
          </div>} />
        </article>;
      })}</div>
      {loading && <p role="status">{t('Loading…')}</p>}
      {error && <div className="library-empty"><strong>{t('Store unavailable')}</strong><p role="alert">{t(error)}</p><button className="secondary-button" onClick={() => page ? void loadMore() : setRevision(v => v + 1)}>{t('Retry')}</button></div>}
      {!loading && !error && page?.items.length === 0 && <p>{t('No matching packs')}</p>}
      {page?.next_cursor && !error && <button className="secondary-button" disabled={loading} onClick={() => void loadMore()}>{t('Load more')}</button>}
    </>}
    {Object.values(installed).some(state => state.restart_required) && <p role="status">{t('Restart OAW to activate Pack changes.')}</p>}
  </section>;
}
