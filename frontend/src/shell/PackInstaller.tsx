import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import type { PackInspection, PackInstallations } from '../types/packs';
import { PackGuide } from './PackCreator';
import { PackPlanDetails } from './PackDependencies';
import { packOperationPending, PackOperationStatus } from './PackOperations';

export function PackInstaller({ actionTarget, onInstalled, installations }: {
  actionTarget: HTMLElement | null; onInstalled: (value: PackInstallations) => void;
  installations?: PackInstallations;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [inspection, setInspection] = useState<PackInspection>();
  const [phase, setPhase] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const operation = installations?.operations?.find(item => item.digest === inspection?.sha256);
  const pending = Boolean(operation && packOperationPending(operation));
  const installed = Boolean(inspection && installations?.versions.some(item => item.id === inspection.manifest.id
    && item.version === inspection.manifest.version && item.selected));
  useEffect(() => () => { generation.current++; }, []);
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
      setInspection(result); setFile(selected); setPhase('');
    } catch (cause) { if (current === generation.current) { setError(apiErrorMessage(cause)); setPhase('Failed'); } }
    finally { if (current === generation.current) setBusy(false); }
  };
  const mutate = async (operation: () => Promise<PackInstallations>, label = 'Installing') => {
    const current = ++generation.current;
    setBusy(true); setError(''); setPhase(label);
    try {
      const result = await operation();
      if (current !== generation.current) return;
      setInspection(undefined); setFile(undefined); setPhase(''); onInstalled(result);
    }
    catch (cause) { if (current === generation.current) { setError(apiErrorMessage(cause)); setPhase(''); } }
    finally { if (current === generation.current) setBusy(false); }
  };
  const dismiss = () => { setInspection(undefined); setFile(undefined); setPhase(''); setError(''); };
  return <>
    <input ref={input} type="file" accept=".oawpack" hidden aria-label={t('Pack file')} onChange={event => {
      void inspect(event.target.files?.[0]); event.target.value = '';
    }} />
    {actionTarget && createPortal(<>
      <button className="secondary-button" disabled={busy} onClick={() => input.current?.click()}>{t('Install Pack from File...')}</button>
      {(phase || inspection || error) && <section className="pack-install-popover" aria-label={t('Install Pack')} aria-busy={busy}>
        <header><strong>{inspection?.manifest.name ?? t('Install Pack')}</strong>
          {inspection && <span>{inspection.manifest.version}</span>}</header>
        {phase && !error && !pending && <p role="status">{t(phase)}</p>}
        {operation && (pending || installed || operation.state === 'failed') && <PackOperationStatus operation={operation} />}
        {inspection && file && <>
          <p>{t(inspection.manifest.kind === 'content'
            ? 'Adds reusable cards or Legion templates.'
            : 'Runs application code. Install only from a trusted source.')}</p>
          {inspection.manifest.creator && <details><summary>{t('Pack guide')}</summary><PackGuide creator={inspection.manifest.creator} /></details>}
          {inspection.plan && <PackPlanDetails plan={inspection.plan} />}
        </>}
        {error && <p role="alert" className="pack-action-error">{error}</p>}
        <div className="pack-detail-actions">
          {inspection && file && <button className="secondary-button" disabled={busy || pending || installed || Boolean(inspection.plan?.blockers.length)} onClick={() => void mutate(() => worldApi.installPack(file))}>{t(installed ? 'Installed' : busy || pending ? 'Installing' : 'Install Pack')}</button>}
          <button className="library-text-button" disabled={busy} onClick={dismiss}>{t(inspection ? 'Cancel' : 'Close')}</button>
        </div>
      </section>}
    </>, actionTarget)}
  </>;
}
