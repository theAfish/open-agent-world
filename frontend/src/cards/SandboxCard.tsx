import { CircleStop, Folder, Play, RefreshCw, Settings2, SquareArrowOutUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { worldApi, apiErrorMessage } from "../api/client";
import { useNodeSurfaceStore, type NodeSurfaceLevel } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { SandboxWorkspaceAccess, WorldCard } from "../types/world";
import { FolderPathInput } from "../shell/FolderPathInput";
import { RelationshipList } from "./CardUtilities";
import { SandboxEnvironment } from "./SandboxEnvironment";

function useSandboxRuntime(card: WorldCard) {
  const info = useWorldStore((state) => state.sandboxInfo[card.id]);
  const busy = useWorldStore((state) => state.sandboxBusy[card.id]);
  const error = useWorldStore((state) => state.sandboxErrors[card.id]);
  const runtimes = useWorldStore((state) => state.sandboxRuntimes);
  const runtimesError = useWorldStore((state) => state.sandboxRuntimesError);
  const runtime = card.config.runtime ?? "auto";
  const selectedRuntime = runtimes?.runtimes.find((item) => item.id === (
    info?.runtime_locked ? info.runtime_id : runtime === "auto" ? runtimes.default_runtime : runtime
  ));
  const ready = card.status === "ready" || card.status === "running";
  const stopped = card.status === "stopped";
  const statusLabel = busy === "starting" ? "Starting…"
    : busy === "stopping" ? "Stopping…"
    : busy === "executing" || card.status === "running" ? "Command running"
    : !info ? "Status unavailable"
    : !info.available ? "Runtime unavailable"
    : card.status === "error" ? "Needs attention"
    : card.status === "ready" ? "Ready"
    : stopped ? "Stopped"
    : card.status;
  const network = info ?? selectedRuntime;
  const enabled = info?.network_enabled ?? Boolean(card.config.network_enabled);
  const networkLabel = !enabled ? "Disabled" : network?.network_status === "unsupported" ? "Requested · unsupported"
    : network?.network_status === "setup_failed" ? "Requested · setup failed"
    : !network?.network_available ? "Requested · prerequisites unavailable"
    : ready ? "Enabled · runtime ready" : "Requested · start to apply";
  const runtimeIssue = !info && selectedRuntime && !selectedRuntime.available ? selectedRuntime.reason : undefined;
  return {
    info, busy, selectedRuntime, network, networkLabel, ready, stopped, statusLabel,
    runtimeLabel: selectedRuntime?.label ?? info?.runtime_id ?? "Sandbox",
    issue: (enabled && network?.network_available === false ? network.network_reason : undefined)
      ?? error ?? info?.unavailable_reason ?? runtimeIssue ?? (!info ? runtimesError : undefined),
  };
}

export function SandboxRuntimeControls({ card, disabled = false }: { card: WorldCard; disabled?: boolean }) {
  const startSandbox = useWorldStore((state) => state.startSandbox);
  const stopSandbox = useWorldStore((state) => state.stopSandbox);
  const { info, busy, selectedRuntime, network, ready, stopped, statusLabel, runtimeLabel } = useSandboxRuntime(card);
  const canStop = ready || card.status === "error" || busy === "executing";
  const enabled = info?.network_enabled ?? Boolean(card.config.network_enabled);
  const supported = (network?.supported_network_modes ?? selectedRuntime?.supported_network_modes)?.includes("enabled");
  const retry = enabled && supported && (!network?.network_available || network.network_status === "setup_failed");
  return <div className="sandbox-status-panel">
    <div className="instrument-gauge" data-active={ready && info?.available === true} aria-hidden="true"><i /><i /><i /></div>
    <div><span>{runtimeLabel}</span><strong role="status">{statusLabel}</strong></div>
    <button type="button" className={canStop ? "secondary-button" : "primary-button"}
      disabled={canStop ? !!busy && busy !== "executing"
        : disabled || !!busy || !info?.available || !stopped || (enabled && !supported)}
      title={!canStop && disabled ? "Save or reset settings before starting" : undefined}
      onClick={() => void (canStop ? stopSandbox(card.id) : startSandbox(card.id))}>
      {canStop ? <CircleStop size={14} /> : <Play size={14} fill="currentColor" />}
      {canStop ? "Stop" : retry ? "Retry / Recheck" : "Start"}
    </button>
  </div>;
}

export function SandboxCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const loadRuntimes = useWorldStore((state) => state.loadSandboxRuntimes);
  const refreshSandbox = useWorldStore((state) => state.refreshSandbox);
  const socketState = useWorldStore((state) => state.socketState);
  const { info, issue, networkLabel } = useSandboxRuntime(card);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (level !== "inspector") return;
    void loadRuntimes();
    void refreshSandbox(card.id);
  }, [card.id, level, loadRuntimes, refreshSandbox, socketState]);
  const workspace = card.config.workspace_path ?? "Managed workspace";
  const openWindow = (tab: "workspace" | "settings") => {
    useNodeSurfaceStore.getState().setDraft(`sandbox-tab:${card.id}`, tab);
    useNodeSurfaceStore.getState().openWorkspace(card.id);
  };
  return <div className="expanded-stack sandbox-card-summary nodrag nowheel">
    <SandboxRuntimeControls card={card} disabled={dirty} />
    <dl className="sandbox-summary-list">
      <div><dt><Folder size={12} /> Folder</dt><dd title={workspace}>{workspace}</dd></div>
      <div><dt>Access</dt><dd>{(info?.workspace_access ?? card.config.workspace_access) === "read_only" ? "Read only" : "Read & write"}</dd></div>
      <div><dt>Network</dt><dd>{networkLabel}</dd></div>
    </dl>
    {card.config.active_command && <code className="sandbox-current-command" title={card.config.active_command}>{card.config.active_command}</code>}
    {issue && <p className="sandbox-error" role="alert">{issue}</p>}
    {level === "inspector" && <details className="sandbox-settings"><summary>Configuration</summary>
      <SandboxSettings card={card} compact onDirtyChange={setDirty} />
    </details>}
    <div className="action-row">
      <button type="button" className="primary-button" onClick={() => openWindow("workspace")}><SquareArrowOutUpRight size={14} /> Open Window</button>
      <button type="button" className="secondary-button" onClick={() => openWindow("settings")}><Settings2 size={14} /> Settings</button>
    </div>
  </div>;
}

export function SandboxSettings({ card, onDirtyChange, compact = false }: { card: WorldCard; onDirtyChange?: (dirty: boolean) => void; compact?: boolean }) {
  const edges = useWorldStore((state) => state.edges);
  const saveConfig = useWorldStore((state) => state.saveSandboxConfig);
  const refreshSandbox = useWorldStore((state) => state.refreshSandbox);
  const loadRuntimes = useWorldStore((state) => state.loadSandboxRuntimes);
  const runtimes = useWorldStore((state) => state.sandboxRuntimes);
  const runtimesLoading = useWorldStore((state) => state.sandboxRuntimesLoading);
  const socketState = useWorldStore((state) => state.socketState);
  const { info, busy, stopped, issue } = useSandboxRuntime(card);
  // Surface drafts are transient (not persisted), shared by inspector and Window.
  const draftKey = `sandbox-settings:${card.id}`;
  const rawDraft = useNodeSurfaceStore(s => s.drafts[draftKey] ?? "{}");
  const draft = JSON.parse(rawDraft || "{}") as Partial<{ network: boolean; memory: number; processes: number; timeout: number; runtime: string; workspace: string; access: SandboxWorkspaceAccess }>;
  const edit = (patch: typeof draft) => useNodeSurfaceStore.getState().setDraft(draftKey, JSON.stringify({ ...draft, ...patch }));
  const network = draft.network ?? Boolean(card.config.network_enabled), setNetwork = (network: boolean) => edit({ network });
  const memory = draft.memory ?? Number(card.config.memory_bytes ?? 536870912), setMemory = (memory: number) => edit({ memory });
  const processes = draft.processes ?? Number(card.config.active_process_limit ?? 16), setProcesses = (processes: number) => edit({ processes });
  const timeout = draft.timeout ?? Number(card.config.command_timeout ?? 60), setTimeoutValue = (timeout: number) => edit({ timeout });
  const [cacheMessage, setCacheMessage] = useState("");
  const [pickingFolder, setPickingFolder] = useState(false);
  const runtime = draft.runtime ?? card.config.runtime ?? "auto", setRuntime = (runtime: string) => edit({ runtime });
  const workspace = draft.workspace ?? card.config.workspace_path ?? "";
  const access = draft.access ?? card.config.workspace_access ?? "read_write", setAccess = (access: SandboxWorkspaceAccess) => edit({ access });

  useEffect(() => {
    void loadRuntimes();
    void refreshSandbox(card.id);
  }, [card.id, loadRuntimes, refreshSandbox, socketState]);

  const dirty = runtime !== (card.config.runtime ?? "auto")
    || (workspace.trim() || null) !== (card.config.workspace_path ?? null)
    || access !== (card.config.workspace_access ?? "read_write")
    || network !== Boolean(card.config.network_enabled) || memory !== Number(card.config.memory_bytes ?? 536870912)
    || processes !== Number(card.config.active_process_limit ?? 16) || timeout !== Number(card.config.command_timeout ?? 60);
  useEffect(() => { onDirtyChange?.(dirty || pickingFolder); }, [dirty, pickingFolder, onDirtyChange]);
  const canConfigure = stopped && !busy && !pickingFolder && !!info;
  const selectedRuntime = runtimes?.runtimes.find((item) => item.id === (
    info?.runtime_locked ? info.runtime_id : runtime === "auto" ? runtimes.default_runtime : runtime
  ));
  const connectionCount = edges.filter((edge) => edge.target === card.id || edge.source === card.id).length;
  const selectedRuntimeIssue = selectedRuntime && info?.runtime_id !== selectedRuntime.id && !selectedRuntime.available ? selectedRuntime.reason : undefined;
  const settingsIssue = issue ?? selectedRuntimeIssue;
  const selectedNetwork = info && info.network_status && (info.runtime_locked || runtime === (card.config.runtime ?? "auto")) ? info : selectedRuntime;

  return <div className="expanded-stack sandbox-controls sandbox-settings-page nodrag nowheel">
    <section className="sandbox-settings-section">
      <form className="sandbox-config-form" onSubmit={(event) => {
        event.preventDefault();
        if (canConfigure && dirty) void saveConfig(card.id, {
          runtime,
          workspace_path: workspace.trim() || null,
          workspace_access: workspace.trim() ? access : "read_write",
          ...(network !== Boolean(card.config.network_enabled) ? { network_enabled: network } : {}),
          ...(memory !== Number(card.config.memory_bytes ?? 536870912) ? { memory_bytes: memory } : {}),
          ...(processes !== Number(card.config.active_process_limit ?? 16) ? { active_process_limit: processes } : {}),
          ...(timeout !== Number(card.config.command_timeout ?? 60) ? { command_timeout: timeout } : {}),
        }).then(saved => { if (saved) useNodeSurfaceStore.getState().setDraft(draftKey, ""); });
      }}>
        <div className="section-heading">
          <span>Runtime & workspace</span>
          <button type="button" className="sandbox-refresh" aria-label="Refresh sandbox environment"
            disabled={!!busy || runtimesLoading}
            onClick={() => { void loadRuntimes(true).then(() => refreshSandbox(card.id, true)); }}>
            <RefreshCw size={12} /> {runtimesLoading ? "Checking…" : "Refresh"}
          </button>
        </div>
        <div className="sandbox-settings-grid">
          <label className="field-label sandbox-setting-wide">
            <span>Runtime</span>
            <select value={runtime} disabled={!canConfigure || info?.runtime_locked} onChange={(event) => setRuntime(event.target.value)}>
              <option value="auto">Automatic{runtimes?.default_runtime
                ? ` · ${runtimes.runtimes.find((item) => item.id === runtimes.default_runtime)?.label ?? runtimes.default_runtime}` : ""}</option>
              {runtime !== "auto" && !runtimes?.runtimes.some((item) => item.id === runtime)
                && <option value={runtime}>{runtime} · unavailable</option>}
              {runtimes?.runtimes.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>
                {item.label}{item.available ? "" : " · unavailable"}
              </option>)}
            </select>
          </label>
          {info?.runtime_locked && <p className="sandbox-help sandbox-setting-wide">Runtime fixed after first start.</p>}
          <div className="field-label sandbox-setting-wide">
            <span title="Absolute folder path on the server. When using WSL from Windows, enter a Windows path."><Folder size={12} /> Working folder</span>
            <FolderPathInput label="Working folder" value={workspace} disabled={!canConfigure}
              onPickingChange={setPickingFolder}
              onChange={(path) => edit({ workspace: path, ...(!path.trim() ? { access: "read_write" } : {}) })}
              placeholder="Managed workspace (default)" describedBy={`workspace-help-${card.id}`} />
          </div>
          <p className="sandbox-help sandbox-setting-wide" id={`workspace-help-${card.id}`}>
            {workspace.trim() ? (access === "read_write" ? "Edits change files in this folder directly." : "Files in this folder are read only.")
              : "Leave empty to create a managed workspace."}
          </p>
          <label className="field-label"><span>Folder access</span>
            <select value={access} disabled={!canConfigure || !workspace.trim()} onChange={(event) => setAccess(event.target.value as SandboxWorkspaceAccess)}>
              <option value="read_write">Read & write</option><option value="read_only">Read only</option>
            </select>
          </label>
          <label className="field-label"><span>Networking</span>
            <select aria-label="Networking" value={network ? "enabled" : "disabled"} disabled={!canConfigure} onChange={event => setNetwork(event.target.value === "enabled")}>
              <option value="disabled">Disabled</option>
              <option value="enabled" disabled={!selectedNetwork?.supported_network_modes?.includes("enabled")}>Enabled</option>
            </select>
          </label>
          {selectedNetwork?.network_reason && (network || !selectedNetwork.network_available)
            && <details className="sandbox-settings sandbox-setting-wide">
              <summary>Network details</summary>
              <p className="sandbox-help">{selectedNetwork.network_reason}</p>
            </details>}
        </div>
        <details className="sandbox-settings"><summary>Resource limits</summary>
          <div className="sandbox-limits-grid">
            <label className="field-label">Memory (MiB)<input type="number" min={16} max={8192} disabled={!canConfigure} value={memory / 1048576} onChange={event => setMemory(Number(event.target.value) * 1048576)} /></label>
            <label className="field-label">Process limit<input type="number" min={1} max={256} disabled={!canConfigure} value={processes} onChange={event => setProcesses(Number(event.target.value))} /></label>
            <label className="field-label">Command timeout (seconds)<input type="number" min={1} max={600} disabled={!canConfigure} value={timeout} onChange={event => setTimeoutValue(Number(event.target.value))} /></label>
          </div>
        </details>
        <div className="sandbox-config-actions">
          <span>{busy === "saving" ? "Saving…" : dirty ? "Unsaved changes" : stopped ? "Settings saved" : "Stop to edit settings"}</span>
          <button type="button" className="secondary-button" disabled={!canConfigure || !dirty} onClick={() => {
            useNodeSurfaceStore.getState().setDraft(draftKey, "");
          }}>Reset</button>
          <button type="submit" className="primary-button" disabled={!canConfigure || !dirty}>Save</button>
        </div>
      </form>
    </section>
    {settingsIssue && <p className="sandbox-error" role="alert">{settingsIssue}</p>}
    <SandboxEnvironment card={card} />
    {!compact && <><details className="sandbox-settings card-section"><summary>Attached objects <span>{connectionCount} connected</span></summary>
      <RelationshipList card={card} empty="No objects connected." />
    </details>
    <details className="sandbox-settings card-section"><summary>Runtime details</summary>
      <dl className="sandbox-runtime-details">
        <div><dt>Shell</dt><dd>{info?.shell.length ? info.shell.join(" ") : "Unavailable"}</dd></div>
        <div><dt>Runtime folder</dt><dd>{info?.workspace ?? "Created on first start"}</dd></div>
        <div><dt>Isolation</dt><dd>{info?.security_boundary ?? "Select an available runtime."}</dd></div>
      </dl>
    </details>
    <details className="sandbox-settings card-section"><summary>Recovery</summary>
      <p className="sandbox-help">Clear the runtime cache. Workspace files are preserved.</p>
      <button type="button" className="secondary-button" disabled={!stopped || !!busy} onClick={() => void worldApi.sandboxWorkspace(card.id, "reset-cache", {})
        .then(() => setCacheMessage("Runtime cache cleared."))
        .catch(error => setCacheMessage(apiErrorMessage(error)))}>Reset runtime cache</button>
      {cacheMessage && <p className="sandbox-help" role="status">{cacheMessage}</p>}
    </details></>}
  </div>;
}
