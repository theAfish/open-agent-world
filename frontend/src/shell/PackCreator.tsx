import { useState } from 'react';
import { Download, PackagePlus } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t } from '../i18n';
import type { LegionSummary } from '../types/world';
import type { CreatorInspection, CreatorMetadata, CreatorRequest } from '../types/packs';
import './packCreator.css';

export function PackGuide({ creator }: { creator?: CreatorMetadata | null }) {
  if (!creator) return null;
  return <div className="pack-guide">
    {creator.description && <p>{creator.description}</p>}
    {creator.author && <p>{t('Author (self-declared)')}: {creator.author}</p>}
    {([['Preparation', creator.preparation], ['Example task', creator.example], ['Expected result', creator.expected_result]] as const)
      .filter(([, value]) => value).map(([label, value]) => <div key={label}><strong>{t(label)}</strong><p>{value}</p></div>)}
  </div>;
}

export function PackCreator({ legion }: { legion: LegionSummary }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<CreatorRequest>(() => ({ legion_id: legion.id,
    id: `local.legion${legion.id.replace(/[^a-z0-9]/gi, '').toLowerCase()}`, name: legion.name, version: '0.1.0',
    creator: { description: legion.description ?? '', author: '', preparation: '', example: '', expected_result: '', accent_color: '#617b72' },
    include_state_nodes: [] }));
  const [review, setReview] = useState<{ key: string; result: CreatorInspection }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [downloaded, setDownloaded] = useState(false);
  const key = JSON.stringify(draft);
  const current = review?.key === key;
  const change = (patch: Partial<CreatorRequest>) => { setDraft(value => ({ ...value, ...patch })); setDownloaded(false); };
  const metadata = (field: keyof CreatorMetadata, value: string) => change({ creator: { ...draft.creator, [field]: value } });
  const inspect = async () => {
    setBusy(true); setError('');
    try { setReview({ key, result: await worldApi.inspectContentPack(draft) }); }
    catch (cause) { setError(apiErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  const download = async () => {
    setBusy(true); setError('');
    try {
      const blob = await worldApi.exportContentPack(draft);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = `${draft.id}-${draft.version}.oawpack`;
      link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); setDownloaded(true);
    } catch (cause) { setError(apiErrorMessage(cause)); }
    finally { setBusy(false); }
  };
  return <section className="pack-creator">
    <button className="secondary-button" aria-expanded={open} onClick={() => setOpen(!open)}><PackagePlus size={15} />{t('Make a Pack…')}</button>
    {open && <form onSubmit={event => { event.preventDefault(); void inspect(); }}>
      <p>{t('Share this saved Legion as a content Pack.')}</p>
      <fieldset disabled={busy}>
        <label>{t('Pack name')}<input required maxLength={120} value={draft.name} onChange={event => change({ name: event.target.value })} /></label>
        <div className="pack-creator-row"><label>{t('Version')}<input required maxLength={64} value={draft.version} onChange={event => change({ version: event.target.value })} /></label>
          <label>{t('Accent color')}<input type="color" value={draft.creator.accent_color} onChange={event => metadata('accent_color', event.target.value)} /></label></div>
        <label>{t('Author (self-declared)')}<input maxLength={120} value={draft.creator.author} onChange={event => metadata('author', event.target.value)} /></label>
        <label>{t('Description')}<textarea maxLength={500} rows={2} value={draft.creator.description} onChange={event => metadata('description', event.target.value)} /></label>
        <details><summary>{t('First-use guide')}</summary>
          {([['preparation', 'Preparation'], ['example', 'Example task'], ['expected_result', 'Expected result']] as const).map(([field, label]) =>
            <label key={field}>{t(label)}<textarea rows={2} maxLength={2000} value={draft.creator[field]} onChange={event => metadata(field, event.target.value)} /></label>)}
        </details>
        <details><summary>{t('Pack identity')}</summary>
          <label>{t('Pack ID')}<input required maxLength={120} pattern="[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+" value={draft.id} onChange={event => change({ id: event.target.value })} /></label>
          <p>{t('Keep this ID for updates; increase the version for each release.')}</p>
        </details>
        {review?.result.nodes.some(node => node.has_state) && <div className="pack-creator-content"><strong>{t('Include initial content')}</strong>
          <p>{t('Only select documents and resources you intend to share.')}</p>
          {review.result.nodes.filter(node => node.has_state).map(node => <label key={node.key} className="pack-creator-check">
            <input type="checkbox" checked={draft.include_state_nodes.includes(node.key)} onChange={event => change({ include_state_nodes: event.target.checked
              ? [...draft.include_state_nodes, node.key] : draft.include_state_nodes.filter(id => id !== node.key) })} />{node.name}
          </label>)}
        </div>}
        <button className="secondary-button" type="submit">{t(busy ? 'Checking…' : 'Check Pack')}</button>
      </fieldset>
      {current && review && <div className="pack-creator-review" aria-live="polite">
        <strong>{t('Required packs')}</strong><ul>{review.result.manifest.dependencies?.packs.map(dep => <li key={dep.id}><code>{dep.id}</code> {dep.version}</li>)}</ul>
        {review.result.issues.map((issue, index) => <p key={index} className={issue.severity === 'error' ? 'library-error' : ''}><strong>{issue.path}</strong> · {t(issue.message)}</p>)}
        <p>{t('Review your instructions and selected content for private information before sharing.')}</p>
        <button className="secondary-button" type="button" disabled={busy || !review.result.can_export} onClick={() => void download()}><Download size={14} />{t('Export .oawpack')}</button>
      </div>}
      {error && <p className="library-error" role="alert">{error}</p>}
      {downloaded && <p role="status">{t('Pack exported. Share the file; recipients can install it from the Library.')}</p>}
    </form>}
  </section>;
}
