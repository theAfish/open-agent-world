import { t, useLocale } from "../i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import type { WorldCard } from "../types/world";
import { FileText, Files, Eye, Download, Copy, RefreshCw } from "lucide-react";
import "./artifacts.css";
import { Modal } from '../components/Modal';
import { WorkspaceEmptyState, WorkspaceToolbar } from '../workspace/WorkspacePrimitives';
import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';

export interface ArtifactVersion {
  version_id: string; artifact_id: string; name: string; state: string; size_bytes: number;
  created_at: string; error?: string; cleanup?: string; cleanup_error?: string;
  retention: { retained: boolean; owner: string; reason: string };
  provenance: { agent_id?: string; agent_name?: string; run_id?: string; sandbox_name: string; selected_paths: string[] };
  manifest: { path: string; directory: boolean; size: number; sha256?: string }[];
}

export interface ArtifactReference { version_id: string; name: string; state: string; collection_id?: string }

export function PublishedReferences({ references }: { references?: ArtifactReference[] }) {
  useLocale();
  return <>{references?.map(ref => <p key={ref.version_id}>{ref.name} · {ref.state} · {ref.version_id}
    {ref.collection_id && <button className="secondary-button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(ref.collection_id!)}>{t("Inspect artifact")}</button>}</p>)}</>;
}

export function PublishFiles({ card, paths, children, compact = false }: { card: WorldCard; paths: string[]; children?: ReactNode; compact?: boolean }) {
  useLocale();
  const cards = useWorldStore(s => s.cards);
  const [collection, setCollection] = useState("");
  const [finalized, setFinalized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [publishOpen, setPublishOpen] = useState(false);
  const request = useRef<{ fingerprint: string; key: string }>();
  async function publish() {
    setBusy(true); setMessage("");
    try {
      const fingerprint = JSON.stringify([collection, paths]);
      if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
      const result = await worldApi.artifacts<ArtifactVersion>(collection, "versions", {
        sandbox_id: card.id, paths, finalized, name: paths.length === 1 ? paths[0].split("/").at(-1) : t("{v0} outputs", { v0: String(card.name) }), request_key: request.current.key,
      });
      setMessage(result.state === "ready" ? t("Published {v0} · {v1}", { v0: String(result.name), v1: String(result.version_id) }) : `${result.state}: ${result.error ?? t("Inspect collection for details")}`);
      if (result.state !== "staging") request.current = undefined;
    } catch (error) { setMessage(apiErrorMessage(error)); }
    finally { setBusy(false); }
  }
  const content = <div className="artifact-publish-content">
    {children}
    <p>{paths.join(", ") || t("Select files or directories in the file tree.")}</p>
    <label>{t("Collection")} <select aria-label={t("Publication collection")} value={collection} onChange={e => setCollection(e.target.value)}>
      <option value="">{t("Choose a collection")}</option>{cards.filter(c => c.type === "core.artifact-collection").map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select></label>
    <button className="secondary-button" disabled={busy} onClick={() => void useWorldStore.getState().createCard("core.artifact-collection", { x: card.position.x + 360, y: card.position.y }).then(c => { if (c) setCollection(c.id); })}>{t("New collection")}</button>
    <label><input type="checkbox" checked={finalized} onChange={e => setFinalized(e.target.checked)} />{t("Files are finalized; external writers are paused")}</label>
    <button className="primary-button" disabled={busy || !collection || !finalized || !paths.length} onClick={() => void publish()}>{busy ? t("Publishing…") : t("Publish retained version")}</button>
    {collection && <button className="secondary-button" onClick={() => { useNodeSurfaceStore.getState().openWorkspace(collection); if (compact) setPublishOpen(false); }}>{t("Inspect collection")}</button>}
    {message && <p role="status">{message}</p>}
    </div>;
  const title = `${t('Publish selected files (')}${paths.length})`;
  return compact ? <>
    <button type="button" className="sandbox-publish-button" aria-label={title} onClick={() => setPublishOpen(true)}>{t('Publish')}{paths.length > 0 && <span>{paths.length}</span>}</button>
    {publishOpen && <Modal title={title} busy={busy} onClose={() => setPublishOpen(false)} className="artifact-publish-dialog">{content}</Modal>}
  </> : <details className="artifact-publish"><summary>{title}</summary>{content}</details>;
}

function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / (bytes < 1048576 ? 1024 : 1048576)).toFixed(1)} ${bytes < 1048576 ? 'KB' : 'MB'}`;
}

type PreviewTarget = { version: ArtifactVersion; path: string };

function ArtifactPreview({ collectionId, target, onClose }: { collectionId: string; target: PreviewTarget; onClose: () => void }) {
  const [result, setResult] = useState<{ text?: string; state: string; truncated?: boolean }>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setResult(undefined); setError('');
    void worldApi.artifacts<{ text?: string; state: string; truncated?: boolean }>(collectionId,
      `versions/${target.version.version_id}/preview?${new URLSearchParams({ path: target.path })}`)
      .then(value => { if (live) setResult(value); }).catch(e => { if (live) setError(apiErrorMessage(e)); });
    return () => { live = false; };
  }, [collectionId, target.version.version_id, target.path, attempt]);
  return <Modal title={target.path} onClose={onClose} className="artifact-preview-dialog" actions={
    <a className="secondary-button artifact-download" href={worldApi.artifactDownloadUrl(collectionId, target.version.version_id, target.path)} download><Download size={14} aria-hidden="true" />{t('Download')}</a>
  }>
    <p className="artifact-preview-source">{target.version.name}</p>
    {!result && !error && <p role="status">{t('Loading preview…')}</p>}
    {error && <div><p role="alert" className="ui-error">{error}</p><button className="secondary-button" onClick={() => setAttempt(a => a + 1)}>{t('Retry')}</button></div>}
    {result && (result.state === 'text' ? <>
      {result.truncated && <p className="artifact-preview-note">{t('Preview limited to 64 KiB. Download for the complete file.')}</p>}
      {result.text ? <pre className="artifact-preview">{result.text}</pre> : <p>{t('This file is empty.')}</p>}
    </> : <p>{t('This file has no text preview. Download it to view its contents.')}</p>)}
  </Modal>;
}

function ArtifactEntry({ version: v, collectionId, busy, onPreview, onCopy, action }: {
  version: ArtifactVersion; collectionId: string; busy: boolean; onPreview: (target: PreviewTarget) => void;
  onCopy: () => void; action: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const { locale } = useLocale();
  const { deployed } = useWorkspaceAccess();
  const [expanded, setExpanded] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const files = v.manifest.filter(file => !file.directory);
  const date = new Date(v.created_at);
  const states: Record<string, string> = { ready: 'Ready', staging: 'Publishing…', failed: 'Failed', deleting: 'Deleting…', deleted: 'Deleted', released: 'Released' };
  return <article className="artifact-entry">
    <header className="artifact-entry-heading">
      <span className="artifact-entry-icon" aria-hidden="true">{files.length > 1 ? <Files size={20} /> : <FileText size={20} />}</span>
      <div className="artifact-entry-title"><h4>{v.name}</h4>
        <p className="artifact-version-meta"><span>{files.length === 1 ? t('1 file') : t('{count} files', { count: files.length })}</span><span>{sizeLabel(v.size_bytes)}</span>
          <time dateTime={v.created_at} title={v.created_at}>{Number.isNaN(date.getTime()) ? v.created_at : date.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' })}</time></p>
      </div>
      <span className="artifact-status" data-state={v.state}>{t(states[v.state] ?? v.state)}</span>
    </header>
    {(v.error || v.cleanup_error) && <p role="alert" className="ui-error">{v.error ?? v.cleanup_error}</p>}
    {!!files.length && <ul className="artifact-files">{(expanded ? files : files.slice(0, 3)).map(file => <li key={file.path}>
      <FileText size={15} aria-hidden="true" /><span className="artifact-file-path" title={file.path}>{file.path}</span><span className="artifact-file-size">{sizeLabel(file.size)}</span>
      {v.state === 'ready' && <div className="artifact-file-actions">
        <button className="artifact-preview-button" onClick={() => onPreview({ version: v, path: file.path })}><Eye size={14} aria-hidden="true" />{t('Preview')}</button>
        <a className="artifact-download" href={worldApi.artifactDownloadUrl(collectionId, v.version_id, file.path)} download aria-label={t('Download {name}', { name: file.path })} title={t('Download')}><Download size={15} aria-hidden="true" /></a>
      </div>}
    </li>)}</ul>}
    {files.length > 3 && <button className="artifact-text-button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? t('Show fewer files') : t('Show all {count} files', { count: files.length })}</button>}
    <div className="artifact-entry-secondary">
      <details><summary>{t('Version details')}</summary><dl className="artifact-metadata">
        <dt>{t('Version')}</dt><dd>{v.version_id}</dd><dt>{t('Artifact')}</dt><dd>{v.artifact_id}</dd>
        <dt>{t('Produced by')}</dt><dd>{v.provenance.agent_name ?? t('User')}</dd>
        <dt>{t('Sandbox')}</dt><dd>{v.provenance.sandbox_name}</dd>
        {v.provenance.run_id && <><dt>Run</dt><dd>{v.provenance.run_id}</dd></>}
        <dt>{t('Retention:')}</dt><dd>{v.retention.retained ? v.retention.reason : t('Released')} · {v.retention.owner}</dd>
      </dl><details className="artifact-manifest"><summary>{t('Content manifest (')}{v.manifest.length})</summary>
        {v.manifest.map(file => <div key={file.path}><strong>{file.path}{file.directory ? '/' : ''}</strong>{file.sha256 && <code>SHA-256 {file.sha256}</code>}</div>)}
      </details></details>
      {!deployed && <><button className="artifact-text-button artifact-copy-button" disabled={busy || v.state !== 'ready'} onClick={onCopy}><Copy size={14} aria-hidden="true" />{t('Copy into Sandbox')}</button>
      <details className="artifact-manage" onToggle={e => { if (!e.currentTarget.open) setConfirm(false); }}><summary>{t('Manage version')}</summary>
        <p>{t('Retained independently of producers. Removing a reference preserves its stored content.')}</p>
        <div className="artifact-manage-actions"><button className="secondary-button" disabled={busy} onClick={() => void action(() => worldApi.artifacts(collectionId, `references/${v.version_id}`, undefined, 'DELETE'))}>{t('Remove reference')}</button>
        {confirm ? <><button className="danger-button" disabled={busy} onClick={() => void action(() => worldApi.artifacts(collectionId, `versions/${v.version_id}`, undefined, 'DELETE'))}>{t('Confirm release and delete stored content')}</button><button className="secondary-button" disabled={busy} onClick={() => setConfirm(false)}>{t('Cancel')}</button></>
          : <button className="artifact-text-button artifact-release" disabled={busy || v.state === 'deleted'} onClick={() => setConfirm(true)}>{t('Release retained content…')}</button>}</div>
      </details></>}
    </div>
  </article>;
}

export function ArtifactCollection({ card }: { card: WorldCard }) {
  useLocale();
  const { deployed } = useWorkspaceAccess();
  const socket = useWorldStore(s => s.socketState);
  const event = useWorldStore(s => s.events.find(e => e.type === 'artifact_updated' && e.node_id === card.id)?.id);
  const cards = useWorldStore(s => s.cards);
  const [versions, setVersions] = useState<ArtifactVersion[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<PreviewTarget>();
  const [copy, setCopy] = useState<ArtifactVersion>();
  const [copyError, setCopyError] = useState('');
  const [notice, setNotice] = useState('');
  const [sandbox, setSandbox] = useState('');
  const [destination, setDestination] = useState('');
  const [retained, setRetained] = useState<ArtifactVersion[]>([]);
  const [busy, setBusy] = useState(false);
  async function refresh() { setVersions(await worldApi.artifacts<ArtifactVersion[]>(card.id)); }
  useEffect(() => { let live = true;
    void worldApi.artifacts<ArtifactVersion[]>(card.id).then(value => { if (live) { setVersions(value); setError(''); } }).catch(e => { if (live) setError(apiErrorMessage(e)); }).finally(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [card.id, socket, event]);
  useEffect(() => {
    if (!deployed && !versions.some(v => v.state === 'staging' || v.state === 'deleting')) return;
    const timer = window.setInterval(() => void refresh().catch(e => setError(apiErrorMessage(e))), 2000);
    return () => window.clearInterval(timer);
  }, [versions, card.id, deployed]);
  async function action(operation: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await operation(); await refresh(); }
    catch (e) { setError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  }
  async function materialize() {
    if (!copy) return;
    setBusy(true); setCopyError('');
    try {
      await worldApi.artifacts(card.id, `versions/${copy.version_id}/materialize`, { sandbox_id: sandbox, destination });
      setNotice(t('Copied to {path}', { path: destination })); setCopy(undefined);
    } catch (e) { setCopyError(apiErrorMessage(e)); }
    finally { setBusy(false); }
  }
  return <div className="artifact-collection nowheel">
    <WorkspaceToolbar label={t('Published versions')}><div className="artifact-collection-heading"><strong>{t('Published versions')}</strong><span>{versions.length}</span></div>
      <button className="artifact-text-button" disabled={busy} onClick={() => void action(async () => {})}><RefreshCw size={14} aria-hidden="true" />{t('Refresh')}</button></WorkspaceToolbar>
    <p className="artifact-collection-description">{t('Published snapshots stay unchanged when workspace files are edited.')}</p>
    {error && <p role="alert" className="ui-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!versions.length && !error && <WorkspaceEmptyState busy={!loaded} title={loaded ? t('No published files yet') : t('Loading…')}
      description={loaded ? t('Publish selected files from a Sandbox to keep a version here.') : undefined} />}
    {[...versions].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(v => <ArtifactEntry key={v.version_id} version={v} collectionId={card.id} busy={busy} onPreview={setPreview} action={action}
      onCopy={() => { setCopy(v); setDestination(''); setCopyError(''); setNotice(''); }} />)}
    {!deployed && <details className="artifact-restore"><summary>{t('Restore retained references')}</summary>
      <button className="secondary-button" disabled={busy} onClick={() => void action(async () => setRetained(await worldApi.retainedArtifacts<ArtifactVersion[]>()))}>{t('Browse retained versions')}</button>
      {retained.filter(v => v.retention.retained && !versions.some(r => r.version_id === v.version_id)).map(v => <p key={v.version_id}>{v.name} · {v.version_id}
        <button className="secondary-button" disabled={busy} onClick={() => void action(() => worldApi.artifacts(card.id, `references/${v.version_id}`, undefined, 'PUT'))}>{t('Add reference')}</button></p>)}
    </details>}
    {preview && <ArtifactPreview collectionId={card.id} target={preview} onClose={() => setPreview(undefined)} />}
    {copy && <Modal title={t('Copy into Sandbox')} busy={busy} onClose={() => setCopy(undefined)} className="artifact-copy-dialog" actions={
      <button className="primary-button" disabled={busy || !sandbox || !destination.trim()} onClick={() => void materialize()}>{t('Copy version to workspace')}</button>
    }><p><strong>{copy.name}</strong></p><p>{t('Create a working copy in a new directory. The published version stays unchanged.')}</p>
      <div className="artifact-copy-settings"><label>{t('Copy into Sandbox')}<select value={sandbox} onChange={e => setSandbox(e.target.value)}><option value="">{t('Choose workspace')}</option>{cards.filter(c => c.type === 'sandbox').map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>{t('New destination directory')}<input value={destination} onChange={e => setDestination(e.target.value)} placeholder="results/review-01" /></label></div>
      {copyError && <p role="alert" className="ui-error">{copyError}</p>}
    </Modal>}
  </div>;
}
