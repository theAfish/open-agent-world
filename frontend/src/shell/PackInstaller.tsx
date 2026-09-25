import { useEffect, useRef, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import type { PackInspection, PackInstallations } from '../types/packs';

export function PackInstaller() {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [inspection, setInspection] = useState<PackInspection>();
  const [state, setState] = useState<PackInstallations>();
  const [phase, setPhase] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const current = generation.current;
      try { const value = await worldApi.getInstalledPacks(); if (active && current === generation.current) setState(value); }
      catch (cause) { if (active) setError(apiErrorMessage(cause)); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => { active = false; generation.current++; window.clearInterval(timer); };
  }, []);
  const inspect = async (selected?: File) => {
    if (!selected) return;
    const current = ++generation.current;
    setBusy(true); setError(''); setInspection(undefined); setFile(undefined); setPhase('Inspecting');
    try {
      if (!selected.name.toLowerCase().endsWith('.oawpack')) throw new Error('Choose a .oawpack file');
      if (selected.size > 128 * 1024 * 1024) throw new Error('Pack exceeds the 128 MiB upload limit');
      const header = new Uint8Array(await selected.slice(0, 4).arrayBuffer());
      if (header[0] !== 0x50 || header[1] !== 0x4b || header[2] !== 3 || header[3] !== 4) throw new Error('Choose a valid .oawpack ZIP file');
      if (current !== generation.current) return;
      setPhase('Validating');
      const result = await worldApi.inspectPack(selected);
      if (current !== generation.current) return;
      setInspection(result); setFile(selected); setPhase('Ready to install');
    } catch (cause) { if (current === generation.current) { setError(apiErrorMessage(cause)); setPhase('Failed'); } }
    finally { if (current === generation.current) setBusy(false); }
  };
  const mutate = async (operation: () => Promise<PackInstallations>, label = 'Installing') => {
    const current = ++generation.current;
    setBusy(true); setError(''); setPhase(label);
    try {
      const result = await operation();
      if (current !== generation.current) return;
      generation.current++;
      setState(result); setInspection(undefined); setFile(undefined); setPhase('');
    }
    catch (cause) { setError(apiErrorMessage(cause)); setPhase('Failed'); }
    finally { setBusy(false); }
  };
  return <section className="pack-installer" aria-label={t('Local Pack management')}>
    <input ref={input} type="file" accept=".oawpack" hidden aria-label={t('Pack file')} onChange={event => {
      void inspect(event.target.files?.[0]); event.target.value = '';
    }} />
    <button className="secondary-button" disabled={busy} onClick={() => input.current?.click()}>{t('Install Pack from File...')}</button>
    {phase && <p role="status">{t(phase)}</p>}
    {inspection && file && <div className="pack-install-review">
      <strong>{inspection.manifest.name} {inspection.manifest.version}</strong>
      <p>{t('Packs run trusted application code. Install only files from a source you trust.')}</p>
      <button className="secondary-button" disabled={busy} onClick={() => void mutate(() => worldApi.installPack(file))}>{t('Install Pack')}</button>
      <button className="library-text-button" disabled={busy} onClick={() => { setInspection(undefined); setFile(undefined); setPhase(''); }}>{t('Cancel')}</button>
    </div>}
    {error && <p role="alert" className="library-error">{error}</p>}
    {state?.restart_required && <p role="status">{t('Restart OAW to activate Pack changes.')}</p>}
    {state?.versions.length ? <details><summary>{t('Manage installed Packs')}</summary>
      {state.versions.map(item => <div className="pack-install-record" key={`${item.id}@${item.version}`}>
        <strong>{item.name} {item.version}</strong>
        <span>{t('Pack')}: {t('Installed')}{item.selected && !item.loaded ? ` · ${t('Restart required')}` : !item.selected ? ` · ${t('Retained version')}` : ''}</span>
        {item.loaded && <span>{t('Sandbox runtime')}: {t(item.environment?.state === 'environment_ready' ? 'Ready' : item.environment?.state === 'environment_failed' ? 'Failed' : 'Environment preparing')}</span>}
        {item.environment?.error && <p role="alert">{item.environment.error}</p>}
        <div>
          {item.environment?.state === 'environment_failed' && <button className="library-text-button" disabled={busy} onClick={() => void mutate(() => worldApi.managePack('environment/retry', 'POST'), 'Environment preparing')}>{t('Retry environment preparation')}</button>}
          {!item.selected && <button className="library-text-button" disabled={busy} onClick={() => void mutate(() => worldApi.managePack(`${encodeURIComponent(item.id)}/activate`, 'POST', { version: item.version }), 'Validating')}>{t('Use this version on restart')}</button>}
          {item.selected && <button className="library-text-button" disabled={busy} onClick={() => void mutate(() => worldApi.managePack(encodeURIComponent(item.id), 'DELETE'), 'Validating')}>{t('Uninstall on restart')}</button>}
          {!item.selected && !item.loaded && <button className="library-text-button" disabled={busy} onClick={() => void mutate(() => worldApi.managePack(`${encodeURIComponent(item.id)}/versions/${encodeURIComponent(item.version)}`, 'DELETE'), 'Validating')}>{t('Remove retained version')}</button>}
        </div>
      </div>)}
    </details> : null}
  </section>;
}
