import { CircleStop, Folder, Play, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { SandboxWorkspaceAccess, WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { InstrumentOutput, RelationshipList } from "./CardUtilities";
import { FolderPathInput } from "../shell/FolderPathInput";

export function SandboxCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const edges = useWorldStore((state) => state.edges);
  const startSandbox = useWorldStore((state) => state.startSandbox);
  const stopSandbox = useWorldStore((state) => state.stopSandbox);
  const executeSandbox = useWorldStore((state) => state.executeSandbox);
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
  const [command, setCommand] = useState("");
  const [pickingFolder, setPickingFolder] = useState(false);
  const [runtime, setRuntime] = useState(card.config.runtime ?? "auto");
  const [workspace, setWorkspace] = useState(card.config.workspace_path ?? "");
  const [access, setAccess] = useState<SandboxWorkspaceAccess>(card.config.workspace_access ?? "read_write");

  useEffect(() => {
    if (level !== "inspector") return;
    void loadRuntimes();
    void refreshSandbox(card.id);
  }, [card.id, level, loadRuntimes, refreshSandbox, socketState]);

  useEffect(() => setRuntime(card.config.runtime ?? "auto"), [card.config.runtime]);
  useEffect(() => setWorkspace(card.config.workspace_path ?? ""), [card.config.workspace_path]);
  useEffect(() => setAccess(card.config.workspace_access ?? "read_write"), [card.config.workspace_access]);

  const connectionCount = useMemo(
    () => edges.filter((edge) => edge.target === card.id || edge.source === card.id).length,
    [card.id, edges],
  );
  const output = Array.isArray(card.config.output) ? card.config.output.map(String) : [];
  const activeCommand = String(card.config.active_command ?? "");
  const dirty = runtime !== (card.config.runtime ?? "auto")
    || (workspace.trim() || null) !== (card.config.workspace_path ?? null)
    || access !== (card.config.workspace_access ?? "read_write");
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
            : !!busy || pickingFolder || dirty || !info?.available || !stopped}
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
        <div className="sandbox-config-actions">
          <span>{busy === "saving" ? "Saving…" : dirty ? "Unsaved changes" : stopped ? "Settings saved" : "Stop to edit settings"}</span>
          <button type="button" className="secondary-button" disabled={!canConfigure || !dirty} onClick={() => {
            setRuntime(card.config.runtime ?? "auto");
            setWorkspace(card.config.workspace_path ?? "");
            setAccess(card.config.workspace_access ?? "read_write");
          }}>Reset</button>
          <button type="submit" className="primary-button" disabled={!canConfigure || !dirty}>Save</button>
        </div>
      </form>
      </details>

      {issue && <p className="sandbox-error" role="alert">{issue}</p>}

      <form className="command-form" onSubmit={(event) => {
        event.preventDefault();
        if (command.trim() && ready && !busy && !dirty) void executeSandbox(card.id, command.trim());
      }}>
        <label htmlFor={`command-${card.id}`}>Terminal <span className="sandbox-shell">{shellLabel}</span></label>
        {info?.workspace && <span className="sandbox-working-directory" title={info.workspace}>{info.workspace}</span>}
        <div>
          <span aria-hidden="true">›</span>
          <input id={`command-${card.id}`} value={command} onChange={(event) => setCommand(event.target.value)}
            spellCheck={false} autoComplete="off" placeholder="echo hello world" disabled={!ready || !!busy} />
          <button type="submit" disabled={!ready || !!busy || dirty || !command.trim()} aria-label="Execute command">
            <Play size={13} fill="currentColor" />
          </button>
        </div>
      </form>

      <section className="card-section output-section sandbox-output-section">
        <div className="section-heading"><span>Terminal activity</span><small>{activeCommand ? `running: ${activeCommand}` : "idle"}</small></div>
        <InstrumentOutput lines={output} empty="Start the sandbox and run a command in its workspace." />
      </section>

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
