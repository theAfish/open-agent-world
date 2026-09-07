import { SandboxEnvironment } from "./SandboxEnvironment";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { worldApi, apiErrorMessage } from "../api/client";
import { CircleStop, Folder, Play, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { SandboxWorkspaceAccess, WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { RelationshipList } from "./CardUtilities";
import { FolderPathInput } from "../shell/FolderPathInput";

export function SandboxCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const edges = useWorldStore((state) => state.edges);
  const startSandbox = useWorldStore((state) => state.startSandbox);
  const stopSandbox = useWorldStore((state) => state.stopSandbox);
  const saveConfig = useWorldStore((state) => state.saveSandboxConfig);
  const refreshSandbox = useWorldStore((state) => state.refreshSandbox);
  const loadRuntimes = useWorldStore((state) => state.loadSandboxRuntimes);
  const info = useWorldStore((state) => state.sandboxInfo[card.id]);
  const busy = useWorldStore((state) => state.sandboxBusy[card.id]);
  const error = useWorldStore((state) => state.sandboxErrors[card.id]);
  const runtimes = useWorldStore((state) => state.sandboxRuntimes);
  const runtimesLoading = useWorldStore((state) => state.sandboxRuntimesLoading);
  const runtimesError = useWorldStore((state) => state.sandboxRuntimesError);
  const socketState = useWorldStore((state) => state.socketState);
  const [network, setNetwork] = useState(Boolean(card.config.network_enabled));
  const [memory, setMemory] = useState(Number(card.config.memory_bytes ?? 536870912));
  const [processes, setProcesses] = useState(Number(card.config.active_process_limit ?? 16));
  const [timeout, setTimeoutValue] = useState(Number(card.config.command_timeout ?? 60));
  const [cacheMessage, setCacheMessage] = useState("");
  const [pickingFolder, setPickingFolder] = useState(false);
  const [runtime, setRuntime] = useState(card.config.runtime ?? "auto");
  const [workspace, setWorkspace] = useState(card.config.workspace_path ?? "");
  const [access, setAccess] = useState<SandboxWorkspaceAccess>(card.config.workspace_access ?? "read_write");

  useEffect(() => {
    if (level !== "inspector") return;
    void loadRuntimes();
    void refreshSandbox(card.id);
  }, [card.id, level, loadRuntimes, refreshSandbox, socketState]);

  useEffect(() => {
    setNetwork(Boolean(card.config.network_enabled)); setMemory(Number(card.config.memory_bytes ?? 536870912));
    setProcesses(Number(card.config.active_process_limit ?? 16)); setTimeoutValue(Number(card.config.command_timeout ?? 60));
  }, [card.config.network_enabled, card.config.memory_bytes, card.config.active_process_limit, card.config.command_timeout]);
  useEffect(() => setRuntime(card.config.runtime ?? "auto"), [card.config.runtime]);
  useEffect(() => setWorkspace(card.config.workspace_path ?? ""), [card.config.workspace_path]);
  useEffect(() => setAccess(card.config.workspace_access ?? "read_write"), [card.config.workspace_access]);

  const connectionCount = useMemo(
    () => edges.filter((edge) => edge.target === card.id || edge.source === card.id).length,
    [card.id, edges],
  );
  const activeCommand = String(card.config.active_command ?? "");
  const dirty = runtime !== (card.config.runtime ?? "auto")
    || (workspace.trim() || null) !== (card.config.workspace_path ?? null)
    || access !== (card.config.workspace_access ?? "read_write")
    || network !== Boolean(card.config.network_enabled) || memory !== Number(card.config.memory_bytes ?? 536870912)
    || processes !== Number(card.config.active_process_limit ?? 16) || timeout !== Number(card.config.command_timeout ?? 60);
  const ready = card.status === "ready" || card.status === "running";
  const stopped = card.status === "stopped";
  const canConfigure = stopped && !busy && !pickingFolder && !!info;
  const canStop = ready || card.status === "error" || busy === "executing";
  const selectedRuntime = runtimes?.runtimes.find((item) => item.id === (
    info?.runtime_locked ? info.runtime_id : runtime === "auto" ? runtimes.default_runtime : runtime
  ));
  const runtimeLabel = selectedRuntime?.label ?? info?.runtime_id ?? "Execution environment";
  const statusLabel = busy === "starting" ? "Starting…"
    : busy === "stopping" ? "Stopping…"
    : busy === "executing" || card.status === "running" ? "Command running"
    : !info ? "Status unavailable"
    : !info.available ? "Runtime unavailable"
    : card.status === "error" ? "Needs attention"
    : card.status === "ready" ? "Ready"
    : stopped ? "Stopped"
    : card.status;
  const runtimeIssue = selectedRuntime && !selectedRuntime.available ? selectedRuntime.reason : undefined;
  const issue = error ?? runtimesError ?? runtimeIssue ?? info?.unavailable_reason;
  const shellLabel = info?.shell.length ? info.shell.join(" ") : "Runtime shell";

  return (
    <div className="expanded-stack sandbox-controls nodrag nowheel">
      <div className="sandbox-status-panel">
        <div className="instrument-gauge" data-active={ready && info?.available === true} aria-hidden="true"><i /><i /><i /></div>
        <div>
          <span>{runtimeLabel}</span>
          <strong role="status">{statusLabel}</strong>
        </div>
        <button
          type="button"
          className={canStop ? "secondary-button" : "primary-button"}
          disabled={canStop
            ? !!busy && busy !== "executing"
            : !!busy || pickingFolder || dirty || !info?.available || !stopped || (network && !selectedRuntime?.network_available)}
          onClick={() => void (canStop ? stopSandbox(card.id) : startSandbox(card.id))}
        >
          {canStop ? <CircleStop size={14} /> : <Play size={14} fill="currentColor" />}
          {canStop ? "Stop" : "Start"}
        </button>
      </div>

      <details className="sandbox-settings" open={!ready || dirty}>
        <summary>Workspace settings <span>{card.config.workspace_path ?? "Managed workspace"}</span></summary>
      <form className="sandbox-config-form" onSubmit={(event) => {
        event.preventDefault();
        if (canConfigure && dirty) {
          void saveConfig(card.id, {
            runtime,
            workspace_path: workspace.trim() || null,
            workspace_access: workspace.trim() ? access : "read_write",
            ...(network !== Boolean(card.config.network_enabled) ? { network_enabled: network } : {}),
            ...(memory !== Number(card.config.memory_bytes ?? 536870912) ? { memory_bytes: memory } : {}),
            ...(processes !== Number(card.config.active_process_limit ?? 16) ? { active_process_limit: processes } : {}),
            ...(timeout !== Number(card.config.command_timeout ?? 60) ? { command_timeout: timeout } : {}),
          });
        }
      }}>
        <div className="section-heading">
          <span>Environment & workspace</span>
          <button type="button" className="sandbox-refresh" aria-label="Refresh sandbox environment"
            disabled={!!busy || runtimesLoading}
            onClick={() => { void loadRuntimes(true); void refreshSandbox(card.id); }}>
            <RefreshCw size={12} /> {runtimesLoading ? "Checking…" : "Refresh"}
          </button>
        </div>
        <label className="field-label">
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
        {info?.runtime_locked && <p className="sandbox-help">Runtime fixed for this sandbox. Create another card to use a different environment.</p>}

        <div className="field-label">
          <span><Folder size={12} /> Working folder</span>
          <FolderPathInput label="Working folder" value={workspace} disabled={!canConfigure}
            onPickingChange={setPickingFolder}
            onChange={(path) => {
              setWorkspace(path);
              if (!path.trim()) setAccess("read_write");
            }}
            placeholder="Leave empty for a managed workspace"
            describedBy={`workspace-help-${card.id}`} />
        </div>
        <p className="sandbox-help" id={`workspace-help-${card.id}`}>
          Absolute folder path on the machine running the server. For WSL launched from Windows, use a Windows path.
          {workspace.trim() ? (access === "read_write"
            ? " Agent edits change the files in this folder directly."
            : " The sandbox can read this folder; edits are blocked.")
            : " A managed workspace is created on first start."}
        </p>
        <label className="field-label">
          <span>Folder access</span>
          <select value={access} disabled={!canConfigure || !workspace.trim()} onChange={(event) => setAccess(event.target.value as SandboxWorkspaceAccess)}>
            <option value="read_write">Read & write</option>
            <option value="read_only">Read only</option>
          </select>
        </label>
        <label className="field-label"><span>Networking</span><select aria-label="Networking" value={network ? "enabled" : "disabled"} disabled={!canConfigure} onChange={e => setNetwork(e.target.value === "enabled")}>
          <option value="disabled">Disabled (default)</option><option value="enabled" disabled={!selectedRuntime?.supported_network_modes?.includes("enabled") || !selectedRuntime?.network_available}>Enabled</option>
        </select></label>
        <p className="sandbox-help">{selectedRuntime?.network_reason ?? "Refresh to check networking support and prerequisites."} Restart required for runtime settings: stop, save changes, then start.</p>
        <details><summary>Advanced settings</summary>
          <label className="field-label">Memory (MiB)<input type="number" min={16} max={8192} disabled={!canConfigure} value={memory / 1048576} onChange={e => setMemory(Number(e.target.value) * 1048576)} /></label>
          <label className="field-label">Process limit<input type="number" min={1} max={256} disabled={!canConfigure} value={processes} onChange={e => setProcesses(Number(e.target.value))} /></label>
          <label className="field-label">Command timeout (seconds)<input type="number" min={1} max={600} disabled={!canConfigure} value={timeout} onChange={e => setTimeoutValue(Number(e.target.value))} /></label>
        </details>
        <div className="sandbox-config-actions">
          <span>{busy === "saving" ? "Saving…" : dirty ? "Unsaved changes" : stopped ? "Settings saved" : "Stop to edit settings"}</span>
          <button type="button" className="secondary-button" disabled={!canConfigure || !dirty} onClick={() => {
            setNetwork(Boolean(card.config.network_enabled)); setMemory(Number(card.config.memory_bytes ?? 536870912));
            setProcesses(Number(card.config.active_process_limit ?? 16)); setTimeoutValue(Number(card.config.command_timeout ?? 60));
            setRuntime(card.config.runtime ?? "auto");
            setWorkspace(card.config.workspace_path ?? "");
            setAccess(card.config.workspace_access ?? "read_write");
          }}>Reset</button>
          <button type="submit" className="primary-button" disabled={!canConfigure || !dirty}>Save</button>
        </div>
      </form>
      </details>

      {issue && <p className="sandbox-error" role="alert">{issue}</p>}

      <div className="sandbox-help"><span>{shellLabel}</span> · <span>{info?.workspace}</span><p>Network {info?.network_enabled ? "enabled" : "disabled"} · {activeCommand ? `Running: ${activeCommand}` : "No current activity"}</p></div>
      <button className="primary-button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(card.id)}>Open Window</button>
      <SandboxEnvironment card={card} />
      <details className="sandbox-settings"><summary>Recovery</summary><p className="sandbox-help">Stop terminates the process tree. Reset clears host-managed Skill cache while preserving workspace files and outputs. New, copied and summoned Sandboxes start stopped. Opening a window never starts them.</p>
        <button className="secondary-button" disabled={!stopped || !!busy} onClick={() => void worldApi.sandboxWorkspace(card.id, "reset-cache", {}).then(() => setCacheMessage("Runtime cache cleared; workspace preserved.")).catch(e => setCacheMessage(apiErrorMessage(e)))}>Reset runtime cache</button>
        {cacheMessage && <p>{cacheMessage}</p>}
      </details>

      <section className="card-section">
        <div className="section-heading"><span>Attached objects</span><small>{connectionCount} total</small></div>
        <RelationshipList card={card} empty="Connect an agent or resource to make it available here." />
      </section>

      <div className="security-strip sandbox-security">
        <ShieldCheck size={16} />
        <div><strong>Isolation</strong><span>{info?.security_boundary ?? "Start after an available runtime is selected."}</span></div>
      </div>
    </div>
  );
}
