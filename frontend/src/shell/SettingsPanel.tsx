import { createPortal } from 'react-dom';
import { reportInteraction } from "../state/interactions";
import { t, useLocale } from "../i18n";
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
import { DeepLSettings } from "./DeepLSettings";
import { EnvironmentVariablesEditor, environmentVariablesFromValue, environmentVariablesToValue, type EnvironmentVariableRow } from "../cards/ExecutionConfiguration";

export function SettingsPanel() {
  const { locale, setLocale } = useLocale();
  const open = useWorldStore((state) => state.settingsOpen);
  const settings = useWorldStore((state) => state.modelSettings);
  const setOpen = useWorldStore((state) => state.toggleSettings);
  const [draft, setDraft] = useState<ModelCatalog>(EMPTY_MODEL_CATALOG);
  const [savedCatalog, setSavedCatalog] = useState<ModelCatalog>(EMPTY_MODEL_CATALOG);
  const [modelLoaded, setModelLoaded] = useState(false);
  const [modelError, setModelError] = useState("");
  const [modelRetry, setModelRetry] = useState(0);
  const [section, setSection] = useState<"model" | "sandbox" | "storage" | "deepl">("model");
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
    }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : t("Could not load storage settings.")); });
    return () => { active = false; };
  }, [open, section, storageRetry]);
  const [sandbox, setSandbox] = useState<SandboxSettings>({ workspace_root: null, runtime: "auto" });
  const [sandboxSaved, setSandboxSaved] = useState(false);
  const [environmentRows, setEnvironmentRows] = useState<EnvironmentVariableRow[]>([]);
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
        if (active) setModelError(cause instanceof Error ? cause.message : t("Could not load model settings."));
      });
    return () => { active = false; };
  }, [open, settings, modelRetry]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoaded(false);
    setSandboxSaved(false);
    setError("");
    Promise.all([worldApi.getSandboxSettings(), worldApi.getSandboxRuntimes()])
      .then(([value, catalog]) => {
        if (!active) return;
        setSandbox(value);
        setEnvironmentRows(environmentVariablesFromValue({ variables: value.environment_variables ?? {} }));
        setRuntimes(catalog.runtimes);
        setLoaded(true);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : t("Could not load Sandbox settings."));
      });
    return () => { active = false; };
  }, [open, retry]);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (section === "deepl") return;
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
        const variables = environmentVariablesToValue(environmentRows).variables as Record<string, string>;
        const saved = await worldApi.saveSandboxSettings({ runtime: sandbox.runtime, workspace_root: sandbox.workspace_root?.trim() || null, environment_variables: variables });
        setSandbox(saved);
        setSandboxSaved(true);
        if (!saved.backup_paths?.length) setOpen();
      } else {
        const saved = await worldApi.saveModelConnections(draft);
        useWorldStore.setState({ modelCatalog: saved });
        setDraft(saved);
        setSavedCatalog(saved);
        setOpen();
        reportInteraction({ type: 'models-saved' });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Could not save settings."));
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="dialog-backdrop settings-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) setOpen();
    }}>
      <form className="settings-dialog" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (!busy) setOpen(); } }} role="dialog" aria-modal="true" aria-labelledby="settings-title" onSubmit={submit}>
        <header>
          <div className="dialog-icon"><Settings2 size={19} /></div>
          <div>
            <span>{t("Application preferences")}</span>
            <h2 id="settings-title">{t("Settings")}</h2>
          </div>
          <button type="button" className="icon-button" onClick={setOpen} disabled={busy} data-tutorial="settings-close" aria-label={t("Close settings")}><X size={16} /></button>
        </header>

        <div className="settings-layout">
        <nav className="settings-sections" aria-label={t("Settings sections")}>
          <label className="field-label"><span>{t('Language')}</span><select aria-label={t('Language')} value={locale} onChange={event => setLocale(event.target.value === 'zh-CN' ? 'zh-CN' : 'en')}><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>
          <button type="button" className="secondary-button" data-tutorial="models-tab" aria-pressed={section === "model"} disabled={busy} onClick={() => { setSection("model"); setError(""); }}><Cpu size={16} /> {t("Models")}</button>
          <button type="button" className="secondary-button" aria-pressed={section === "sandbox"} disabled={busy} onClick={() => { setSection("sandbox"); setError(""); }}><Box size={16} /> {t("Sandbox")}</button>
          <button type="button" className="secondary-button" aria-pressed={section === "storage"} disabled={busy} onClick={() => { setSection("storage"); setError(""); }}><HardDrive size={16} /> {t("Storage")}</button>
          <button type="button" className="secondary-button" aria-pressed={section === "deepl"} disabled={busy} onClick={() => { setSection("deepl"); setError(""); }}>{t("DeepL")}</button>
        </nav>

        <div className="settings-content">
        {section === "deepl" ? <DeepLSettings /> : section === "model" ? <div className="settings-form">
          {!modelLoaded && !modelError && <p role="status">{t("Loading model settings…")}</p>}
          {modelLoaded && <>
            {draft.revision === 0 && draft.connections.length > 0 && <p className="settings-description">{t("Previous models are included in this draft. Save to keep them on the backend.")}</p>}
            <ModelConnectionsEditor value={draft} onChange={setDraft} saved={savedCatalog} busy={busy} />
          </>}
        </div> : section === "storage" ? <div className="settings-form">
          <div className="settings-page-heading"><h3>{t("Storage")}</h3></div>
          {!storage && !error && <p role="status">{t("Loading storage settings?")}</p>}
          {storage && <>
            <label className="field-label"><span>{t("Current data location")}</span><input readOnly value={storage.current_path} /></label>
            {storage.editable ? <div className="field-label">
              <span>{t("New data location")}</span>
              <FolderPathInput label={t("New data location")} describedBy="storage-help" value={storagePath} disabled={busy} onChange={setStoragePath} onPickingChange={setPicking} placeholder={t("Choose a new or empty folder")} />
              <small id="storage-help">{t("Moves on next restart. The original folder is kept as a backup.")}</small>
            </div> : <p className="settings-description">{t("This location is controlled by startup configuration (OPEN_AGENT_WORLD_DATA_ROOT). Remove that override to manage storage here.")}</p>}
            {storage.pending_path && <p role="status" className="settings-description">{t("Scheduled for next start:")} {storage.pending_path}</p>}
            {storage.pending_path && <button type="button" className="secondary-button" disabled={busy} onClick={async () => {
              setBusy(true); setError("");
              try { const saved = await worldApi.saveStorageSettings(null, storage.revision); setStorage(saved); setStoragePath(""); }
              catch (cause) { setError(cause instanceof Error ? cause.message : t("Could not cancel migration.")); }
              finally { setBusy(false); }
            }}>{t("Cancel scheduled move")}</button>}
            {storage.last_error && <p role="alert" className="settings-error">{t("Migration did not complete. The original location remains active.")} {storage.last_error}</p>}
            {storage.previous_path && <label className="field-label"><span>{t("Retained backup (before the move)")}</span><input readOnly value={storage.previous_path} /></label>}
          </>}
          {error && <button type="button" className="secondary-button" disabled={busy} onClick={() => { setError(""); setStorageRetry(value => value + 1); }}>{t("Reload storage settings")}</button>}
        </div> : <div className="settings-form">
          <div className="settings-page-heading"><h3>{t("Sandbox")}</h3></div>
          {!!sandbox.backup_paths?.length && <section className="settings-workspace-backups" aria-label={t("Old workspace folders")}>
            <strong>{t("Old workspace folders")}</strong>
            {sandboxSaved && <p role="status">{t("Workspace settings saved.")}</p>}
            <p>{t("After verifying the new files, please delete unused backup folders manually.")}</p>
            <ul>{sandbox.backup_paths.map(path => <li key={path}><code>{path}</code></li>)}</ul>
          </section>}
          {!loaded && !error && <p role="status">{t("Loading Sandbox settings…")}</p>}
          <div className="field-label">
            <span id="sandbox-default-workspace-label">{t("Default Workspace location")}</span>
            <FolderPathInput label={t("Default Workspace location")} describedBy="sandbox-default-workspace-help"
              value={sandbox.workspace_root ?? ""} disabled={!loaded || busy} placeholder={t("System-managed location")}
              onChange={(path) => setSandbox((current) => ({ ...current, workspace_root: path }))} onPickingChange={setPicking} />
            <small id="sandbox-default-workspace-help">{t("Changing this folder moves all Sandbox workspaces and keeps backups. Stop Sandboxes and Agents first.")}</small>
            {saving && <small role="status">{t("Saving settings. Please keep this window open.")}</small>}
          </div>
          <label className="field-label">
            <span id="sandbox-default-runtime-label">{t("Default runtime")}</span>
            <select aria-labelledby="sandbox-default-runtime-label" value={sandbox.runtime} disabled={!loaded || busy} onChange={(event) => setSandbox({ ...sandbox, runtime: event.target.value })}>
              <option value="auto">{t("Automatic")}</option>
              {sandbox.runtime !== "auto" && !runtimes.some((runtime) => runtime.id === sandbox.runtime) && <option value={sandbox.runtime}>{sandbox.runtime} {t("(not installed)")}</option>}
              {runtimes.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.label}{runtime.available ? "" : " (unavailable)"}</option>)}
            </select>
          </label>
          <section aria-label={t("Global environment variables")}>
            <h3>{t("Global environment variables")}</h3>
            <p className="settings-description">{t("Applies to all Sandboxes on their next command. Local values take priority.")}</p>
            <EnvironmentVariablesEditor rows={environmentRows} onChange={setEnvironmentRows} disabled={!loaded || busy}
              allowSecrets={false} secrets={{}} bindings={{}} onSecretsChange={() => {}} />
            {environmentRows.length > 0 && <small>{t("Plain text values. Store credentials as Sandbox secrets.")}</small>}
          </section>
        </div>}
        {(error || (section === "model" && modelError)) && <p role="alert" className="settings-error">{error || modelError} {section === "sandbox" && !loaded && <button type="button" className="secondary-button" onClick={() => setRetry((value) => value + 1)}>{t("Retry")}</button>} {section === "model" && <button type="button" className="secondary-button" onClick={() => { setError(""); setModelRetry((value) => value + 1); }}>{modelLoaded ? t("Discard draft and reload") : t("Retry")}</button>}</p>}

        </div>
        </div>
        <footer>
          <button type="button" className="secondary-button" onClick={setOpen} disabled={busy}>{t(section === "sandbox" && sandboxSaved ? "Close" : "Cancel")}</button>
          {section !== "deepl" && <button data-tutorial={section === "model" ? "model-save" : undefined} type="submit" className="primary-button" disabled={busy || (section === "storage" && (!storage?.editable || !storagePath.trim() || storagePath.trim() === storage.pending_path)) || (section === "sandbox" && !loaded) || (section === "model" && !modelLoaded)}>{saving ? t("Saving…") : t("Save settings")}</button>}
        </footer>
      </form>
    </div>,
    document.querySelector('dialog.legion-workspace[open]') ?? document.body,
  );
}
