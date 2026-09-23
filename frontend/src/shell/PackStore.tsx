import { useEffect, useRef, useState } from 'react';
import { Archive, ArrowLeft, Search } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import type { StoreDetail, StorePack, StorePage, StoreState, StoreVersion } from '../types/packs';
import './packStore.css';

function InstallationStatus({ state }: { state: StoreState }) {
  return <span className="store-installation" role="status">
    {state.installed_version && <span>{t('Installed')}{state.restart_required ? ` · ${t('Restart required')}` : ''}</span>}
    {state.update_available && <span>{t('Update available')}</span>}
  </span>;
}

export function PackStore() {
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
        if (result.latest_version) {
          const metadata = await worldApi.getStoreVersion(selected, result.latest_version, controller.signal);
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
  const action = (pack: StorePack) => <button className="secondary-button" disabled={Boolean(installing) || !pack.can_install}
    onClick={() => void install(pack)}>{installing === pack.id ? t('Installing') : !pack.can_install && pack.installed_version ? t('Installed') : pack.update_available ? t('Update') : t('Get')}</button>;
  const shown = detail && merged(detail);

  return <section className="pack-store" aria-label={t('Pack Store')}>
    {selected ? <>
      <button className="library-text-button" onClick={() => setSelected(undefined)}><ArrowLeft size={15} />{t('Back to Store')}</button>
      {shown && <article className="store-detail" aria-label={t('Pack details')}>
        <div className="store-pack-emblem"><Archive size={32} /></div>
        <h3>{shown.name}</h3><p>{shown.summary}</p>
        <p className="store-description">{shown.description}</p>
        <InstallationStatus state={shown} />
        {version && <><dl className="store-requirements">
          <dt>{t('Version')}</dt><dd>{version.version}</dd>
          <dt>{t('OAW compatibility')}</dt><dd>{version.manifest.compatibility.oaw}</dd>
          <dt>{t('Pack dependencies')}</dt><dd>{version.manifest.dependencies.packs.length
            ? version.manifest.dependencies.packs.map(item => <div key={item.id}>{item.id} {item.version}</div>) : t('None')}</dd>
          <dt>{t('Sandbox Python requirements')}</dt><dd>{version.manifest.runtime.sandbox.python.length
            ? version.manifest.runtime.sandbox.python.map(item => <div key={item}>{item}</div>) : t('None')}</dd>
        </dl>{action(shown)}</>}
        {!version && !detailError && shown.latest_version && <p role="status">{t('Loading…')}</p>}
        {!shown.latest_version && <p>{t('No version available')}</p>}
      </article>}
      {!detail && !detailError && <p role="status">{t('Loading…')}</p>}
      {detailError && <div className="library-error" role="alert">{t(detailError)}<button className="secondary-button" onClick={() => setDetailRevision(v => v + 1)}>{t('Retry')}</button></div>}
    </> : <>
      <div className="library-section-heading"><div><h3>{t('Store')}</h3><p>{t('Discover new Packs for your world.')}</p></div>
        <label className="library-search"><Search size={15} /><input maxLength={200} aria-label={t('Search packs')} placeholder={t('Search packs')} value={query} onChange={event => setQuery(event.target.value)} /></label>
      </div>
      <div className="store-pack-grid">{page?.items.map(item => {
        const pack = merged(item);
        return <article key={pack.id} className="store-pack" data-store-pack-id={pack.id}>
          <button className="store-pack-open" aria-label={t('View {v0} details', { v0: pack.name })} onClick={() => setSelected(pack.id)}>
            <div className="store-pack-emblem"><Archive size={28} /></div><h4>{pack.name}</h4><p>{pack.summary}</p>
          </button><div className="store-pack-footer"><InstallationStatus state={pack} />{action(pack)}</div>
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
