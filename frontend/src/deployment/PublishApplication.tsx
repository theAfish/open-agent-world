import { useEffect, useState } from 'react';
import { Copy, LockKeyhole, X } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { deploymentRequest } from './api';
import { paneViews, readWorkspaceLayout } from '../legions/workspaceLayout';
import type { WorldCard } from '../types/world';
import './deployment.css';

interface Release { id: string; name: string; legion_id: string; source_path: string; created_at: string }
const quote = (value: string) => /^[A-Za-z]:/.test(value) ? `'${value.replaceAll("'", "''")}'` : `'${value.replaceAll("'", "'\"'\"'")}'`;

export function PublishApplication({ card, disabled }: { card: WorldCard; disabled: boolean }) {
  useLocale();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(card.name);
  const [terminal, setTerminal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [release, setRelease] = useState<Release>();
  const [history, setHistory] = useState<Release[]>([]);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (open) void deploymentRequest<Release[]>('/deployments').then(items => setHistory(items.filter(item => item.legion_id === card.id).reverse())).catch(reason => setError(reason.message));
  }, [open, card.id]);
  const command = release ? `${/^[A-Za-z]:/.test(release.source_path) ? 'python' : 'python3'} scripts/deploy.py --source ${quote(release.source_path)} --release ${release.id} --open` : '';
  const publish = async () => {
    setBusy(true); setError('');
    try {
      const result = await deploymentRequest<Release>('/deployments', { method: 'POST', body: JSON.stringify({ legion_id: card.id, name, allow_terminal: terminal }) });
      setRelease(result); setHistory(items => [result, ...items]); setCopied(false);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  return <>
    <button className="secondary-button" disabled={disabled} onClick={() => { setOpen(true); setError(''); }}><LockKeyhole size={13} />{t('Publish application')}</button>
    {open && <div className="publish-overlay" onClick={event => event.stopPropagation()} onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape' && !busy) { event.preventDefault(); setOpen(false); } }}>
      <section className="publish-dialog" role="dialog" aria-modal="true" aria-label={t('Publish application')}>
        <header><h2>{t('Publish application')}</h2><button aria-label={t('Close')} disabled={busy} onClick={() => setOpen(false)}><X size={18} /></button></header>
        <p>{t('Turn this saved workspace into a locked application. Only placed pages are published; other cards keep working in the background.')}</p>
        <p className="publish-hint">{t('Existing conversations and files are included. Users with the password share this application’s data.')}</p>
        <label>{t('Application name')}<input value={name} onChange={event => setName(event.target.value)} maxLength={100} autoFocus /></label>
        <p>{t('Published pages')}: {paneViews(readWorkspaceLayout(card.config.workspace_layout).root).length}</p>
        <label className="publish-checkbox"><input type="checkbox" checked={terminal} onChange={event => setTerminal(event.target.checked)} />{t('Allow users to run Sandbox terminal commands')}</label>
        <p className="publish-hint">{t('Conversations, files, text, images, task boards and agent status have public pages. Unsupported plugin pages must be removed from the layout before publication.')}</p>
        {error && <p className="deployment-error" role="alert">{error}</p>}
        <button className="primary-button" disabled={busy || !name.trim()} onClick={() => { void publish(); }}>{busy ? t('Publishing…') : t('Create release')}</button>
        {!!history.length && <label>{t('Published versions')}<select value={release?.id ?? ''} onChange={event => { setRelease(history.find(item => item.id === event.target.value)); setCopied(false); }}><option value="" disabled>{t('Select a release')}</option>{history.map(item => <option key={item.id} value={item.id}>{item.name} · {new Date(item.created_at).toLocaleString()} · {item.id.slice(0, 8)}</option>)}</select></label>}
        {release && <div className="publish-next"><h3>{t('Deploy this release')}</h3>
          <ol><li>{t('Copy the command below, then close the engineering application to release its data directory.')}</li><li>{t('Run it from the repository root. It creates an independent data copy and prints an access password.')}</li><li>{t('Open the printed runtime address and give users the access password.')}</li></ol>
          <pre>{command}</pre><button onClick={() => { void navigator.clipboard.writeText(command).then(() => setCopied(true)).catch(() => setError(t('Copy the command manually from the box above.'))); }}><Copy size={14} />{copied ? t('Copied') : t('Copy command')}</button>
          <p>{t('See docs/deployment.zh-CN.md for server hosting, restarts, updates and rollback. Publishing another version does not change a running deployment.')}</p>
        </div>}
      </section>
    </div>}
  </>;
}
