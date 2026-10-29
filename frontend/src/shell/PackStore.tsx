import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Search } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import type { PackInstallations, StoreDetail, StorePack, StorePage, StoreState, StoreVersion } from '../types/packs';
import { PackPlanDetails } from './PackDependencies';
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

export function PackStore({ installations }: { installations?: PackInstallations }) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState<StorePage>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string>();
  const [detail, setDetail] = useState<StoreDetail>();
  const [version, setVersion] = useState<StoreVersion>();
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
    setDetail(undefined); setVersion(undefined); setDetailError(''); setInstallError('');
    if (!selected) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const result = await worldApi.getStorePack(selected, controller.signal);
        if (controller.signal.aborted) return;
        setDetail(result);
        const candidate = result.available_version ?? result.latest_version;
        if (candidate) {
          const metadata = await worldApi.getStoreVersion(selected, candidate, controller.signal);
          if (!controller.signal.aborted) setVersion(metadata);
        }
      } catch (cause) { if (!controller.signal.aborted) setDetailError(apiErrorMessage(cause)); }
    })();
    return () => controller.abort();
  }, [selected, detailRevision]);

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
    if (!pack.available_version || installRequest.current) return;
    const controller = new AbortController(); installRequest.current = controller;
    setInstalling(pack.id); setInstallError('');
    try {
      const result = await worldApi.installStorePack(pack.id, pack.available_version, controller.signal);
      if (!controller.signal.aborted) setInstalled(current => ({ ...current, [pack.id]: result }));
    } catch (cause) { if (!controller.signal.aborted) setInstallError(apiErrorMessage(cause)); }
    finally { if (!controller.signal.aborted) { setInstalling(undefined); installRequest.current = undefined; } }
  };
  const merged = (pack: StorePack): StorePack => ({ ...pack, ...installed[pack.id] });
  const action = (pack: StorePack) => {
    const pending = installations?.operations?.some(item => item.pack_id === pack.id && packOperationPending(item));
    const selectedVersion = installations?.versions.some(item => item.id === pack.id && item.version === pack.available_version && item.selected);
    return <button className="secondary-button" disabled={Boolean(installing) || pending || selectedVersion || !pack.can_install || Boolean(version?.plan?.blockers.length)}
      onClick={() => void install(pack)}>{installing === pack.id || pending ? t('Installing') : selectedVersion || !pack.can_install && pack.installed_version ? t('Installed') : pack.update_available ? t('Update') : t('Get')}</button>;
  };
  const shown = detail && merged(detail);

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
        <aside className="pack-detail-panel" aria-label={t('Install Pack')}>
          <div className="pack-detail-panel-heading"><h4>{t('Install Pack')}</h4><span className="pack-tag">.oawpack</span></div>
          {version && <><dl className="pack-detail-facts">
            <dt>{t('Version')}</dt><dd><span className="pack-tag pack-version-tag">v{version.version}</span></dd>
            <dt>{t('OAW compatibility')}</dt><dd><span className="pack-tag">{version.manifest.compatibility.oaw}</span></dd>
          </dl><InstallationStatus state={shown} />{action(shown)}</>}
          {!version && !detailError && shown.latest_version && <p role="status">{t('Loading…')}</p>}
          {!shown.latest_version && <p>{t('No version available')}</p>}
        </aside>
        <div className="pack-detail-body">
          {shown.description && <section className="pack-detail-section"><h4>{t('About this Pack')}</h4><p className="pack-detail-description">{shown.description}</p></section>}
          {version?.plan && <div className="pack-detail-section"><PackPlanDetails plan={version.plan} /></div>}
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
    {installError && <p role="alert" className="library-error">{t(installError)}</p>}
    {Object.values(installed).some(state => state.restart_required) && <p role="status">{t('Restart OAW to activate Pack changes.')}</p>}
  </section>;
}
