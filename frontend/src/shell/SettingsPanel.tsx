import { Box, Cpu, HardDrive, Settings2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import { EMPTY_MODEL_CATALOG, importLegacyModels, type ModelCatalog } from "../state/modelConnections";
import { ModelConnectionsEditor } from "./ModelConnectionsEditor";
import { worldApi } from "../api/client";
import type { SandboxSettings, StorageSettings } from "../api/client";
import type { SandboxRuntime } from "../types/world";
import { FolderPathInput } from "./FolderPathInput";

export function SettingsPanel() {
  const open = useWorldStore((state) => state.settingsOpen);
  const settings = useWorldStore((state) => state.modelSettings);
  const setOpen = useWorldStore((state) => state.toggleSettings);
  const [draft, setDraft] = useState<ModelCatalog>(EMPTY_MODEL_CATALOG);
  const [savedCatalog, setSavedCatalog] = useState<ModelCatalog>(EMPTY_MODEL_CATALOG);
  const [modelLoaded, setModelLoaded] = useState(false);
  const [modelError, setModelError] = useState("");
  const [modelRetry, setModelRetry] = useState(0);
  const [section, setSection] = useState<"model" | "sandbox" | "storage">("model");
  const [storage, setStorage] = useState<StorageSettings | null>(null);
  const [storagePath, setStoragePath] = useState("");
  const [storageRetry, setStorageRetry] = useState(0);
  useEffect(() => {
    if (!open || section !== "storage") return;
    let active = true;
    setStorage(null);
    worldApi.getStorageSettings().then(value => {
      if (!active) return;
      setStorage(value);
      setStoragePath(value.pending_path ?? "");
    }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Could not load storage settings."); });
    return () => { active = false; };
  }, [open, section, storageRetry]);
  const [sandbox, setSandbox] = useState<SandboxSettings>({ workspace_root: null, runtime: "auto" });
  const [runtimes, setRuntimes] = useState<SandboxRuntime[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const busy = saving || picking;
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!open) { setDraft(EMPTY_MODEL_CATALOG); return; }
    setModelLoaded(false);
    setModelError("");
    let active = true;
    worldApi.getModelConnections()
      .then((value) => {
        if (!active) return;
        setSavedCatalog(value);
        setDraft(importLegacyModels(value, settings));
        useWorldStore.setState(state => value.revision >= state.modelCatalog.revision ? { modelCatalog: value } : {});
        setModelLoaded(true);
      })
      .catch((cause: unknown) => {
        if (active) setModelError(cause instanceof Error ? cause.message : "Could not load model settings.");
      });
    return () => { active = false; };
  }, [open, settings, modelRetry]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoaded(false);
    setError("");
    Promise.all([worldApi.getSandboxSettings(), worldApi.getSandboxRuntimes()])
      .then(([value, catalog]) => {
        if (!active) return;
        setSandbox(value);
        setRuntimes(catalog.runtimes);
        setLoaded(true);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load Sandbox settings.");
      });
    return () => { active = false; };
  }, [open, retry]);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || (section === "sandbox" && !loaded) || (section === "model" && !modelLoaded)) return;
    setBusy(true);
    setError("");
    try {
      if (section === "storage") {
        if (!storage?.editable) return;
        const saved = await worldApi.saveStorageSettings(storagePath.trim() || null, storage.revision);
        setStorage(saved);
        setStoragePath(saved.pending_path ?? "");
      } else if (section === "sandbox") {
        await worldApi.saveSandboxSettings({ ...sandbox, workspace_root: sandbox.workspace_root?.trim() || null });
        setOpen();
      } else {
        const saved = await worldApi.saveModelConnections(draft);
        useWorldStore.setState({ modelCatalog: saved });
        setDraft(saved);
        setSavedCatalog(saved);
        setOpen();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save settings.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop settings-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) setOpen();
    }}>
      <form className="settings-dialog" onKeyDown={event => { if (event.key === "Escape" && !busy) setOpen(); }} role="dialog" aria-modal="true" aria-labelledby="settings-title" onSubmit={submit}>
        <header>
          <div className="dialog-icon"><Settings2 size={19} /></div>
          <div>
            <span>Application preferences</span>
            <h2 id="settings-title">Settings</h2>
          </div>
          <button type="button" className="icon-button" onClick={setOpen} disabled={busy} aria-label="Close settings"><X size={16} /></button>
        </header>

        <div className="settings-layout">
        <nav className="settings-sections" aria-label="Settings sections">
          <button type="button" className="secondary-button" aria-pressed={section === "model"} disabled={busy} onClick={() => { setSection("model"); setError(""); }}><Cpu size={16} /> Models</button>
          <button type="button" className="secondary-button" aria-pressed={section === "sandbox"} disabled={busy} onClick={() => { setSection("sandbox"); setError(""); }}><Box size={16} /> Sandbox</button>
          <button type="button" className="secondary-button" aria-pressed={section === "storage"} disabled={busy} onClick={() => { setSection("storage"); setError(""); }}><HardDrive size={16} /> Storage</button>
        </nav>

        <div className="settings-content">
        {section === "model" ? <div className="settings-form">
          {!modelLoaded && !modelError && <p role="status">Loading model settings…</p>}
          {modelLoaded && <>
            {draft.revision === 0 && draft.connections.length > 0 && <p className="settings-description">Previous models are included in this draft. Save to keep them on the backend.</p>}
            <ModelConnectionsEditor value={draft} onChange={setDraft} saved={savedCatalog} busy={busy} />
          </>}
        </div> : section === "storage" ? <div className="settings-form">
          <div className="settings-page-heading"><h3>Storage</h3><p>Application data on the backend computer.</p></div>
          {!storage && !error && <p role="status">Loading storage settings?</p>}
          {storage && <>
            <label className="field-label"><span>Current data location</span><input readOnly value={storage.current_path} /></label>
            <p className="settings-description">Includes conversations, sessions, settings, credentials, artifacts and managed Sandbox files. External workspaces stay in their current folders.</p>
            {storage.editable ? <div className="field-label">
              <span>New data location</span>
              <FolderPathInput label="New data location" describedBy="storage-help" value={storagePath} disabled={busy} onChange={setStoragePath} onPickingChange={setPicking} placeholder="Choose a new or empty folder" />
              <small id="storage-help">Use an absolute local folder on the backend computer. Saving schedules a move for the next backend start. Data is copied and verified before switching; the original folder is retained as a backup.</small>
            </div> : <p className="settings-description">This location is controlled by startup configuration (OPEN_AGENT_WORLD_DATA_ROOT). Remove that override to manage storage here.</p>}
            {storage.pending_path && <p role="status" className="settings-description">Scheduled for next start: {storage.pending_path}</p>}
            {storage.pending_path && <button type="button" className="secondary-button" disabled={busy} onClick={async () => {
              setBusy(true); setError("");
              try { const saved = await worldApi.saveStorageSettings(null, storage.revision); setStorage(saved); setStoragePath(""); }
              catch (cause) { setError(cause instanceof Error ? cause.message : "Could not cancel migration."); }
              finally { setBusy(false); }
            }}>Cancel scheduled move</button>}
            {storage.last_error && <p role="alert" className="settings-error">Migration did not complete. The original location remains active. {storage.last_error}</p>}
            {storage.previous_path && <label className="field-label"><span>Retained backup (before the move)</span><input readOnly value={storage.previous_path} /></label>}
          </>}
          {error && <button type="button" className="secondary-button" disabled={busy} onClick={() => { setError(""); setStorageRetry(value => value + 1); }}>Reload storage settings</button>}
        </div> : <div className="settings-form">
          <div className="settings-page-heading"><h3>Sandbox</h3><p>Workspace and runtime defaults for this backend.</p></div>
          <p className="settings-description">Defaults for new Sandboxes on this backend host. Existing Sandboxes keep their current folders and runtime.</p>
          {!loaded && !error && <p role="status">Loading Sandbox settings…</p>}
          <div className="field-label">
            <span id="sandbox-default-workspace-label">Default Workspace location</span>
            <FolderPathInput label="Default Workspace location" describedBy="sandbox-default-workspace-help"
              value={sandbox.workspace_root ?? ""} disabled={!loaded || busy} placeholder="System-managed location"
              onChange={(path) => setSandbox((current) => ({ ...current, workspace_root: path }))} onPickingChange={setPicking} />
            <small id="sandbox-default-workspace-help">Enter an existing absolute folder on the backend computer, for example D:\Workspaces. Each new Sandbox gets its own subfolder. Leave blank to use the system-managed location.</small>
          </div>
          <label className="field-label">
            <span id="sandbox-default-runtime-label">Default runtime</span>
            <select aria-labelledby="sandbox-default-runtime-label" aria-describedby="sandbox-default-runtime-help" value={sandbox.runtime} disabled={!loaded || busy} onChange={(event) => setSandbox({ ...sandbox, runtime: event.target.value })}>
              <option value="auto">Automatic</option>
              {sandbox.runtime !== "auto" && !runtimes.some((runtime) => runtime.id === sandbox.runtime) && <option value={sandbox.runtime}>{sandbox.runtime} (not installed)</option>}
              {runtimes.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.label}{runtime.available ? "" : " (unavailable)"}</option>)}
            </select>
            <small id="sandbox-default-runtime-help">Used when a new Sandbox has no explicit runtime. You can choose a different runtime on the card before its first start.</small>
          </label>
          <p className="settings-description">Settings are saved on the backend and survive restarts. Workspaces in your chosen folder are retained when a Sandbox is deleted.</p>
        </div>}
        {(error || (section === "model" && modelError)) && <p role="alert" className="settings-error">{error || modelError} {section === "sandbox" && !loaded && <button type="button" className="secondary-button" onClick={() => setRetry((value) => value + 1)}>Retry</button>} {section === "model" && <button type="button" className="secondary-button" onClick={() => { setError(""); setModelRetry((value) => value + 1); }}>{modelLoaded ? "Discard draft and reload" : "Retry"}</button>}</p>}

        </div>
        </div>
        <footer>
          <button type="button" className="secondary-button" onClick={setOpen} disabled={busy}>Cancel</button>
          <button type="submit" className="primary-button" disabled={busy || (section === "storage" && (!storage?.editable || !storagePath.trim() || storagePath.trim() === storage.pending_path)) || (section === "sandbox" && !loaded) || (section === "model" && !modelLoaded)}>{saving ? "Saving…" : "Save settings"}</button>
        </footer>
      </form>
    </div>
  );
}
