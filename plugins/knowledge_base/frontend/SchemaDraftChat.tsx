import { t } from "@oaw/plugin-api";
import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Paperclip, Send, Sparkles, Undo2, X } from "lucide-react";

export type DraftSchema = { name: string; description: string; system_prompt: string; definition: Record<string, unknown> };
export type DraftApi = {
  /** False until a model is available. */
  ready: boolean; modelSelect: ReactNode;
  convert: (file: File) => Promise<{ markdown: string; truncated: boolean }>;
  send: (body: { messages: { role: string; content: string }[]; samples: { filename: string; markdown: string }[];
    current?: Record<string, unknown> }) => Promise<{ reply: string; draft: DraftSchema | null }>;
};

type Sample = { id: number; filename: string; state: "converting" | "ready" | "failed"; markdown?: string; error?: string; truncated?: boolean };
type Turn = { role: "user" | "assistant"; content: string; draft?: DraftSchema };

export const MAX_SAMPLES = 5;
export const MAX_SAMPLE_BYTES = 2 * 1024 * 1024;

const reason = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** A short conversation that drafts a schema from a request and sample files. A draft only
 * reaches the editor when the person applies it, and the editor's form is where it is checked. */
export function SchemaDraftChat({ api, current, onApply, onUndo, defaultOpen }: {
  api: DraftApi; current: () => Record<string, unknown> | undefined; onApply: (draft: DraftSchema) => void;
  onUndo?: () => void; defaultOpen: boolean;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The newest draft the person has not applied; a follow-up request edits it, not the editor.
  const [pending, setPending] = useState<DraftSchema>();
  const nextId = useRef(0);
  const picker = useRef<HTMLInputElement>(null);

  const attach = (files: FileList | File[]) => {
    setError("");
    let count = samples.length;
    for (const file of Array.from(files)) {
      if (count >= MAX_SAMPLES) { setError(t("At most {v0} sample files", { v0: MAX_SAMPLES })); break; }
      if (file.size > MAX_SAMPLE_BYTES) { setError(t("Sample files are limited to 2 MiB")); continue; }
      count += 1;
      const id = nextId.current++;
      setSamples(previous => [...previous, { id, filename: file.name, state: "converting" }]);
      api.convert(file).then(
        result => setSamples(previous => previous.map(item => item.id === id ? { ...item, state: "ready", ...result } : item)),
        failure => setSamples(previous => previous.map(item => item.id === id ? { ...item, state: "failed", error: reason(failure) } : item)));
    }
  };

  const converting = samples.some(item => item.state === "converting");
  const canSend = api.ready && !busy && !converting && !!input.trim();
  const send = async () => {
    if (!canSend) return;
    const text = input.trim();
    const messages = [...turns.map(turn => ({ role: turn.role, content: turn.content || "(draft)" })), { role: "user", content: text }];
    setTurns(previous => [...previous, { role: "user", content: text }]);
    setInput(""); setBusy(true); setError("");
    try {
      const result = await api.send({ messages, current: pending ?? current(),
        samples: samples.filter(item => item.state === "ready").map(item => ({ filename: item.filename, markdown: item.markdown ?? "" })) });
      setTurns(previous => [...previous, { role: "assistant", content: result.reply, ...(result.draft ? { draft: result.draft } : {}) }]);
      if (result.draft) setPending(result.draft);
    } catch (failure) { setError(reason(failure)); }
    finally { setBusy(false); }
  };

  return <details className="knowledge-draft-chat" open={defaultOpen || undefined}
    onDragOver={event => event.preventDefault()}
    onDrop={event => { event.preventDefault(); attach(event.dataTransfer.files); }}>
    <summary><Sparkles size={14} />{t("Draft with AI")}</summary>
    <div className="knowledge-draft-body">
      {!turns.length && <p className="knowledge-meta">{t("Describe the schema you need, or attach sample files, and the model drafts one for you to check and edit.")}</p>}
      <ul className="knowledge-draft-turns" aria-live="polite">
        {turns.map((turn, index) => <li key={index} className={`is-${turn.role}`}>
          <span>{turn.content}</span>
          {turn.draft && <div className="knowledge-draft-card">
            <strong>{turn.draft.name}</strong>
            <small>{t("{v0} properties", { v0: Object.keys((turn.draft.definition.properties ?? {}) as object).length })}</small>
            <button type="button" className="knowledge-primary" disabled={busy}
              onClick={() => { onApply(turn.draft!); if (pending === turn.draft) setPending(undefined); }}>{t("Apply to editor")}</button>
          </div>}
        </li>)}
        {busy && <li className="is-assistant knowledge-meta">{t("Drafting…")}</li>}
      </ul>
      {!!samples.length && <ul className="knowledge-draft-files">
        {samples.map(item => <li key={item.id} className={`is-${item.state}`}>
          <span>{item.filename}</span>
          <small>{item.state === "converting" ? t("converting…") : item.state === "failed" ? item.error
            : item.truncated ? t("ready (shortened)") : t("ready")}</small>
          <button type="button" aria-label={t("Remove {v0}", { v0: item.filename })} onClick={() => setSamples(previous => previous.filter(sample => sample.id !== item.id))}><X size={12} /></button>
        </li>)}
      </ul>}
      {error && <small className="knowledge-error" role="alert">{error}</small>}
      <textarea aria-label={t("Message")} rows={3} maxLength={8000} value={input} disabled={busy}
        placeholder={t("e.g. One production batch per row; I need yield, ionic conductivity (mS/cm) and an XRD pass/fail.")}
        onChange={event => setInput(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) void send(); }} />
      <div className="knowledge-formbar">
        <input ref={picker} type="file" multiple hidden aria-label={t("Attach sample files")}
          onChange={event => { if (event.target.files) attach(event.target.files); event.target.value = ""; }} />
        <button type="button" disabled={busy || samples.length >= MAX_SAMPLES} onClick={() => picker.current?.click()}>
          <Paperclip size={14} />{t("Attach files")}</button>
        {api.modelSelect}
        {onUndo && <button type="button" onClick={onUndo}><Undo2 size={14} />{t("Undo apply")}</button>}
        <button type="button" className="knowledge-primary" disabled={!canSend} onClick={() => void send()}>
          <Send size={14} />{t("Send")}</button>
      </div>
      {!api.ready && <small className="knowledge-meta">{t("Choose a model to draft a schema.")}</small>}
    </div>
  </details>;
}
