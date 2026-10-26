import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from 'react';
import { ArrowLeft, Check, ChevronDown, Code2, Copy, Download, Eye, FileCode2, FolderOpen, Layers, Package, Plus, RefreshCw, Save, Settings2, X } from 'lucide-react';
import { ApiError, apiErrorMessage, worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { exportedPackFile, MarketplacePublish, PACK_ID_PATTERN } from '../shell/PackPublishing';
import type { PluginSlot } from '../plugins/sdk';
import type { NodeSurfaceLevel, WorldCard } from '../types/world';
import { AuthoringPreview } from './AuthoringCardView';
import { PackVisualEditor } from './PackVisualEditor';
import type { SourceEditorSession } from './SourceEditor';
import { FolderPathInput } from '../shell/FolderPathInput';
import { createPythonStarter } from './pythonScaffold';
import { faceStudio, FACE_MODES } from './faceDesign';
import type { Scalar } from './types';
import type { CardInterface, PackProject, ProjectCard, ProjectCheck, ProjectDiagnostic, ProjectList, ProjectTextFile } from './projectTypes';
import './packIDE.css';

type Buffer = Omit<ProjectTextFile, 'sha256'> & { original: string; sha256: string | null };
type Mode = 'design' | 'function' | 'code';
type EditorDraft = { project?: PackProject; buffers: Record<string, Buffer>; pending: Record<string, CardInterface>; selected: string; filePath: string; mode: Mode };
const emptyList: ProjectList = { projects: [], default_directory: '' };
const format = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
// Installed preview is a host surface. Keep its optional plugin implementations
// out of the exported Pack SDK declaration graph.
const hostSurfaces = import.meta.glob<{ PluginSurface: ComponentType<{ card: WorldCard; slot: PluginSlot; level: NodeSurfaceLevel }> }>('../plugins/PluginSurface.tsx');
const InstalledCardSurface = lazy(async () => ({ default: (await hostSurfaces['../plugins/PluginSurface.tsx']()).PluginSurface }));
const SourceEditor = lazy(() => import('./SourceEditor'));

export function initialInterface(card: ProjectCard): CardInterface {
  const face: CardInterface['face'] = { title: card.label || card.id, description: '', variant: 'text', tone: 'sage', color: '#617b72',
    icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: 'Run' };
  return { face: { ...face, studio: faceStudio(face) }, function: { fields: [], operation: 'template', template: '', separator: '\n' },
    action: card.actions?.[0]?.id ?? 'run', result_key: 'result', surfaces: card.kind === 'declarative' ? ['preview', 'body', 'settings', 'workspace'] : [] };
}

function download(file: File) {
  const url = URL.createObjectURL(file), anchor = document.createElement('a');
  anchor.href = url; anchor.download = file.name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PackIDE({ onDirtyChange, onBusyChange, draftKey, onClose }: { onDirtyChange?: (dirty: boolean) => void; onBusyChange?: (busy: boolean) => void; draftKey?: string; onClose?: () => void }) {
  useLocale();
  const [initial] = useState<Partial<EditorDraft>>(() => {
    const saved = draftKey && useNodeSurfaceStore.getState().drafts[draftKey];
    return saved ? JSON.parse(saved) as EditorDraft : {};
  });
  const [project, setProject] = useState<PackProject | undefined>(initial.project), [listing, setListing] = useState<ProjectList>(emptyList);
  const [buffers, setBuffers] = useState<Record<string, Buffer>>(initial.buffers ?? {}), [pending, setPending] = useState<Record<string, CardInterface>>(initial.pending ?? {});
  const [selected, setSelected] = useState(initial.selected ?? ''), [filePath, setFilePath] = useState(initial.filePath ?? ''), [mode, setMode] = useState<Mode>(initial.mode ?? 'design');
  const [name, setName] = useState('My Pack'), [distributionId, setDistributionId] = useState('local.my-pack'), [path, setPath] = useState('');
  const [customId, setCustomId] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [external, setExternal] = useState<PackProject>(), [check, setCheck] = useState<ProjectCheck>(), [artifact, setArtifact] = useState<File>();
  const [preview, setPreview] = useState(false), [level, setLevel] = useState<NodeSurfaceLevel>('inspector');
  const [directory, setDirectory] = useState(''), [newCardName, setNewCardName] = useState(''), [legacyId, setLegacyId] = useState('');
  const [newFile, setNewFile] = useState(''), [backendWheel, setBackendWheel] = useState(''), [frontendDir, setFrontendDir] = useState('');
  const [runtimePreview, setRuntimePreview] = useState(false);
  const [releaseVersion, setReleaseVersion] = useState(initial.project?.version ?? '');
  const [diskContent, setDiskContent] = useState<Record<string, string>>({});
  const [launcher, setLauncher] = useState<'new' | 'open'>('new');
  const [tabs, setTabs] = useState<string[]>(initial.filePath ? [initial.filePath] : []);
  const [panel, setPanel] = useState<'diagnostics' | 'build' | undefined>();
  const [task, setTask] = useState('');
  const [reveal, setReveal] = useState<{ line: number; column?: number; token: number }>();
  const [codePreview, setCodePreview] = useState(false);
  const [history, setHistory] = useState<Record<string, CardInterface[]>>({});
  const editorSession = useMemo<SourceEditorSession>(() => new Map(), []);
  const [editorGeneration, setEditorGeneration] = useState(0);
  const active = useRef(false), latest = useRef({ project, buffers, pending, selected, filePath, mode }); latest.current = { project, buffers, pending, selected, filePath, mode };
  useEffect(() => () => {
    if (draftKey) useNodeSurfaceStore.getState().setDraft(draftKey, JSON.stringify(latest.current));
  }, [draftKey]);
  const dirty = Object.values(buffers).some(buffer => buffer.content !== buffer.original) || Object.keys(pending).length > 0;
  const cards = useWorldStore(state => state.cards);
  const legacyCards = cards.filter(card => card.config.face && card.config.function);
  const selectedCard = project?.cards.find(card => card.id === selected) ?? project?.cards[0];
  const installedCard = cards.find(card => card.type === selectedCard?.id);
  let design = selectedCard?.design_path && buffers[selectedCard.design_path] ? undefined : selectedCard?.design ?? undefined;
  let designError = '';
  if (selectedCard?.design_path && buffers[selectedCard.design_path]) {
    try { const raw = JSON.parse(buffers[selectedCard.design_path].content) as CardInterface; const base = selectedCard.design ?? initialInterface(selectedCard); design = { ...base, ...raw, face: { ...base.face, ...raw.face }, function: { ...base.function, ...raw.function } }; }
    catch { designError = 'The visual resource contains invalid JSON. Correct it in Code mode.'; }
  }
  if (selectedCard && pending[selectedCard.id]) design = pending[selectedCard.id];
  if (design && (!design.face || !design.function || !Array.isArray(design.function.fields) || !Array.isArray(design.surfaces) || (design.face.studio && (!design.face.studio.enabled?.length || !design.face.studio.modes || !design.face.studio.modes[design.face.studio.initial])))) {
    design = undefined; designError = 'The visual resource needs face, function, and surfaces. Correct it in Code mode.';
  }
  if (design) {
    try {
      const studio = faceStudio(design.face);
      if (studio.enabled.some(item => {
        const surface = studio.modes[item];
        return !surface || !Number.isFinite(surface.width) || !Number.isFinite(surface.height) || !Array.isArray(surface.shapes) || !surface.shapes.length || !Array.isArray(surface.elements);
      })) throw new Error('Invalid surface');
    } catch { design = undefined; designError = 'The visual resource contains an invalid surface. Correct it in Code mode, then run Check.'; }
  }
  const perform = async (work: () => Promise<void>, label = 'Working') => {
    if (active.current) return;
    active.current = true; setBusy(true); setTask(label); setError(''); setNotice('');
    try { await work(); } catch (cause) {
      setError(apiErrorMessage(cause));
      if (cause instanceof ApiError && cause.status === 409 && latest.current.project) {
        try { setExternal(await worldApi.getPackProject(latest.current.project.id)); } catch { /* Keep the original conflict. */ }
      }
    } finally { active.current = false; setBusy(false); setTask(''); }
  };
  const accept = (next: PackProject, clear = false) => {
    setProject(next); setExternal(undefined); setArtifact(undefined); setCheck(undefined);
    setReleaseVersion(next.version);
    if (clear) { setBuffers({}); setPending({}); setSelected(next.cards[0]?.id ?? ''); setFilePath(''); setPreview(false); setTabs([]); setHistory({}); setPanel(undefined); setBackendWheel(''); setFrontendDir(''); setDiskContent({}); editorSession.clear(); setEditorGeneration(value => value + 1); }
  };
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => { void worldApi.listPackProjects().then(setListing).catch(cause => setError(apiErrorMessage(cause))); }, []);
  const refresh = useCallback(async (force = false) => {
    const current = latest.current;
    if (!current.project || (active.current && !force)) return;
    const next = await worldApi.getPackProject(current.project.id);
    if (latest.current.project?.id !== next.id || next.revision === latest.current.project?.revision) return;
    setArtifact(undefined); setCheck(undefined);
    if (Object.values(latest.current.buffers).some(buffer => buffer.content !== buffer.original) || Object.keys(latest.current.pending).length) setExternal(next);
    else { setProject(next); setBuffers({}); setExternal(undefined); setNotice('External changes loaded.'); }
  }, []);
  useEffect(() => {
    if (!project) return;
    const update = () => { void refresh().catch(cause => setError(apiErrorMessage(cause))); };
    window.addEventListener('focus', update); const timer = window.setInterval(update, 5000);
    return () => { window.removeEventListener('focus', update); window.clearInterval(timer); };
  }, [project?.id, refresh]);
  useEffect(() => {
    if (!project || !filePath || buffers[filePath]) return;
    let current = true;
    void worldApi.readPackProjectFile(project.id, filePath).then(file => {
      if (current) setBuffers(existing => existing[file.path] ? existing : { ...existing, [file.path]: { ...file, original: file.content } });
    }).catch(cause => { if (current) setError(apiErrorMessage(cause)); });
    return () => { current = false; };
  }, [project?.id, project?.revision, filePath, buffers]);
  const invalidate = () => { setCheck(undefined); setArtifact(undefined); };
  const changeDesign = (next: CardInterface, remember = true) => {
    if (!selectedCard || !project || active.current) return;
    if (remember && design) setHistory(current => ({ ...current, [selectedCard.id]: [...(current[selectedCard.id] ?? []).slice(-49), design] }));
    invalidate();
    setPending(current => ({ ...current, [selectedCard.id]: next }));
    const resource = selectedCard.design_path;
    if (!resource) return;
    setBuffers(current => ({ ...current, [resource]: { path: resource, sha256: current[resource]?.sha256 ?? project.files.find(file => file.path === resource)?.sha256 ?? null,
      original: current[resource]?.original ?? format(selectedCard.design), content: format(next) } }));
  };
  const save = async () => {
    if (!project) return;
    if (external) throw new Error('Review external changes before saving. Your draft is still available below.');
    let current = project;
    const visualPaths = new Set(project.cards.filter(card => pending[card.id]).map(card => card.design_path));
    for (const [cardId, value] of Object.entries(pending)) {
      current = await worldApi.savePackProjectDesign(current.id, { card_id: cardId, design: value, expected_revision: current.revision });
      setProject(current);
      const designPath = current.cards.find(card => card.id === cardId)?.design_path;
      if (designPath) {
        const file = await worldApi.readPackProjectFile(current.id, designPath);
        setBuffers(previous => ({ ...previous, [designPath]: { ...file, original: file.content } }));
      }
      setPending(previous => { const next = { ...previous }; delete next[cardId]; return next; });
    }
    for (const buffer of Object.values(buffers).filter(value => value.content !== value.original && !visualPaths.has(value.path))) {
      const saved = await worldApi.writePackProjectFile(current.id, { path: buffer.path, content: buffer.content, expected_sha256: buffer.sha256 });
      setBuffers(previous => ({ ...previous, [saved.path]: { ...saved, original: saved.content } }));
    }
    accept(await worldApi.getPackProject(current.id)); setNotice('Project files saved.');
  };
  const maySwitch = () => !dirty || window.confirm('Discard your unsaved project edits?');
  const selectFile = (value: string) => { setFilePath(value); setTabs(current => current.includes(value) ? current : [...current, value]); setMode('code'); setPreview(false); setReveal(undefined); };
  const runCheck = async () => {
    if (!project) return;
    const result = await worldApi.checkPackProject(project.id, project.revision); setCheck(result);
    setNotice(result.ok ? 'Check passed. Ready to build.' : 'Check found errors. Select a diagnostic to open its source.');
    if (!result.ok) setPanel('diagnostics');
  };
  const currentBuffer = buffers[filePath];
  const files = [...(project?.files ?? []), ...Object.values(buffers).filter(file => !project?.files.some(saved => saved.path === file.path)).map(file => ({ ...file, editable: true }))];
  const sourceFiles = files.filter(file => /\.(py|tsx?|jsx?)$/.test(file.path));
  const designPaths = new Set(project?.cards.map(card => card.design_path));
  const diagnostics = check?.diagnostics ?? project?.diagnostics ?? [];
  const errors = diagnostics.filter(item => item.severity === 'error').length;
  const saveReason = external ? 'Review external changes first' : !dirty ? 'All changes saved' : 'Save project files (Ctrl+S)';
  const buildReason = external ? 'Review external changes first' : dirty ? 'Save changes before building' : !check?.ok ? 'Run Check before building' : 'Build and download the Pack';
  const openCode = () => {
    if (filePath) { setMode('code'); setPreview(false); return; }
    selectFile(selectedCard?.source_path ?? sourceFiles.find(file => file.path.endsWith('.py'))?.path ?? sourceFiles[0]?.path ?? 'oaw.pack.toml');
  };
  const openDiagnostic = (item: ProjectDiagnostic) => {
    if (!item.path) return;
    selectFile(item.path); setReveal({ line: item.line ?? 1, column: item.column, token: Date.now() });
  };
  const changeSource = (content: string) => {
    setBuffers(current => ({ ...current, [filePath]: { ...current[filePath], content } }));
    setPending(current => Object.fromEntries(Object.entries(current).filter(([id]) => project?.cards.find(card => card.id === id)?.design_path !== filePath)));
    invalidate();
  };
  const addFile = (path: string, content = '\n') => {
    if (files.some(file => file.path === path)) { setError('This file already exists. Select it from the tree.'); return; }
    if (!path || path.startsWith('/') || path.includes('\\') || path.split('/').some(part => !part || part === '..' || part === '.')) { setError('Use a relative project path, such as src/analysis.py.'); return; }
    setBuffers(current => ({ ...current, [path]: { path, content, original: '', sha256: null } })); selectFile(path); setNewFile(''); invalidate();
  };
  const closeTab = (path: string) => {
    // Closing a tab never discards its buffer; Save includes all project changes.
    const remaining = tabs.filter(value => value !== path); setTabs(remaining);
    if (filePath === path) setFilePath(remaining.at(-1) ?? '');
  };
  const addPythonStarter = () => {
    if (!project || dirty || external) return;
    try {
      const starter = createPythonStarter(project, Object.keys(buffers));
      setBuffers(current => ({ ...current, ...Object.fromEntries(Object.entries(starter.files).map(([path, content]) => [path, { path, content, original: '', sha256: null }])) }));
      selectFile(starter.primaryPath); invalidate();
      setNotice('Python starter added. Implement its actions, then follow PYTHON_SETUP.md to connect and build it.');
    } catch (cause) { setError(apiErrorMessage(cause)); }
  };
  const run = design && selectedCard?.kind === 'declarative' ? async (action: string, values: Record<string, Scalar>) => {
    if (action !== design.action) throw new Error('This action has no built-in implementation. Select the primary action or implement it in Python.');
    return (await worldApi.previewPackProject(project!.id, design.function, values)).result;
  } : undefined;
  const testAction = run ?? (design && installedCard ? async (action: string, values: Record<string, Scalar>) => {
    const response = await worldApi.nodeResourceAction(installedCard.id, action, values);
    return design.result_key ? response[design.result_key] : response;
  } : undefined);
  const leaveProject = () => { if (maySwitch()) { setProject(undefined); setBuffers({}); setPending({}); setArtifact(undefined); setExternal(undefined); setDiskContent({}); setCheck(undefined); setPanel(undefined); setError(''); setNotice(''); void worldApi.listPackProjects().then(setListing).catch(cause => setError(apiErrorMessage(cause))); } };
  const treeFile = (file: typeof files[number]) => <button className="pack-ide-tree-item" aria-label={file.path} aria-current={mode === 'code' && filePath === file.path ? 'true' : undefined} disabled={!file.editable || busy} key={file.path} onClick={() => selectFile(file.path)} title={file.path}>
    <FileCode2 size={13} /><span>{file.path.split('/').at(-1)}<small>{file.path.includes('/') ? file.path.slice(0, file.path.lastIndexOf('/')) : ''}</small></span>{buffers[file.path] && buffers[file.path].content !== buffers[file.path].original && <i className="pack-ide-dirty-dot" aria-label="Unsaved" />}</button>;
  return <section className="factory pack-ide nodrag nopan nowheel" aria-label="Pack IDE" aria-busy={busy} onKeyDownCapture={event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); event.stopPropagation(); if (dirty && !external && !busy) void perform(save, 'Saving'); }
  }}>
    <header className="pack-ide-header">
      <div className="pack-ide-brand">{project ? <button className="pack-ide-icon" aria-label="Back to projects" title="Back to projects" disabled={busy} onClick={leaveProject}><ArrowLeft size={17} /></button> : <Package size={21} />}<div><h2>{project?.name ?? 'Pack IDE'}</h2><span>{project ? `${project.distribution_id} / ${project.version}` : 'Card Factory'}</span></div></div>
      <span className="pack-ide-experimental" title={t('Visual authoring is still in development.')}>{t('Experimental')}</span>
      {project && <div className="pack-ide-toolbar"><span className={`pack-ide-save-state ${dirty ? 'is-dirty' : ''}`} role="status">{busy ? `${task}...` : external ? 'Disk conflict' : dirty ? 'Unsaved changes' : 'All changes saved'}</span>
        <button aria-label="Save" title={saveReason} disabled={busy || !dirty || !!external} onClick={() => void perform(save, 'Saving')}><Save size={14} />Save</button>
        <button title={dirty ? 'Save changes before checking' : 'Validate project files'} disabled={busy || dirty || !!external} onClick={() => void perform(runCheck, 'Checking')}><Check size={14} />Check</button>
        <button disabled={busy} aria-pressed={preview} onClick={() => setPreview(!preview)}><Eye size={14} />Preview</button>
        <button className="pack-ide-primary" title={buildReason} disabled={busy || dirty || !!external || check?.ok !== true} onClick={() => { setPanel('build'); void perform(async () => {
          const blob = await worldApi.buildPackProject(project.id, { expected_revision: project.revision, ...(backendWheel ? { backend_wheel: backendWheel } : {}), ...(frontendDir ? { frontend_dir: frontendDir } : {}) });
          const file = exportedPackFile(blob, { id: project.distribution_id, version: project.version }); download(file); setArtifact(file); setNotice(`Built ${file.name}. Install it from the Pack Library.`);
        }, 'Building archive'); }}><Download size={14} />Build / Export</button>
        <button className="pack-ide-icon" aria-label="Build settings" title="Build settings" aria-pressed={panel === 'build'} onClick={() => setPanel(panel === 'build' ? undefined : 'build')}><Settings2 size={15} /></button></div>}
      {onClose && <button className="pack-ide-icon" aria-label="Close Pack IDE" title="Close Pack IDE" disabled={busy} onClick={onClose}><X size={17} /></button>}
    </header>
    {error && <div className="pack-ide-message factory-error" role="alert"><strong>{task ? `${task} failed. ` : ''}</strong>{error}<button aria-label="Dismiss error" onClick={() => setError('')}><X size={13} /></button></div>}
    {notice && <div className="pack-ide-message" role="status">{notice}<button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={13} /></button></div>}
    {external && <div className="pack-ide-conflict" role="alert"><div><strong>Files changed externally</strong><span>Your edits are preserved. Compare files before reloading.</span><button onClick={() => { if (window.confirm('Reload from disk and discard your unsaved edits?')) accept(external, true); }}>Reload from disk</button></div>
      <details><summary>Compare with disk</summary>{external.files.filter(file => project?.files.find(old => old.path === file.path)?.sha256 !== file.sha256).map(file => <div key={file.path}><code>{file.path}</code>{file.editable && <button onClick={() => void perform(async () => { const current = await worldApi.readPackProjectFile(external.id, file.path); setDiskContent(previous => ({ ...previous, [file.path]: current.content })); })}>Compare {file.path}</button>}{diskContent[file.path] !== undefined && <div className="pack-ide-diff"><pre aria-label={`Draft ${file.path}`}>{buffers[file.path]?.content ?? '(no local edits)'}</pre><pre aria-label={`Disk ${file.path}`}>{diskContent[file.path]}</pre></div>}</div>)}{project?.files.filter(file => !external.files.some(current => current.path === file.path)).map(file => <p key={file.path}>Deleted: <code>{file.path}</code></p>)}</details>
    </div>}
    {!project ? <div className="pack-ide-launcher"><div className="pack-ide-launch-main"><span className="pack-ide-eyebrow">PACK IDE</span><h3>{t('Visual Pack authoring')}</h3><p>{t('Visual authoring is still in development.')}</p>
      <nav className="pack-ide-launch-tabs" aria-label="Project action"><button aria-pressed={launcher === 'new'} onClick={() => setLauncher('new')}><Plus size={16} />New project</button><button aria-pressed={launcher === 'open'} onClick={() => setLauncher('open')}><FolderOpen size={16} />Open project</button></nav>
      {launcher === 'new' ? <form className="pack-ide-launch-form" onSubmit={event => { event.preventDefault(); void perform(async () => accept(await worldApi.createPackProject({ name, distribution_id: distributionId, ...(directory ? { directory } : {}), ...(legacyId ? { design: { ...initialInterface({ id: '', label: name, kind: 'declarative', design: null, design_path: null }), face: legacyCards.find(card => card.id === legacyId)!.config.face as CardInterface['face'], function: legacyCards.find(card => card.id === legacyId)!.config.function as CardInterface['function'] } } : {}) }), true), 'Creating project'); }}>
        <label>Project name<input autoFocus value={name} required maxLength={120} disabled={busy} onChange={event => { const next = event.target.value; setName(next); if (!customId) { const slug = next.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'my-pack'; setDistributionId(`local.${/^[a-z]/.test(slug) ? slug : `card-${slug}`}`); } }} /></label>
        <small className="pack-ide-location">Saved in <code>{directory || listing.default_directory || 'your local Pack projects folder'}</code></small>
        <details><summary>Project options</summary><label>Pack ID<input value={distributionId} required pattern={PACK_ID_PATTERN} maxLength={120} disabled={busy} onChange={event => { setDistributionId(event.target.value); setCustomId(true); }} /></label><label>New project directory<input value={directory} placeholder="Optional absolute path to a new directory" disabled={busy} onChange={event => setDirectory(event.target.value)} /></label>
          {legacyCards.length > 0 && <label>Start from saved Factory card<select value={legacyId} onChange={event => setLegacyId(event.target.value)}><option value="">New card</option>{legacyCards.map(card => <option key={card.id} value={card.id}>{card.name}</option>)}</select></label>}</details>
        <button className="pack-ide-primary" disabled={busy} type="submit">{busy ? 'Creating...' : 'Create project'}</button></form>
        : <form className="pack-ide-launch-form" onSubmit={event => { event.preventDefault(); void perform(async () => accept(await worldApi.openPackProject(path), true), 'Opening project'); }}><label>Project folder</label><FolderPathInput label="Project path" value={path} onChange={setPath} disabled={busy} placeholder="Choose the folder containing oaw.pack.toml" /><small>Your source stays in the same folder as your external editor.</small><button className="pack-ide-primary" disabled={busy || !path.trim()} type="submit">Open folder</button></form>}
    </div><aside className="pack-ide-recents"><h4>Recent projects</h4>{listing.projects.length ? listing.projects.map(item => <button key={item.id} disabled={busy} onClick={() => void perform(async () => accept(await worldApi.getPackProject(item.id), true), 'Opening project')}><FolderOpen size={18} /><span>{item.name}<small title={item.path}>{item.path}</small></span></button>) : <p>Your projects will appear here.<br />Start with a card or open an existing Pack.</p>}</aside></div> : <>
      <div className="pack-ide-body"><aside className="pack-ide-tree"><div className="pack-ide-tree-heading"><strong>EXPLORER</strong><button title="Refresh project" aria-label="Refresh project" disabled={busy} onClick={() => void perform(() => refresh(true), 'Refreshing')}><RefreshCw size={13} /></button></div>
        <details className="pack-ide-project-location"><summary title={project.path}>{project.name}<ChevronDown size={12} /></summary><code>{project.path}</code><button onClick={() => void perform(async () => { await navigator.clipboard.writeText(project.path); setNotice('Project path copied. Open this folder in your external editor.'); })}><Copy size={12} />Copy project path</button><a href={`vscode://file/${project.path.replace(/\\/g, '/').replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/')}`}>Open in VS Code</a><small>Requires VS Code URL registered URL handler.</small></details>
        <h4>Cards</h4>{project.cards.map(card => <button className="pack-ide-tree-item" aria-current={selectedCard?.id === card.id && mode !== 'code' ? 'true' : undefined} key={card.id} disabled={busy} onClick={() => { setSelected(card.id); setMode('design'); setPreview(false); setRuntimePreview(false); }}><Layers size={14} /><span>{card.label || card.id}<small>{card.kind === 'python' ? 'Python card' : 'Visual card'}</small></span></button>)}
        {project.cards.every(card => card.kind === 'declarative') && <details className="pack-ide-new-file"><summary>+ Add card</summary><label>Card name<input value={newCardName} onChange={event => setNewCardName(event.target.value)} /></label><button disabled={busy || dirty || !!external || !newCardName.trim()} title={dirty ? 'Save changes before adding a card' : ''} onClick={() => void perform(async () => { const next = await worldApi.addPackProjectCard(project.id, { name: newCardName, expected_revision: project.revision }); accept(next); setSelected(next.cards.at(-1)?.id ?? ''); setNewCardName(''); setMode('design'); }, 'Adding card')}>Create card</button></details>}
        <details open className="pack-ide-tree-group"><summary>Source <span>{sourceFiles.length}</span></summary>{sourceFiles.map(treeFile)}{sourceFiles.length === 0 && <><p className="pack-ide-tree-hint">This card uses built-in behavior. No Python or frontend source yet.</p><button className="pack-ide-tree-item" disabled={busy || dirty || !!external} title={dirty || external ? 'Save and resolve changes before creating a Python starter' : 'Add an inactive Python implementation and setup guide'} onClick={addPythonStarter}><Plus size={13} />Add Python starter</button></>}</details>
        <details open className="pack-ide-tree-group"><summary>Design resources</summary>{files.filter(file => designPaths.has(file.path)).map(treeFile)}</details>
        <details open className="pack-ide-tree-group"><summary>Project files</summary>{files.filter(file => !sourceFiles.includes(file) && !designPaths.has(file.path)).map(treeFile)}</details>
        {Boolean(project.relationships?.length) && <details className="pack-ide-tree-group"><summary>Relationships</summary>{project.relationships!.map(item => <button className="pack-ide-tree-item" key={item.id} disabled={!item.source_path} onClick={() => { if (item.source_path) selectFile(item.source_path); }}><Layers size={13} /><span>{item.label || item.id}</span></button>)}</details>}
        <details className="pack-ide-new-file"><summary>+ Add source file</summary><label>Relative file path<input value={newFile} placeholder="src/analysis.py" onChange={event => setNewFile(event.target.value)} /></label><button disabled={!newFile || busy} onClick={() => addFile(newFile)}>Add file</button></details>
      </aside><main className="pack-ide-editor"><div className="pack-ide-editor-heading"><div><strong>{selectedCard?.label || 'Project source'}</strong><small>{selectedCard?.kind === 'python' ? 'Python implementation' : 'Visual card'}</small></div>
        <nav aria-label="Editing mode">{(['design', 'function', 'code'] as Mode[]).map(item => <button key={item} aria-pressed={mode === item && !preview} onClick={() => { if (item === 'code') openCode(); else { setMode(item); setPreview(false); } }}>{item === 'code' && <Code2 size={13} />}{item[0].toUpperCase() + item.slice(1)}</button>)}</nav></div>
        <fieldset className="pack-ide-editor-guard" disabled={busy}>{preview ? <div className="pack-ide-preview"><div className="pack-ide-preview-toolbar"><strong>Card preview</strong><label>Display mode<select value={level} onChange={event => setLevel(event.target.value as NodeSurfaceLevel)}>{FACE_MODES.map(value => <option key={value}>{value}</option>)}</select></label>{installedCard && <label className="factory-check"><input type="checkbox" checked={runtimePreview} onChange={event => setRuntimePreview(event.target.checked)} />Use installed card</label>}<button onClick={() => setPreview(false)}>Return to editor</button></div>
          {runtimePreview && installedCard ? <><p>Installed card: {installedCard.name}. Actions affect this instance. Rebuild and restart to load changed Python.</p><Suspense fallback={<p role="status">Loading installed card...</p>}><InstalledCardSurface card={installedCard} slot={level === 'inspector' ? 'body' : level === 'node' ? 'preview' : level} level={level} /></Suspense></>
            : design ? <><p>{selectedCard?.kind === 'declarative' ? 'Try the current design and its built-in behavior.' : 'Visual preview. Build and install to run the Python and custom frontend.'}</p><div className="pack-ide-preview-scroll"><AuthoringPreview design={design} level={level} run={run} onNavigate={(_action, target) => setLevel(target ?? faceStudio(design.face).open)} /></div></> : <p>Build and install to preview the custom frontend.</p>}</div>
        : mode === 'code' ? <div className="pack-ide-code"><div className="pack-ide-file-tabs" role="tablist" aria-label="Open files">{tabs.map(path => <div className="pack-ide-file-tab" key={path} data-active={filePath === path}><button role="tab" aria-selected={filePath === path} title={path} onClick={() => { setFilePath(path); setReveal(undefined); }}>{path.split('/').at(-1)}{buffers[path] && buffers[path].content !== buffers[path].original && <i className="pack-ide-dirty-dot" />}</button><button aria-label={`Close ${path}`} onClick={() => closeTab(path)}><X size={11} /></button></div>)}</div>
          <div className="pack-ide-code-path"><code>{filePath || 'Select a source file'}</code>{design && <button aria-pressed={codePreview} onClick={() => setCodePreview(!codePreview)}><Eye size={12} />Card preview</button>}</div>
          <div className={`pack-ide-code-content ${codePreview && design ? 'with-preview' : ''}`}><div className="pack-ide-source" data-editor-keys>{currentBuffer ? <Suspense fallback={<p role="status">Loading editor...</p>}><SourceEditor session={editorSession} documentKey={`${project.id}:${editorGeneration}:${filePath}`} path={filePath} content={currentBuffer.content} onChange={changeSource} onSave={() => { if (dirty && !external) void perform(save, 'Saving'); }} readOnly={busy} diagnostics={diagnostics.filter(item => item.path === filePath)} reveal={reveal} /></Suspense> : <div className="pack-ide-empty"><Code2 size={27} /><h3>{filePath ? 'Loading source...' : 'Open a file to start editing'}</h3><p>Source files are grouped in the explorer. Changes remain available when you switch tabs or editing modes.</p>{!sourceFiles.length && <p>This project uses built-in behavior. Add a source file to extend it with Python.</p>}</div>}</div>{codePreview && design && <aside className="pack-ide-code-preview"><AuthoringPreview design={design} level="preview" /></aside>}</div></div>
        : selectedCard && design ? null
        : <div className="pack-ide-empty"><Layers size={28} /><h3>{designError || 'Add a visual interface'}</h3><p>{designError ? 'Open the design resource to fix it, then run Check.' : 'Create an editable card face and bind it to the existing actions.'}</p>{selectedCard && !designError ? <button onClick={() => changeDesign(initialInterface(selectedCard))}><Plus size={14} />Add visual interface</button> : selectedCard?.design_path && <button onClick={() => selectFile(selectedCard.design_path!)}>Open design resource</button>}</div>}
        {selectedCard && design && <div className="pack-ide-visual-host" hidden={mode === 'code' || preview}><PackVisualEditor key={selectedCard.id} design={design} onChange={changeDesign} mode={mode === 'function' ? 'function' : 'design'} level={level} onLevelChange={setLevel} actions={selectedCard.actions} cardKind={selectedCard.kind} run={testAction} runContext={selectedCard.kind === 'python' && installedCard ? `Installed card: ${installedCard.name}. Testing affects this instance. Rebuild and restart to load source changes.` : undefined} runDisabledReason={selectedCard.kind === 'python' ? 'Build and install this Pack to run Python actions.' : undefined} canUndo={Boolean(history[selectedCard.id]?.length)} undo={() => { const previous = history[selectedCard.id]?.at(-1); if (previous) { changeDesign(previous, false); setHistory(current => ({ ...current, [selectedCard.id]: current[selectedCard.id].slice(0, -1) })); } }} /></div>}
      </fieldset></main></div>
      {panel && <section className="pack-ide-bottom-panel" aria-label={panel === 'build' ? 'Build panel' : 'Diagnostics'}><div className="pack-ide-panel-heading"><strong>{panel === 'build' ? 'Build & export' : 'Project diagnostics'}</strong><button aria-label="Close bottom panel" onClick={() => setPanel(undefined)}><X size={14} /></button></div>
        {panel === 'diagnostics' ? <div className="pack-ide-diagnostics">{diagnostics.length ? diagnostics.map((item, index) => <button key={index} disabled={!item.path} data-severity={item.severity} onClick={() => openDiagnostic(item)}><span>{item.severity ?? 'info'}</span><span>{item.message}</span>{item.path && <code>{item.path}{item.line ? `:${item.line}` : ''}</code>}</button>) : <p>{check?.ok ? 'Project check passed. Files and declarations are valid.' : 'Run Check to validate the saved project.'}</p>}</div>
        : <div className="pack-ide-build-panel"><div className="pack-ide-build-result">{busy && task === 'Building archive' ? <p role="status"><RefreshCw size={15} className="pack-ide-spinner" />Packaging saved project files...</p> : artifact ? <><strong><Check size={16} />{artifact.name}</strong><p>{(artifact.size / 1024).toFixed(1)} KB / Ready to install from the Pack Library</p><button onClick={() => download(artifact)}><Download size={13} />Download again</button><details><summary>Publish this version</summary><MarketplacePublish file={!dirty && !external ? artifact : undefined} packId={project.distribution_id} busy={busy} /></details></> : <><strong>{buildReason}</strong><p>{project.cards.some(card => card.kind === 'python') ? 'Build the Python wheel in your external toolchain, then choose it here.' : 'Your card design and behavior are packaged together.'}</p></>}</div>
          <div className="pack-ide-build-inputs">{project.cards.some(card => card.kind === 'python') && <><label>Backend wheel<input aria-label="Backend wheel" value={backendWheel} onChange={event => { setBackendWheel(event.target.value); setArtifact(undefined); }} placeholder="dist/my_pack-0.1.0-py3-none-any.whl" /></label><label>Frontend output<input aria-label="Frontend output" value={frontendDir} onChange={event => { setFrontendDir(event.target.value); setArtifact(undefined); }} placeholder="Optional directory, e.g. dist/frontend" /></label></>}
            {project.cards.every(card => card.kind === 'declarative') && <div className="pack-ide-version"><label>Release version<input value={releaseVersion} onChange={event => setReleaseVersion(event.target.value)} /></label><button disabled={busy || dirty || !!external || !releaseVersion || releaseVersion === project.version} onClick={() => void perform(async () => { accept(await worldApi.versionPackProject(project.id, { version: releaseVersion, expected_revision: project.revision })); setNotice('Version updated. Check and build a new archive.'); }, 'Updating version')}>Update version</button></div>}</div></div>}
      </section>}
      <footer className="pack-ide-statusbar"><button data-error={errors > 0} aria-expanded={panel === 'diagnostics'} onClick={() => setPanel(panel === 'diagnostics' ? undefined : 'diagnostics')}><Check size={12} />{errors ? `${errors} error${errors === 1 ? '' : 's'}` : check?.ok ? 'Check passed' : 'Diagnostics'}<ChevronDown size={12} /></button><span>{mode === 'code' && filePath ? filePath : selectedCard?.design_path ?? project.distribution_id}</span><span>{mode === 'code' ? 'UTF-8 / Ctrl+S save / Ctrl+F find' : 'Click a card element to edit'}</span></footer>
    </>}
  </section>;
}
