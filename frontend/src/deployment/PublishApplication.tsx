import { useEffect, useState } from 'react';
import { Copy, LockKeyhole, X } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { deploymentRequest } from './api';
import { paneViews, readWorkspaceLayout } from '../legions/workspaceLayout';
import type { WorldCard } from '../types/world';
import './deployment.css';
import { isHosted } from '../auth/hostedSession';

interface Release { id: string; name: string; legion_id: string; source_path: string; created_at: string }
interface Readiness {
  ready: boolean;
  panels: { card_id: string; name: string; section_id?: string }[];
  issues: { card_id?: string; section_id?: string; message: string }[];
}
const quote = (value: string) => /^[A-Za-z]:/.test(value) ? `'${value.replaceAll("'", "''")}'` : `'${value.replaceAll("'", "'\"'\"'")}'`;

export function PublishApplication({ card, disabled }: { card: WorldCard; disabled: boolean }) {
  const { locale } = useLocale();
  const hosted = isHosted();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(card.name);
  const [terminal, setTerminal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [release, setRelease] = useState<Release>();
  const [history, setHistory] = useState<Release[]>([]);
  const [copied, setCopied] = useState(false);
  const [readiness, setReadiness] = useState<Readiness>();
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    if (open) void deploymentRequest<Release[]>('/deployments').then(items => setHistory(items.filter(item => item.legion_id === card.id).reverse())).catch(reason => setError(reason.message));
  }, [open, card.id]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setReadiness(undefined); setChecking(true);
    void deploymentRequest<Readiness>('/deployments/preview', {
      method: 'POST', signal: controller.signal,
      body: JSON.stringify({ legion_id: card.id, name: card.name, allow_terminal: terminal }),
    }).then(value => { if (!controller.signal.aborted) setReadiness(value); }).catch(reason => {
      if (!controller.signal.aborted) setError(reason.message);
    }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, [open, card.id, card.name, card.config.workspace_layout, terminal]);
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
        <p className="publish-hint">{hosted ? copy('An administrator deploys a separate private workspace for each account. Conversation history and credentials are not copied.', '管理员为每个账号部署独立工作区，不复制工程账号的对话历史和密钥。') : t('Existing conversations and files are included. Users with the password share this application’s data.')}</p>
        <label>{t('Application name')}<input value={name} onChange={event => setName(event.target.value)} maxLength={100} autoFocus /></label>
        <p>{t('Published pages')}: {paneViews(readWorkspaceLayout(card.config.workspace_layout).root).length}</p>
        <label className="publish-checkbox"><input type="checkbox" checked={terminal} onChange={event => setTerminal(event.target.checked)} />{t('Allow users to run Sandbox terminal commands')}</label>
        <p className="publish-hint">{t('Published pages reuse their existing views and business APIs. Configuration and management access remain locked.')}</p>
        <div className="publish-readiness" aria-live="polite">
          {checking ? <p role="status">{t('Checking workspace…')}</p> : readiness && <>
            <p>{t(readiness.ready ? 'Workspace is ready to publish' : 'Resolve these pages before publishing')}</p>
            {readiness.issues.length > 0 && <ul>{readiness.issues.map((issue, index) => <li key={`${issue.card_id}:${issue.section_id}:${index}`}>{issue.message}</li>)}</ul>}
            <ul>{readiness.panels.map(panel => <li key={`${panel.card_id}:${panel.section_id}`}>{panel.name}{panel.section_id ? ` · ${panel.section_id}` : ''}</li>)}</ul>
          </>}
        </div>
        {error && <p className="deployment-error" role="alert">{error}</p>}
        <button className="primary-button" disabled={busy || checking || !readiness?.ready || !name.trim()} onClick={() => { void publish(); }}>{busy ? t('Publishing…') : t('Create release')}</button>
        {!!history.length && <label>{t('Published versions')}<select value={release?.id ?? ''} onChange={event => { setRelease(history.find(item => item.id === event.target.value)); setCopied(false); }}><option value="" disabled>{t('Select a release')}</option>{history.map(item => <option key={item.id} value={item.id}>{item.name} · {new Date(item.created_at).toLocaleString()} · {item.id.slice(0, 8)}</option>)}</select></label>}
        {release && <div className="publish-next"><h3>{t('Deploy this release')}</h3>
          {hosted ? <><p>{copy('Open Administration → Build & deploy to select this release, its target accounts and their interface.', '进入管理后台 → 搭建与部署，选择此版本、使用账号和用户界面。')}</p><a href="/admin">{copy('Open administration', '前往管理后台')}</a></> : <>
          <ol><li>{t('Copy the command below, then close the engineering application to release its data directory.')}</li><li>{t('Run it from the repository root. It creates an independent data copy and prints an access password.')}</li><li>{t('Open the printed runtime address and give users the access password.')}</li></ol>
          <pre>{command}</pre><button onClick={() => { void navigator.clipboard.writeText(command).then(() => setCopied(true)).catch(() => setError(t('Copy the command manually from the box above.'))); }}><Copy size={14} />{copied ? t('Copied') : t('Copy command')}</button>
          <p>{t('See docs/deployment.zh-CN.md for server hosting, restarts, updates and rollback. Publishing another version does not change a running deployment.')}</p>
          </>}
        </div>}
      </section>
    </div>}
  </>;
}
