import { KeyRound, Server, Settings2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import type { ModelSettings } from "../state/modelSettings";
import { worldApi } from "../api/client";
import type { SandboxSettings } from "../api/client";
import type { SandboxRuntime } from "../types/world";
import { FolderPathInput } from "./FolderPathInput";

export function SettingsPanel() {
  const open = useWorldStore((state) => state.settingsOpen);
  const settings = useWorldStore((state) => state.modelSettings);
  const setOpen = useWorldStore((state) => state.toggleSettings);
  const save = useWorldStore((state) => state.saveModelSettings);
  const [draft, setDraft] = useState<ModelSettings>(settings);
  const [section, setSection] = useState<"model" | "sandbox">("model");
  const [sandbox, setSandbox] = useState<SandboxSettings>({ workspace_root: null, runtime: "auto" });
  const [runtimes, setRuntimes] = useState<SandboxRuntime[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const busy = saving || picking;
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (open) setDraft(settings);
  }, [open, settings]);

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
    if (busy || (section === "sandbox" && !loaded)) return;
    setBusy(true);
    setError("");
    try {
      if (section === "sandbox") {
        await worldApi.saveSandboxSettings({ ...sandbox, workspace_root: sandbox.workspace_root?.trim() || null });
        setOpen();
      } else {
        const applied = await save({ ...draft, models: draft.models.join("\n").split(/\r?\n/) });
        if (applied) setOpen();
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
      <form className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title" onSubmit={submit}>
        <header>
          <div className="dialog-icon"><Settings2 size={19} /></div>
          <div>
            <span>Application preferences</span>
            <h2 id="settings-title">Settings</h2>
          </div>
          <button type="button" className="icon-button" onClick={setOpen} disabled={busy} aria-label="Close settings"><X size={16} /></button>
        </header>

        <nav className="settings-sections" aria-label="Settings sections">
          <button type="button" className="secondary-button" aria-pressed={section === "model"} disabled={busy} onClick={() => setSection("model")}>Models</button>
          <button type="button" className="secondary-button" aria-pressed={section === "sandbox"} disabled={busy} onClick={() => setSection("sandbox")}>Sandbox</button>
        </nav>

        {section === "model" ? <div className="settings-form">
          <label className="field-label">
            <span><Server size={11} /> OpenAI-compatible base URL</span>
            <input value={draft.baseUrl} placeholder="https://api.openai.com/v1" onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} spellCheck={false} />
            <small>Used only when ADK resolves the selected model through its LiteLLM adapter.</small>
          </label>
          <label className="field-label">
            <span><KeyRound size={11} /> API key</span>
            <input type="password" value={draft.apiKey} placeholder="sk-..." onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} autoComplete="off" spellCheck={false} />
            <small>Kept in this browser session and ADK runtime memory; restored automatically after a backend restart, never stored in world data.</small>
          </label>
          <label className="field-label">
            <span>Available models</span>
            <textarea rows={7} value={draft.models.join("\n")} placeholder={'openai/gpt-4o-mini\nanthropic/claude-3-5-sonnet'} onChange={(event) => setDraft({ ...draft, models: event.target.value.split(/\r?\n/) })} spellCheck={false} />
            <small>One model per line. ADK chooses its native or LiteLLM model adapter automatically.</small>
          </label>
        </div> : <div className="settings-form">
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
        {error && <p role="alert" className="settings-error">{error} {section === "sandbox" && !loaded && <button type="button" className="secondary-button" onClick={() => setRetry((value) => value + 1)}>Retry</button>}</p>}

        <footer>
          <button type="button" className="secondary-button" onClick={setOpen} disabled={busy}>Cancel</button>
          <button type="submit" className="primary-button" disabled={busy || (section === "sandbox" && !loaded)}>{saving ? "Saving…" : "Save settings"}</button>
        </footer>
      </form>
    </div>
  );
}
