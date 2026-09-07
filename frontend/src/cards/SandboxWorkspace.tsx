import { useEffect, useRef, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import type { WorldCard } from "../types/world";
import { InstrumentOutput } from "./CardUtilities";
import "./sandboxWorkspace.css";

interface Root { id: string; label: string; access: string; directory: boolean }
interface Entry { name: string; directory: boolean; blocked: boolean; size: number }
interface Files { entries?: Entry[]; truncated?: boolean; state?: string; text?: string; data?: string; media_type?: string; message?: string }
interface Receipt { id: string; caller: string; state: string; argv: string[]; stdout?: string; stderr?: string; error?: string; exit_code?: number; duration_seconds?: number; skill_id?: string }
interface Bundle { cached?: boolean; current?: boolean; revision: number; files: string[]; status: string; note: string }

export function SandboxWorkspace({ card }: { card: WorldCard }) {
  const refreshSandbox = useWorldStore(s => s.refreshSandbox);
  const info = useWorldStore(s => s.sandboxInfo[card.id]);
  const execute = useWorldStore(s => s.executeSandbox);
  const busy = useWorldStore(s => s.sandboxBusy[card.id]);
  const runtimeError = useWorldStore(s => s.sandboxErrors[card.id]);
  const socket = useWorldStore(s => s.socketState);
  const cards = useWorldStore(s => s.cards);
  const draft = useNodeSurfaceStore(s => s.drafts[`sandbox:${card.id}`] ?? "");
  const setDraft = (value: string) => useNodeSurfaceStore.getState().setDraft(`sandbox:${card.id}`, value);
  const [sidebarWidth, setSidebarWidth] = useState(() => Number(useNodeSurfaceStore.getState().drafts[`sandbox-sidebar:${card.id}`] ?? 240));
  const sidebarElement = useRef<HTMLElement>(null);
  const resizing = useRef<{ x: number; width: number; scale: number }>();
  const saveSidebarWidth = (width: number) => {
    const bounded = Math.max(180, Math.min(420, width));
    setSidebarWidth(bounded);
    useNodeSurfaceStore.getState().setDraft(`sandbox-sidebar:${card.id}`, String(bounded));
  };
  const [roots, setRoots] = useState<Root[]>([]);
  const [tree, setTree] = useState<Record<string, Files>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [tab, setTab] = useState("console");
  const [selection, setSelection] = useState<{ root: string; path: string; label: string }>();
  const [preview, setPreview] = useState<Files>();
  const previewRequest = useRef(0);
  const [history, setHistory] = useState<Receipt[]>([]);
  const [error, setError] = useState("");
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
  async function refreshHistory() { setHistory(await worldApi.sandboxWorkspace<Receipt[]>(card.id, "history")); }
  async function refreshFiles() {
    setLoading(s => ({ ...s, roots: true }));
    try {
      const value = await worldApi.sandboxWorkspace<Root[]>(card.id, "files");
      if (!Array.isArray(value)) throw new Error((value as Files).message ?? "Workspace unavailable");
      setRoots(value); setTree({}); setExpanded({}); setError("");
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setLoading(s => ({ ...s, roots: false })); }
  }
  useEffect(() => { void refreshSandbox(card.id); void refreshFiles(); void refreshHistory().catch(e => setError(apiErrorMessage(e))); }, [card.id, socket]);
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
    setLoading(s => ({ ...s, [key]: true }));
    try { const value = await worldApi.sandboxWorkspace<Files>(card.id, fileQuery("list", root, path)); setTree(s => ({ ...s, [key]: value })); }
    catch (e) { setTree(s => ({ ...s, [key]: { state: "permission_denied", message: apiErrorMessage(e) } })); }
    finally { setLoading(s => ({ ...s, [key]: false })); }
  }
  async function select(root: string, path: string, label: string) {
    const request = ++previewRequest.current;
    setSelection({ root, path, label }); setTab("preview"); setPreview(undefined);
    try {
      const value = await worldApi.sandboxWorkspace<Files>(card.id, fileQuery("preview", root, path));
      if (request === previewRequest.current) setPreview(value);
    } catch (e) { if (request === previewRequest.current) setPreview({ state: "permission_denied", message: apiErrorMessage(e) }); }
  }
  function directory(root: string, path: string): React.ReactNode {
    const key = `${root}:${path}`, value = tree[key];
    if (!expanded[key]) return null;
    return <ul>{loading[key] ? <li>Loading…</li> : value?.state ? <li>{value.message ?? value.state}</li> : <>
      {value?.entries?.length === 0 && <li>Empty directory</li>}
      {value?.entries?.map(e => { const next = path ? `${path}/${e.name}` : e.name; return <li key={e.name}>
        <button disabled={e.blocked} title={e.blocked ? "Links are blocked" : e.name} onClick={() => void (e.directory ? expand(root, next) : select(root, next, e.name))}>
          {e.directory ? (expanded[`${root}:${next}`] ? "▾ " : "▸ ") : "· "}{e.name}{e.blocked ? " (blocked link)" : ""}
        </button>{e.directory && directory(root, next)}</li>; })}
      {value?.truncated && <li>Showing the first 300 entries. Narrow the folder outside this browser to see more.</li>}
    </>}</ul>;
  }
  async function action(name: string, body: unknown = {}) {
    setError("");
    try {
      if (name === "start") await useWorldStore.getState().startSandbox(card.id);
      else if (name === "stop") await useWorldStore.getState().stopSandbox(card.id);
      else await worldApi.sandboxWorkspace(card.id, name, body);
      await refreshSandbox(card.id); await refreshHistory();
    }
    catch (e) { setError(apiErrorMessage(e)); }
  }
  async function diagnose(connect = false) {
    setDiagnosticBusy(true); setTab("console"); setError("");
    try {
      const result = await worldApi.sandboxWorkspace<{ status: string; stdout: string; stderr: string; network_reason: string; workspace_access?: string }>(card.id, "diagnostics", { destination: connect ? destination : null });
      setNotice(`${result.status}\n${result.stdout}\n${result.stderr}\nWorkspace access: ${result.workspace_access ?? info?.workspace_access}\n${result.network_reason}`);
      await refreshHistory(); await refreshSandbox(card.id);
    } catch (e) { setError(apiErrorMessage(e)); }
    finally { setDiagnosticBusy(false); }
  }
  async function run() { if (ready && draft.trim()) { await execute(card.id, draft.trim()); await refreshHistory(); } }
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
    <aside ref={sidebarElement} className="sandbox-files" aria-label="Sandbox files" style={{ width: sidebarWidth }}>
      <div className="section-heading"><strong>Files</strong><button onClick={() => void refreshFiles()} disabled={loading.roots}>Refresh files</button></div>
      {loading.roots && <p>Loading roots…</p>}
      {roots.map(root => <section key={root.id}><strong>{root.id === "workspace" ? "Workspace" : "Attached resource"}</strong>
        {root.id === "workspace" && <small>{info?.workspace_path ?? info?.workspace ?? "Managed workspace"}</small>}
        <small>{root.access === "read_only" ? "Read only" : "Read & write"}</small>
        <button onClick={() => void (root.directory ? expand(root.id, "") : select(root.id, "", root.label))}>{root.directory ? "▸ " : ""}{root.label}</button>
        {root.directory && directory(root.id, "")}</section>)}
      <section><strong>Skill bundles</strong><small>Host-managed · read only</small>
        <select aria-label="Selected Skill" value={skill} onChange={e => setSkill(e.target.value)}><option value="">Select a Skill</option>
          {cards.filter(c => c.type === "oaw.skills.skill").map(c => <option value={c.id} key={c.id}>{c.name}</option>)}</select>
        {bundle && <><p>{bundle.status} · revision {bundle.revision}<br />{bundle.cached ? (bundle.current ? "Current revision cached" : "Older revision cached") : "Not materialized"}</p><p>{bundle.note}</p>
          <select aria-label="Skill resource" value={resource} onChange={e => setResource(e.target.value)}><option value="">Choose resource to copy</option>{bundle.files.map(f => <option key={f}>{f}</option>)}</select>
          <input aria-label="Copy destination" placeholder="Workspace relative path" value={copyPath} onChange={e => setCopyPath(e.target.value)} />
          <label><input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} /> Overwrite existing file</label>
          <button disabled={!resource || !copyPath || info?.workspace_access === "read_only"} onClick={() => void action("copy-skill-resource", { skill_id: skill, source: resource, destination: copyPath, overwrite }).then(() => refreshFiles())}>Copy into workspace</button>
        </>}
      </section>
    </aside>
    <div className="sandbox-file-divider" role="separator" aria-label="Resize file sidebar" aria-orientation="vertical"
      aria-valuemin={180} aria-valuemax={420} aria-valuenow={sidebarWidth} tabIndex={0}
      onPointerDown={e => {
        e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId);
        const element = sidebarElement.current!;
        resizing.current = { x: e.clientX, width: element.offsetWidth, scale: element.getBoundingClientRect().width / element.offsetWidth };
      }}
      onPointerMove={e => { if (resizing.current) setSidebarWidth(Math.max(180, Math.min(420, resizing.current.width + (e.clientX - resizing.current.x) / resizing.current.scale))); }}
      onPointerUp={e => { if (resizing.current) { resizing.current = undefined; e.currentTarget.releasePointerCapture(e.pointerId); saveSidebarWidth(sidebarWidth); } }}
      onPointerCancel={() => { resizing.current = undefined; }}
      onKeyDown={e => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); saveSidebarWidth(sidebarWidth + (e.key === "ArrowRight" ? 16 : -16)); } }} />
    <main className="sandbox-work-area">
      <div className="sandbox-window-toolbar"><span>{info?.runtime_id} · {card.status} · Network {info?.network_enabled ? "enabled" : "disabled"}</span>
        <button onClick={() => useNodeSurfaceStore.getState().openInspector(card.id)}>Settings</button>
        <button disabled={occupied || info?.state !== "stopped"} onClick={() => void action("start")}>Start</button>
        <button disabled={!running && card.status !== "running"} onClick={() => void action("cancel")}>Cancel command</button>
        <button disabled={card.status === "stopped"} onClick={() => void action("stop")}>Stop Sandbox</button></div>
      <nav className="agent-window-tabs" aria-label="Sandbox workspace views">{["console", "preview", "history"].map(t => <button key={t} aria-pressed={tab === t} onClick={() => setTab(t)}>{t === "preview" ? "File preview" : t === "console" ? "Console" : "History"}</button>)}</nav>
      {(error || runtimeError) && <p role="alert">{error || runtimeError}</p>}
      <div className="sandbox-console" hidden={tab !== "console"}>
        <p>Non-interactive commands. Each command starts in the configured folder; cd and export do not persist. Commands requiring prompts or a terminal are unsupported. Closing this window leaves execution running.</p>
        <p>Current caller: {running?.caller ?? "none"}{running ? ` · ${running.argv.join(" ")}` : ""}</p>
        <label>Command<textarea aria-label="Command" rows={5} spellCheck={false} value={draft} onChange={e => setDraft(e.target.value)} /></label>
        <div className="editor-actions"><button className="primary-button" disabled={!ready || !draft.trim()} onClick={() => void run()}>Run command</button>
          <select aria-label="Command history" value="" onChange={e => setDraft(e.target.value)}><option value="">Recall command</option>{history.map(h => <option key={h.id} value={h.argv.at(-1)}>{h.argv.join(" ")}</option>)}</select>
          <select aria-label="Command preset" value="" onChange={e => setDraft(e.target.value)}><option value="">Load preset</option>{Object.entries(card.config.presets as Record<string, string> ?? {}).map(([name, command]) => <option key={name} value={command}>{name}</option>)}</select></div>
        <div className="editor-actions"><input aria-label="Preset name" value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="Preset name" /><button disabled={!presetName.trim() || !draft.trim()} onClick={() => void savePreset()}>Save preset</button></div>
        <small>Presets never run automatically. Store secrets as environment references, never in command text.</small>
        <div className="sandbox-diagnostics"><button disabled={!ready} onClick={() => void diagnose()}>Check execution environment</button>
          <input aria-label="Connectivity destination" placeholder="https://example.com" value={destination} onChange={e => setDestination(e.target.value)} />
          <button disabled={!ready || !destination || !info?.network_enabled} onClick={() => void diagnose(true)}>Test connectivity</button></div>
        {diagnosticBusy && <p>Checking through the selected Sandbox…</p>}{notice && <pre>{notice}</pre>}
        <InstrumentOutput lines={Array.isArray(card.config.output) && card.config.output.length ? card.config.output.map(String).slice(-250) : [history.at(-1)?.stdout ?? "", history.at(-1)?.stderr ?? ""]} empty="Command output appears here." />
        {history.at(-1) && <p>Last command: {history.at(-1)?.state} · exit {history.at(-1)?.exit_code ?? "—"} · {history.at(-1)?.duration_seconds?.toFixed(2) ?? "—"}s</p>}
      </div>
      <div className="sandbox-preview" hidden={tab !== "preview"}>
        <strong>{selection?.label ?? "Select a file in the sidebar"}</strong>
        {selection && <><button onClick={() => void download()}>Download (up to 16 MiB)</button>
          {selection.root.startsWith("resource:") && <button onClick={() => useNodeSurfaceStore.getState().openInspector(selection.root.slice(9))}>Open resource editor</button>}
          {!preview ? <p>Loading preview…</p> : preview.state === "text" ? <pre>{preview.text || "Empty file"}</pre> : preview.state === "image" ? <img alt={selection.label} src={`data:${preview.media_type};base64,${preview.data}`} /> : <p>{preview.message ?? ({ oversized: "Preview exceeds 1 MiB. Download the file if it is within 16 MiB.", unsupported: "Preview unsupported. Download to open locally.", missing: "File no longer exists.", permission_denied: "Permission denied." }[preview.state ?? ""] ?? preview.state)}</p>}</>}
      </div>
      <div className="sandbox-history" hidden={tab !== "history"}><button onClick={() => void refreshHistory()}>Refresh history</button>
        {!history.length && <p>No executions yet.</p>}{[...history].reverse().map(h => <article key={h.id}><strong>{h.caller} · {h.state}</strong><code>{h.argv.join(" ")}</code>
          <small>Exit {h.exit_code ?? "—"} · {h.duration_seconds?.toFixed(2) ?? "—"}s</small><pre>{h.error || `${h.stdout ?? ""}${h.stderr ?? ""}`}</pre></article>)}
        <small>Latest 20 commands; each output stream retains up to 64 KiB. Reload never resubmits commands.</small>
      </div>
    </main>
  </div>;
}
