import { Bot } from "lucide-react";
import type { ContextStatus } from "../types/world";
import { t } from "../i18n";

/** One quiet indicator per persistent participant, never per message. */
export function ContextAvatar({ status }: { status?: ContextStatus }) {
  const pressure = status && Number.isFinite(status.pressure) ? Math.max(0, Math.min(1, status.pressure)) : undefined;
  const title = pressure === undefined ? undefined : t("Context {v0}% · compacted {v1} times", {
    v0: String(Math.round(pressure * 100)), v1: String(status?.compaction_count ?? 0),
  });
  return <span className={`context-avatar${pressure === undefined ? "" : ` has-pressure is-${status?.state}`}`}
    title={title} aria-label={title} data-context-state={pressure === undefined ? undefined : status?.state}>
    <Bot size={12} aria-hidden="true" />
    {pressure !== undefined && <svg className="context-pressure-ring" viewBox="0 0 32 32" aria-hidden="true">
      <circle className="context-pressure-track" cx="16" cy="16" r="14" />
      <circle className="context-pressure-fill" cx="16" cy="16" r="14" pathLength="100"
        strokeDasharray="100" strokeDashoffset={100 - pressure * 100} />
    </svg>}
  </span>;
}
