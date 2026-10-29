import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Copy, LockKeyhole, X } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { deploymentRequest } from './api';
import { paneViews, readWorkspaceLayout } from '../legions/workspaceLayout';
import type { WorldCard } from '../types/world';
import { useWorldStore } from '../state/worldStore';
import './deployment.css';
import { isHosted } from '../auth/hostedSession';

interface Release { id: string; name: string; legion_id: string; workspaces?: { legion_id: string }[]; source_path: string; created_at: string }
interface Readiness {
  ready: boolean;
  panels: { card_id: string; name: string; section_id?: string }[];
  issues: { card_id?: string; section_id?: string; message: string }[];
  workspaces?: { legion_id: string; name?: string; panels: Readiness['panels']; issues: Readiness['issues'] }[];
}
const quote = (value: string) => /^[A-Za-z]:/.test(value) ? `'${value.replaceAll("'", "''")}'` : `'${value.replaceAll("'", "'\"'\"'")}'`;

export function PublishApplication({ card, disabled }: { card: WorldCard; disabled: boolean }) {
  const { locale } = useLocale();
  const hosted = isHosted();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const [open, setOpen] = useState(false);
  const cards = useWorldStore(state => state.cards);
  const legions = [card, ...cards.filter(item => item.type === 'legion' && item.id !== card.id)];
  const [selectedIds, setSelectedIds] = useState([card.id]);
  const ordered = [...selectedIds.map(id => legions.find(item => item.id === id)).filter((item): item is WorldCard => !!item),
    ...legions.filter(item => !selectedIds.includes(item.id))];
  const selected = ordered.filter(item => selectedIds.includes(item.id));
  const [name, setName] = useState(card.name);
  const [terminal, setTerminal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [release, setRelease] = useState<Release>();
  const [history, setHistory] = useState<Release[]>([]);
  const [copied, setCopied] = useState(false);
  const [readiness, setReadiness] = useState<Readiness>();
  const [checking, setChecking] = useState(false);
  const [checkedKey, setCheckedKey] = useState('');
  const previewKey = JSON.stringify([selectedIds, terminal, selected.map(item => item.config.workspace_layout)]);
  const ready = readiness?.ready && checkedKey === previewKey;
  useEffect(() => {
    if (open) void deploymentRequest<Release[]>('/deployments').then(items => setHistory(items.filter(item =>
      (item.workspaces ?? [{ legion_id: item.legion_id }]).some(workspace => workspace.legion_id === card.id)).reverse())).catch(reason => setError(reason.message));
  }, [open, card.id]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setReadiness(undefined); setError('');
    if (!selectedIds.length) { setChecking(false); return; }
    setChecking(true);
    void deploymentRequest<Readiness>('/deployments/preview', {
      method: 'POST', signal: controller.signal,
      body: JSON.stringify({ legion_ids: selectedIds, name: card.name, allow_terminal: terminal }),
    }).then(value => { if (!controller.signal.aborted) { setReadiness(value); setCheckedKey(previewKey); } }).catch(reason => {
      if (!controller.signal.aborted) setError(reason.message);
    }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, [open, card.name, previewKey]);
  const command = release ? `${/^[A-Za-z]:/.test(release.source_path) ? 'python' : 'python3'} scripts/deploy.py --source ${quote(release.source_path)} --release ${release.id} --open` : '';
  const publish = async () => {
    if (!ready || !selectedIds.length) return;
    setBusy(true); setError('');
    try {
      const result = await deploymentRequest<Release>('/deployments', { method: 'POST', body: JSON.stringify({ legion_ids: selectedIds, name, allow_terminal: terminal }) });
      setRelease(result); setHistory(items => [result, ...items]); setCopied(false);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const move = (id: string, offset: number) => {
    const next = [...selectedIds];
    const index = next.indexOf(id);
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    setSelectedIds(next);
  };
  return <>
    <button className="secondary-button" disabled={disabled} onClick={() => { setSelectedIds([card.id]); setOpen(true); setError(''); }}><LockKeyhole size={13} />{t('Publish application')}</button>
    {open && <div className="publish-overlay" onClick={event => event.stopPropagation()} onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape' && !busy) { event.preventDefault(); setOpen(false); } }}>
      <section className="publish-dialog" role="dialog" aria-modal="true" aria-label={t('Publish application')}>
        <header><h2>{t('Publish application')}</h2><button aria-label={t('Close')} disabled={busy} onClick={() => setOpen(false)}><X size={18} /></button></header>
        <p>{t('Publish selected workspaces as one locked application. Only placed pages are published; other cards keep working in the background.')}</p>
        <p className="publish-hint">{hosted ? copy('An administrator deploys a separate private workspace for each account. Conversation history and credentials are not copied.', '管理员为每个账号部署独立工作区，不复制工程账号的对话历史和密钥。') : t('Existing conversations and files are included. Users with the password share this application’s data.')}</p>
        <label>{t('Application name')}<input value={name} onChange={event => setName(event.target.value)} maxLength={100} autoFocus disabled={busy} /></label>
        <fieldset className="publish-workspaces" disabled={busy}>
          <legend>{t('Workspaces')}</legend>
          <p className="publish-hint">{t('Choose workspaces and arrange their menu order. The first opens by default.')}</p>
          <div className="publish-workspace-list">{ordered.map(workspace => {
            const index = selectedIds.indexOf(workspace.id);
            return <div key={workspace.id} className={`publish-workspace-row${index >= 0 ? ' is-selected' : ''}`}>
              <label><input type="checkbox" checked={index >= 0} onChange={() => setSelectedIds(ids => index >= 0 ? ids.filter(id => id !== workspace.id) : [...ids, workspace.id])} />
                <span>{workspace.name}</span>{index === 0 && <small>{t('Default')}</small>}</label>
              {index >= 0 && <div className="publish-workspace-order">
                <button aria-label={t('Move {v0} up', { v0: workspace.name })} disabled={index === 0} onClick={() => move(workspace.id, -1)}><ArrowUp size={13} /></button>
                <button aria-label={t('Move {v0} down', { v0: workspace.name })} disabled={index === selectedIds.length - 1} onClick={() => move(workspace.id, 1)}><ArrowDown size={13} /></button>
              </div>}
            </div>;
          })}</div>
          {!selectedIds.length && <p className="publish-hint">{t('Select at least one workspace.')}</p>}
        </fieldset>
        <p>{t('Published pages')}: {selected.reduce((count, item) => count + paneViews(readWorkspaceLayout(item.config.workspace_layout).root).length, 0)}</p>
        <label className="publish-checkbox"><input type="checkbox" disabled={busy} checked={terminal} onChange={event => setTerminal(event.target.checked)} />{t('Allow users to run Sandbox terminal commands')}</label>
        <p className="publish-hint">{t('Published pages reuse their existing views and business APIs. Configuration and management access remain locked.')}</p>
        <div className="publish-readiness" aria-live="polite">
          {checking ? <p role="status">{t('Checking workspace…')}</p> : readiness && <>
            <p>{t(readiness.ready ? 'Workspace is ready to publish' : 'Resolve these pages before publishing')}</p>
            {(readiness.workspaces ?? [{ legion_id: card.id, panels: readiness.panels, issues: readiness.issues }]).map(workspace => <div key={workspace.legion_id}>
              {selectedIds.length > 1 && <strong>{workspace.name ?? workspace.legion_id}</strong>}
              {!!workspace.issues.length && <ul>{workspace.issues.map((issue, index) => <li key={`${issue.card_id}:${issue.section_id}:${index}`}>{issue.message}</li>)}</ul>}
              {!!workspace.panels.length && <ul>{workspace.panels.map(panel => <li key={`${panel.card_id}:${panel.section_id}`}>{panel.name}{panel.section_id ? ` · ${panel.section_id}` : ''}</li>)}</ul>}
            </div>)}
          </>}
        </div>
        {error && <p className="deployment-error" role="alert">{error}</p>}
        <button className="primary-button" disabled={busy || checking || !ready || !selectedIds.length || !name.trim()} onClick={() => { void publish(); }}>{busy ? t('Publishing…') : t('Create release')}</button>
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
