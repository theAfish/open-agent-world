import { memo, useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { t, useLocale } from "../i18n";
import type { SavedModelThinking } from "../types/world";
import type { RunActivityItem } from "../state/runActivity";
import { worldApi, apiErrorMessage } from "../api/client";

function reasoningForDisplay(text: string, streaming?: boolean): string {
  if (streaming) return text;
  // Older AtomSculptor runs saved token-sized thought parts with an added blank
  // line between every part. Repair only that distinctive pattern on display;
  // leave genuine multi-paragraph reasoning and the stored event untouched.
  const fragments = text.split(/\r?\n\r?\n/);
  if (fragments.length < 8) return text;
  const short = fragments.filter(fragment => fragment.trim().length <= 24).length;
  const spaced = fragments.slice(1).filter((fragment, index) =>
    /^\s/.test(fragment) || /\s$/.test(fragments[index])).length;
  return short / fragments.length >= 0.75 && spaced / (fragments.length - 1) >= 0.4
    ? fragments.join("") : text;
}

export const ReasoningActivity = memo(function ReasoningActivity({ item, runId, active }: { item: RunActivityItem; active: boolean; runId?: string }) {
  useLocale();
  const [choice, setChoice] = useState<boolean>();
  const open = choice ?? (active && item.streaming === true);
  const [full, setFull] = useState<SavedModelThinking>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const needsFull = open && (choice === true || !item.text || Boolean(item.truncated || item.preview_truncated));
  useEffect(() => {
    if (!needsFull || !runId || !item.provider_message_id) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const row = await worldApi.getRunReasoningText(runId!, item.provider_message_id!);
        if (current) { setFull(row); setError(""); }
      } catch (cause) { if (current) setError(apiErrorMessage(cause)); }
      finally { if (current && active) timer = setTimeout(() => void read(), 2000); }
    }
    void read();
    return () => { current = false; clearTimeout(timer); };
  }, [needsFull, runId, item.provider_message_id, active, retry]);
  const body = full && full.run_id === runId && full.provider_message_id === item.provider_message_id
    && (Date.parse(full.updated_at) >= Date.parse(item.updated_at ?? "") || full.text.startsWith(item.text ?? "")) ? full : undefined;
  return <details className="run-model-reasoning" data-activity-id={item.id} open={open}
    onToggle={event => { if (event.target === event.currentTarget && event.currentTarget.open !== open) setChoice(event.currentTarget.open); }}>
    <summary><span>{t("Model thinking")}{item.role ? ` · ${item.role.replaceAll("_", " ")}` : ""}{item.model_request ? ` #${item.model_request}` : ""}{item.model_attempt ? ` · ${t("Model attempt")} ${item.model_attempt}` : ""}{item.streaming ? ` · ${t("Live")}` : item.interrupted ? ` · ${t("Incomplete preview")}` : ""}</span><ChevronRight size={12} aria-hidden="true" /></summary>
    <pre>{body ? body.text : reasoningForDisplay(item.text ?? "", item.streaming)}</pre>
    {open && !body && !item.text && !error && <p role="status">{t('Loading activity…')}</p>}
    {(body ? body.truncated : item.truncated || item.preview_truncated) ? <small>{t("Thinking text truncated")}</small> : null}
    {error ? <p role="alert">{error} <button onClick={() => setRetry(value => value + 1)}>{t("Retry")}</button></p> : null}
  </details>;
});

