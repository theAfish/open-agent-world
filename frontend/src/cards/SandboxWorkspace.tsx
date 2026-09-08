import { ChevronDown, ChevronRight, Download, File, FileText, Folder, FolderOpen, History, LayoutPanelLeft, Play, RefreshCw, Settings, Square, Terminal } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import type { WorldCard } from "../types/world";
import { IconButton } from "../components/IconButton";
import { SandboxRuntimeControls, SandboxSettings } from "./SandboxCard";
import { PublishFiles } from "./Artifacts";
import "./sandboxWorkspace.css";

interface Root { id: string; label: string; access: string; directory: boolean }
interface Entry { name: string; directory: boolean; blocked: boolean; size: number }
interface Files { entries?: Entry[]; truncated?: boolean; state?: string; text?: string; data?: string; media_type?: string; message?: string }
interface Receipt { id: string; caller: string; state: string; argv: string[]; stdout?: string; stderr?: string; error?: string; exit_code?: number; duration_seconds?: number; skill_id?: string }
interface Bundle { cached?: boolean; current?: boolean; revision: number; files: string[]; status: string; note: string }

function tabKeys(event: KeyboardEvent<HTMLElement>) {
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  const index = tabs.indexOf(event.target as HTMLButtonElement);
  const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
    : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
    : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
  if (next < 0) return;
  event.preventDefault(); event.stopPropagation(); tabs[next].focus(); tabs[next].click();
}

const boundedSize = (value: number, min: number, max: number, fallback: number) =>
  Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;

export function SandboxWorkspace({ card }: { card: WorldCard }) {
  const refreshSandbox = useWorldStore(s => s.refreshSandbox);
  const info = useWorldStore(s => s.sandboxInfo[card.id]);
  const execute = useWorldStore(s => s.executeSandbox);
  const busy = useWorldStore(s => s.sandboxBusy[card.id]);
  const runtimeError = useWorldStore(s => s.sandboxErrors[card.id]);
  const socket = useWorldStore(s => s.socketState);
  const cards = useWorldStore(s => s.cards);
  const loadRuntimes = useWorldStore(s => s.loadSandboxRuntimes);
  const tab = useNodeSurfaceStore(s => s.drafts[`sandbox-tab:${card.id}`] === "settings" ? "settings" : "workspace");
  const setTab = (value: string) => useNodeSurfaceStore.getState().setDraft(`sandbox-tab:${card.id}`, value);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const draft = useNodeSurfaceStore(s => s.drafts[`sandbox:${card.id}`] ?? "");
  const setDraft = (value: string) => useNodeSurfaceStore.getState().setDraft(`sandbox:${card.id}`, value);
  const [sidebarWidth, setSidebarWidth] = useState(() => boundedSize(Number(useNodeSurfaceStore.getState().drafts[`sandbox-sidebar:${card.id}`] ?? 224), 180, 420, 224));
  const sidebarElement = useRef<HTMLElement>(null);
  const resizing = useRef<{ x: number; width: number; scale: number }>();
  const saveSidebarWidth = (width: number) => {
    const bounded = Math.max(180, Math.min(420, width));
    setSidebarWidth(bounded);
    useNodeSurfaceStore.getState().setDraft(`sandbox-sidebar:${card.id}`, String(bounded));
  };
  const [terminalHeight, setTerminalHeight] = useState(() => boundedSize(Number(useNodeSurfaceStore.getState().drafts[`sandbox-terminal:${card.id}`] ?? 42), 28, 65, 42));
  const workArea = useRef<HTMLElement>(null);
  const terminalResize = useRef<{ y: number; height: number; area: number }>();
  const saveTerminalHeight = (height: number) => {
    const bounded = boundedSize(height, 28, 65, 42);
    setTerminalHeight(bounded);
    useNodeSurfaceStore.getState().setDraft(`sandbox-terminal:${card.id}`, String(bounded));
  };
  const [roots, setRoots] = useState<Root[]>([]);
  const [tree, setTree] = useState<Record<string, Files>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [terminalTab, setTerminalTab] = useState("terminal");
  const [selection, setSelection] = useState<{ root: string; path: string; label: string }>();
  const [preview, setPreview] = useState<Files>();
  const [history, setHistory] = useState<Receipt[]>([]);
  const [error, setError] = useState("");
  const [filesError, setFilesError] = useState("");
  const [publishPaths, setPublishPaths] = useState<string[]>([]);
  const binding = JSON.stringify([card.id, card.config.runtime, card.config.workspace_path, card.config.workspace_access,
    info?.runtime_id, info?.workspace_path, info?.workspace_access, info?.workspace]);
  const fileContext = useRef({ binding, live: true, generation: 0, requests: new Map<string, number>() });
  if (fileContext.current.binding !== binding) {
    fileContext.current.live = false;
    fileContext.current = { binding, live: true, generation: 0, requests: new Map() };
  }
  const context = fileContext.current;
  function fileRequest(key: string) {
    const generation = context.generation;
    const sequence = (context.requests.get(key) ?? 0) + 1;
    context.requests.set(key, sequence);
    return () => context.live && context.generation === generation && fileContext.current === context && context.requests.get(key) === sequence;
  }
  const [notice, setNotice] = useState("");
  const [destination, setDestination] = useState("");
  const [diagnosticBusy, setDiagnosticBusy] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [skill, setSkill] = useState("");
  const [bundle, setBundle] = useState<Bundle>();
  const [resource, setResource] = useState("");
  const [copyPath, setCopyPath] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const fileQuery = (operation: string, root: string, path: string) => `files?${new URLSearchParams({ operation, root, path })}`;
  const running = history.find(h => h.state === "running");
  const occupied = !!busy || !!running || card.status === "running" || diagnosticBusy;
  const ready = info?.state === "ready" && !occupied;
  const latest = history.at(-1);
  const output = Array.isArray(card.config.output) && card.config.output.length
    ? card.config.output.map(String).slice(-250).join("\n")
    : latest?.error || [latest?.stdout, latest?.stderr].filter(Boolean).join("\n");
  async function refreshHistory() { setHistory(await worldApi.sandboxWorkspace<Receipt[]>(card.id, "history")); }
  async function refreshFiles() {
    const current = fileRequest("roots");
    setLoading(s => ({ ...s, roots: true }));
    try {
      const value = await worldApi.sandboxWorkspace<Root[]>(card.id, "files");
      if (!current()) return;
      if (!Array.isArray(value)) throw new Error((value as Files).message ?? "Workspace unavailable");
      const open = { ...expandedRef.current };
      if (!("workspace:" in open) && value.some(root => root.id === "workspace" && root.directory)) open["workspace:"] = true;
      const directories = value.flatMap(root => Object.entries(open)
        .filter(([key, visible]) => visible && root.directory && key.startsWith(`${root.id}:`))
        .map(([key]) => ({ key, root: root.id, path: key.slice(root.id.length + 1) })));
      setRoots(value);
      setTree(tree => Object.fromEntries(Object.entries(tree).filter(([key]) => expandedRef.current[key]
        && value.some(root => key.startsWith(`${root.id}:`)))));
      setExpanded(current => ({ ...open, ...current })); setFilesError("");
      await Promise.all(directories.map(({ root, path }) => loadDirectory(root, path)));
    } catch (e) { if (current()) { setFilesError(apiErrorMessage(e)); setRoots([]); setTree({}); } }
    finally { if (current()) setLoading(s => ({ ...s, roots: false })); }
  }
  useEffect(() => {
    context.live = true;
    setRoots([]); setTree({}); setLoading({}); setFilesError("");
    setSelection(undefined); setPreview(undefined); expandedRef.current = {}; setExpanded({});
    setPublishPaths([]);
    void refreshFiles();
    return () => { context.live = false; context.generation++; context.requests.clear(); };
  }, [context]);
  useEffect(() => { void loadRuntimes(); void refreshSandbox(card.id); void refreshHistory().catch(e => setError(apiErrorMessage(e))); }, [card.id, socket]);
  const previousStatus = useRef(card.status);
  useEffect(() => {
    const becameReady = previousStatus.current !== "ready" && card.status === "ready";
    previousStatus.current = card.status;
    if (becameReady) void refreshFiles();
  }, [card.status]);
  useEffect(() => { void refreshHistory().catch(e => setError(apiErrorMessage(e))); }, [card.status, busy]);
  // Poll receipts only while a command is active, never the file tree.
  useEffect(() => {
    if (!running && card.status !== "running") return;
    const timer = window.setInterval(() => { void refreshHistory().catch(() => {}); void refreshSandbox(card.id); }, 2000);
    return () => window.clearInterval(timer);
  }, [!!running, card.status, card.id]);
  async function expand(root: string, path: string) {
    const key = `${root}:${path}`;
    if (expanded[key]) { setExpanded(s => ({ ...s, [key]: false })); return; }
    setExpanded(s => ({ ...s, [key]: true }));
    if (tree[key]) return;
    await loadDirectory(root, path);
  }
  async function loadDirectory(root: string, path: string) {
    const key = `${root}:${path}`, current = fileRequest(`directory:${key}`);
    setLoading(s => ({ ...s, [key]: true }));
    try { const value = await worldApi.sandboxWorkspace<Files>(card.id, fileQuery("list", root, path)); if (current()) setTree(s => ({ ...s, [key]: value })); }
    catch (e) { if (current()) setTree(s => ({ ...s, [key]: { state: "permission_denied", message: apiErrorMessage(e) } })); }
    finally { if (current()) setLoading(s => ({ ...s, [key]: false })); }
  }
  async function select(root: string, path: string, label: string) {
    const current = fileRequest("preview");
    setSelection({ root, path, label }); setPreview(undefined);
    try {
      const value = await worldApi.sandboxWorkspace<Files>(card.id, fileQuery("preview", root, path));
      if (current()) setPreview(value);
    } catch (e) { if (current()) setPreview({ state: "permission_denied", message: apiErrorMessage(e) }); }
  }
  function directory(root: string, path: string): React.ReactNode {
    const key = `${root}:${path}`, value = tree[key];
    if (!expanded[key]) return null;
    return <ul>{loading[key] && !value ? <li className="sandbox-tree-note">Loading…</li> : value?.state ? <li className="sandbox-tree-note">{value.message ?? value.state}</li> : <>
      {value?.entries?.length === 0 && <li className="sandbox-tree-note">Empty folder</li>}
      {value?.entries?.map(e => { const next = path ? `${path}/${e.name}` : e.name; return <li key={e.name}>
        {root === "workspace" && !e.blocked && <input type="checkbox" aria-label={`Select ${next} for publication`} checked={publishPaths.includes(next)}
          onChange={event => setPublishPaths(current => event.target.checked ? [...current, next] : current.filter(p => p !== next))} />}
        <button className="sandbox-tree-entry" disabled={e.blocked} title={e.blocked ? "Links are blocked" : next}
          aria-expanded={e.directory ? !!expanded[`${root}:${next}`] : undefined}
          aria-current={!e.directory && selection?.root === root && selection.path === next ? "true" : undefined}
          onClick={() => void (e.directory ? expand(root, next) : select(root, next, e.name))}>
          {e.directory ? <>{expanded[`${root}:${next}`] ? <ChevronDown size={11} /> : <ChevronRight size={11} />}<Folder size={13} /></> : <FileText size={13} />}
          <span>{e.name}</span>
        </button>{e.directory && directory(root, next)}</li>; })}
      {value?.truncated && <li className="sandbox-tree-note">First 300 entries shown.</li>}
    </>}</ul>;
  }
  async function action(name: string, body: unknown = {}) {
    setError("");
    try {
      await worldApi.sandboxWorkspace(card.id, name, body);
      await refreshSandbox(card.id); await refreshHistory();
    }
    catch (e) { setError(apiErrorMessage(e)); }
  }
  async function diagnose(connect = false) {
    setDiagnosticBusy(true); setError("");
    try {
      const result = await worldApi.sandboxWorkspace<{ status: string; stdout: string; stderr: string; network_reason: string; workspace_access?: string }>(card.id, "diagnostics", { destination: connect ? destination : null });
      setNotice(`${result.status}\n${result.stdout}\n${result.stderr}\nWorkspace access: ${result.workspace_access ?? info?.workspace_access}\n${result.network_reason}`);
      await refreshHistory(); await refreshSandbox(card.id);
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setDiagnosticBusy(false); }
  }
  async function run() {
    if (!ready || !draft.trim()) return;
    setError("");
    try { await execute(card.id, draft.trim()); await refreshHistory(); await refreshFiles(); }
    catch (e) { setError(apiErrorMessage(e)); }
  }
  async function download() {
    if (!selection) return;
    try {
      const blob = await worldApi.downloadSandboxFile(card.id, selection.root, selection.path);
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = selection.label; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(apiErrorMessage(e)); }
  }
  async function savePreset() {
    try {
      const saved = await worldApi.updateNode(card.id, { config: { presets: { ...(card.config.presets as Record<string, string> ?? {}), [presetName.trim()]: draft } } });
      useWorldStore.setState(s => ({ cards: s.cards.map(c => c.id === card.id ? saved : c) }));
      setPresetName("");
    } catch (e) { setError(apiErrorMessage(e)); }
  }
  useEffect(() => {
    setBundle(undefined); setResource("");
    if (skill) { let live = true; void worldApi.sandboxWorkspace<Bundle>(card.id, `skills/${encodeURIComponent(skill)}`).then(b => { if (live) setBundle(b); }).catch(e => setError(apiErrorMessage(e))); return () => { live = false; }; }
  }, [skill, card.id, history.length]);
  return <div className="sandbox-workspace nodrag nopan nowheel">
    <header className="sandbox-window-toolbar">
      <nav className="sandbox-tabs" role="tablist" aria-label="Sandbox window" onKeyDown={tabKeys}>
        {(["workspace", "settings"] as const).map(view => <button key={view} role="tab" id={`${card.id}-${view}-tab`}
          aria-selected={tab === view} aria-controls={`${card.id}-${view}-panel`} tabIndex={tab === view ? 0 : -1}
          onClick={() => setTab(view)}>
          {view === "workspace" ? <LayoutPanelLeft size={13} /> : <Settings size={13} />}
          {view === "workspace" ? "Workspace" : "Settings"}{view === "settings" && settingsDirty && <span className="sandbox-dirty-dot" aria-label="Unsaved changes" />}
        </button>)}
      </nav>
      <SandboxRuntimeControls card={card} disabled={settingsDirty || diagnosticBusy} />
    </header>
    {(error || (tab === "workspace" && runtimeError)) && <p className="sandbox-workspace-error" role="alert">{error || runtimeError}</p>}
    <div className="sandbox-workbench" role="tabpanel" id={`${card.id}-workspace-panel`} aria-labelledby={`${card.id}-workspace-tab`} hidden={tab !== "workspace"}>
      <aside ref={sidebarElement} className="sandbox-files" aria-label="Sandbox files" style={{ width: sidebarWidth }}>
        <header className="sandbox-pane-heading"><span><FolderOpen size={13} /> Files</span>
          <IconButton icon={RefreshCw} size="xs" quiet label="Refresh files" disabled={loading.roots} onClick={() => { void refreshFiles(); if (selection) void select(selection.root, selection.path, selection.label); }} />
        </header>
        <div className="sandbox-tree-scroll">
          {loading.roots && !roots.length && <p className="sandbox-tree-note">Loading files…</p>}
          {filesError && <p className="sandbox-tree-note" role="alert">{filesError}</p>}
          {!loading.roots && !roots.length && !filesError && <p className="sandbox-tree-note">Start the sandbox to browse files.</p>}
          {roots.map(root => <section className="sandbox-file-root" key={root.id}>
            <button className="sandbox-tree-entry sandbox-root-entry" aria-expanded={root.directory ? !!expanded[`${root.id}:`] : undefined}
              title={root.id === "workspace" ? info?.workspace_path ?? info?.workspace ?? "Managed workspace" : root.label}
              onClick={() => void (root.directory ? expand(root.id, "") : select(root.id, "", root.label))}>
              {root.directory ? <>{expanded[`${root.id}:`] ? <ChevronDown size={11} /> : <ChevronRight size={11} />}<Folder size={13} /></> : <File size={13} />}
              <span>{root.label}</span>{root.access === "read_only" && <small>Read only</small>}
            </button>
            {root.directory && directory(root.id, "")}
          </section>)}
        </div>
        <footer className="sandbox-files-footer" title={info?.workspace_path ?? info?.workspace ?? "Managed workspace"}>
          <Folder size={11} /><span>{info?.workspace_path ?? info?.workspace ?? "Managed workspace"}</span>
        </footer>
      </aside>
      <div className="sandbox-file-divider" role="separator" aria-label="Resize file sidebar" aria-orientation="vertical"
        aria-valuemin={180} aria-valuemax={420} aria-valuenow={sidebarWidth} tabIndex={0}
        onPointerDown={e => {
          e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId);
          const element = sidebarElement.current!;
          resizing.current = { x: e.clientX, width: element.offsetWidth, scale: element.getBoundingClientRect().width / element.offsetWidth };
        }}
        onPointerMove={e => { if (resizing.current) setSidebarWidth(boundedSize(resizing.current.width + (e.clientX - resizing.current.x) / resizing.current.scale, 180, 420, 224)); }}
        onPointerUp={e => { if (resizing.current) saveSidebarWidth(resizing.current.width + (e.clientX - resizing.current.x) / resizing.current.scale); resizing.current = undefined; e.currentTarget.releasePointerCapture(e.pointerId); }}
        onPointerCancel={() => { resizing.current = undefined; }}
        onKeyDown={e => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); saveSidebarWidth(sidebarWidth + (e.key === "ArrowRight" ? 16 : -16)); } }} />
      <main ref={workArea} className="sandbox-work-area">
        <section className="sandbox-preview" aria-label="File preview">
          <PublishFiles card={card} paths={publishPaths.length ? publishPaths : selection?.root === "workspace" ? [selection.path] : []} />
          <header className="sandbox-pane-heading">
            <span title={selection?.path}><FileText size={13} />{selection?.label ?? "File preview"}</span>
            {selection && <div className="sandbox-pane-actions">
              {selection.root.startsWith("resource:") && <button className="secondary-button" onClick={() => useNodeSurfaceStore.getState().openInspector(selection.root.slice(9))}>Edit resource</button>}
              <IconButton icon={Download} size="xs" quiet label="Download file" title="Download file (up to 16 MiB)" onClick={() => void download()} />
            </div>}
          </header>
          {selection ? <>
            <div className="sandbox-preview-path" title={selection.path}>{selection.root === "workspace" ? "Workspace" : "Resource"} / {selection.path || selection.label}</div>
            <div className="sandbox-preview-content">
              {!preview ? <div className="sandbox-pane-empty">Loading preview…</div>
                : preview.state === "text" ? <pre>{preview.text || "Empty file"}</pre>
                : preview.state === "image" ? <img alt={selection.label} src={`data:${preview.media_type};base64,${preview.data}`} />
                : <div className="sandbox-pane-empty">{preview.message ?? ({ oversized: "Preview exceeds 1 MiB. Download to view.", unsupported: "Preview unsupported. Download to open locally.", missing: "File no longer exists.", permission_denied: "Permission denied." }[preview.state ?? ""] ?? preview.state)}</div>}
            </div>
          </> : <div className="sandbox-pane-empty"><FileText size={26} strokeWidth={1.2} /><span>Select a file to preview</span></div>}
        </section>
        <div className="sandbox-terminal-divider" role="separator" aria-label="Resize terminal" aria-orientation="horizontal"
          aria-valuemin={28} aria-valuemax={65} aria-valuenow={terminalHeight} aria-valuetext={`${Math.round(terminalHeight)}% terminal height`} tabIndex={0}
          onPointerDown={e => {
            e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId);
            terminalResize.current = { y: e.clientY, height: terminalHeight, area: workArea.current!.getBoundingClientRect().height };
          }}
          onPointerMove={e => { if (terminalResize.current) setTerminalHeight(boundedSize(terminalResize.current.height + (terminalResize.current.y - e.clientY) / terminalResize.current.area * 100, 28, 65, 42)); }}
          onPointerUp={e => { if (terminalResize.current) saveTerminalHeight(terminalResize.current.height + (terminalResize.current.y - e.clientY) / terminalResize.current.area * 100); terminalResize.current = undefined; e.currentTarget.releasePointerCapture(e.pointerId); }}
          onPointerCancel={() => { terminalResize.current = undefined; }}
          onKeyDown={e => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); saveTerminalHeight(terminalHeight + (e.key === "ArrowUp" ? 4 : -4)); } }} />
        <section className="sandbox-terminal" aria-label="Sandbox terminal" style={{ flexBasis: `${terminalHeight}%` }}>
          <header className="sandbox-pane-heading">
            <nav className="sandbox-tabs" role="tablist" aria-label="Terminal views" onKeyDown={tabKeys}>
              {(["terminal", "history"] as const).map(view => <button key={view} role="tab" id={`${card.id}-${view}-tab`}
                aria-selected={terminalTab === view} aria-controls={`${card.id}-${view}-panel`} tabIndex={terminalTab === view ? 0 : -1}
                onClick={() => setTerminalTab(view)}>{view === "terminal" ? <Terminal size={12} /> : <History size={12} />}{view === "terminal" ? "Terminal" : "History"}</button>)}
            </nav>
            <div className="sandbox-pane-actions">
              <span className="sandbox-shell-hint" title="Non-interactive commands. Each command starts in the working folder; cd and export do not persist. Interactive prompts are unsupported. Closing this window leaves execution running.">Non-interactive</span>
              {(running || card.status === "running") && <IconButton icon={Square} size="xs" quiet label="Cancel command" onClick={() => void action("cancel")} />}
              {terminalTab === "history" && <IconButton icon={RefreshCw} size="xs" quiet label="Refresh history" onClick={() => void refreshHistory().catch(e => setError(apiErrorMessage(e)))} />}
            </div>
          </header>
          <div className="sandbox-console" role="tabpanel" id={`${card.id}-terminal-panel`} aria-labelledby={`${card.id}-terminal-tab`} hidden={terminalTab !== "terminal"}>
            <div className="sandbox-terminal-output" role="log" aria-live="polite" aria-label="Command output">
              {output ? <pre>{output}</pre> : <span>{ready ? "Ready for a command." : occupied ? "Command running…" : "Start the sandbox to run commands."}</span>}
            </div>
            {(running || latest) && <div className="sandbox-command-meta">
              <span title={running?.argv.join(" ")}>{running ? `${running.caller} · running` : `${latest!.state} · exit ${latest!.exit_code ?? "—"}`}</span>
              {!running && latest?.duration_seconds !== undefined && <span>{latest.duration_seconds.toFixed(2)}s</span>}
            </div>}
            <form className="sandbox-command-form" onSubmit={e => { e.preventDefault(); void run(); }}>
              <span className="sandbox-prompt" aria-hidden="true">›</span>
              <textarea aria-label="Command" placeholder="Enter a command…" rows={2} spellCheck={false} value={draft} onChange={e => setDraft(e.target.value)}
                onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); e.stopPropagation(); void run(); } }} />
              <button className="primary-button" type="submit" aria-label="Run command" title="Run command (Ctrl/⌘ + Enter)" disabled={!ready || !draft.trim()}><Play size={12} /> Run</button>
              <div className="sandbox-command-options">
                <select aria-label="Command history" value="" disabled={!history.length} onChange={e => setDraft(e.target.value)}><option value="">Recall command</option>{history.map(h => <option key={h.id} value={h.argv.at(-1)}>{h.argv.join(" ")}</option>)}</select>
                {Object.keys(card.config.presets as Record<string, string> ?? {}).length > 0 && <select aria-label="Command preset" value="" onChange={e => setDraft(e.target.value)}><option value="">Load preset</option>{Object.entries(card.config.presets as Record<string, string> ?? {}).map(([name, command]) => <option key={name} value={command}>{name}</option>)}</select>}
                <span>Ctrl/⌘ + Enter</span>
              </div>
            </form>
          </div>
          <div className="sandbox-history" role="tabpanel" id={`${card.id}-history-panel`} aria-labelledby={`${card.id}-history-tab`} hidden={terminalTab !== "history"}>
            {!history.length && <div className="sandbox-pane-empty">No executions yet.</div>}
            {[...history].reverse().map(h => <article key={h.id}><header><strong>{h.caller} · {h.state}</strong><small>Exit {h.exit_code ?? "—"} · {h.duration_seconds?.toFixed(2) ?? "—"}s</small></header>
              <code>{h.argv.join(" ")}</code><pre>{h.error || `${h.stdout ?? ""}${h.stderr ?? ""}`}</pre></article>)}
          </div>
        </section>
      </main>
    </div>
    <div className="sandbox-settings-window" role="tabpanel" id={`${card.id}-settings-panel`} aria-labelledby={`${card.id}-settings-tab`} hidden={tab !== "settings"}>
      <div className="sandbox-settings-content">
        <SandboxSettings card={card} onDirtyChange={setSettingsDirty} />
        <details className="sandbox-settings"><summary>Command presets</summary><div className="sandbox-config-form">
          <p className="sandbox-help">Save the current command for reuse.</p>
          <label className="field-label">Preset name<input value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="e.g. Run tests" /></label>
          {draft.trim() && <pre className="sandbox-preset-preview">{draft}</pre>}
          <div className="editor-actions"><button className="secondary-button" disabled={!presetName.trim() || !draft.trim()} onClick={() => void savePreset()}>Save preset</button></div>
        </div></details>
        <details className="sandbox-settings"><summary>Skill resources</summary><div className="sandbox-config-form">
          <label className="field-label">Skill<select aria-label="Selected Skill" value={skill} onChange={e => setSkill(e.target.value)}><option value="">Select a Skill</option>
            {cards.filter(c => c.type === "oaw.skills.skill").map(c => <option value={c.id} key={c.id}>{c.name}</option>)}</select></label>
          {bundle && <><p className="sandbox-help" title={bundle.note}>{bundle.status} · r{bundle.revision} · {bundle.cached ? (bundle.current ? "Cached" : "Older cache") : "Not cached"}</p>
            <label className="field-label">Resource<select aria-label="Skill resource" value={resource} onChange={e => setResource(e.target.value)}><option value="">Choose a resource</option>{bundle.files.map(f => <option key={f}>{f}</option>)}</select></label>
            <label className="field-label">Copy destination<input placeholder="Workspace relative path" value={copyPath} onChange={e => setCopyPath(e.target.value)} /></label>
            <label className="sandbox-checkbox"><input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} /> Overwrite existing file</label>
            <div className="editor-actions"><button className="secondary-button" disabled={!resource || !copyPath || info?.workspace_access === "read_only"} onClick={() => void action("copy-skill-resource", { skill_id: skill, source: resource, destination: copyPath, overwrite }).then(() => refreshFiles())}>Copy into workspace</button></div>
          </>}
        </div></details>
        <details className="sandbox-settings"><summary>Diagnostics</summary><div className="sandbox-config-form">
          <div className="editor-actions"><button className="secondary-button" disabled={!ready} onClick={() => void diagnose()}>Check execution environment</button></div>
          <label className="field-label">Connectivity destination<input placeholder="https://example.com" value={destination} onChange={e => setDestination(e.target.value)} /></label>
          <div className="editor-actions"><button className="secondary-button" disabled={!ready || !destination || !info?.network_enabled} onClick={() => void diagnose(true)}>Test connectivity</button></div>
          {diagnosticBusy && <p className="sandbox-help">Checking…</p>}{notice && <pre className="sandbox-diagnostic-output">{notice}</pre>}
        </div></details>
      </div>
    </div>
  </div>;
}
