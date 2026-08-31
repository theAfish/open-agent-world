import { KeyRound, Server, Settings2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import type { ModelSettings } from "../state/modelSettings";

export function SettingsPanel() {
  const open = useWorldStore((state) => state.settingsOpen);
  const settings = useWorldStore((state) => state.modelSettings);
  const setOpen = useWorldStore((state) => state.toggleSettings);
  const save = useWorldStore((state) => state.saveModelSettings);
  const [draft, setDraft] = useState<ModelSettings>(settings);

  useEffect(() => {
    if (open) setDraft(settings);
  }, [open, settings]);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const applied = await save({ ...draft, models: draft.models.join("\n").split(/\r?\n/) });
    if (applied) setOpen();
  };

  return (
    <div className="dialog-backdrop settings-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) setOpen();
    }}>
      <form className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title" onSubmit={submit}>
        <header>
          <div className="dialog-icon"><Settings2 size={19} /></div>
          <div>
            <span>ADK model connection</span>
            <h2 id="settings-title">Model settings</h2>
          </div>
          <button type="button" className="icon-button" onClick={setOpen} aria-label="Close model settings"><X size={16} /></button>
        </header>

        <div className="settings-form">
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
        </div>

        <footer>
          <button type="button" className="secondary-button" onClick={setOpen}>Cancel</button>
          <button type="submit" className="primary-button">Save settings</button>
        </footer>
      </form>
    </div>
  );
}
