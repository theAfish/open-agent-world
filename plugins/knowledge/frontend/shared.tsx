import { useEffect, useState } from "react";
import type { PluginViewProps } from "@oaw/plugin-api";

export type Source = { paper: string; page: number; quote: string | null; level: "quote" | "page"; status: "fresh" | "stale"; cite: string };
export type LogEntry = { at: string; actor: string; op: string; records: string[]; note: string };
export const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Citations of one record: cite, verification level, stale marker and the quote. */
export function Sources({ sources }: { sources: Source[] }) {
  if (!sources.length) return <span className="knowledge-muted">No sources (entered by the user)</span>;
  return <ul className="knowledge-sources">{sources.map((source, index) => <li key={index}>
    <code>{source.cite}</code>
    <span className={`knowledge-tag knowledge-${source.status}`}>{source.status}</span>
    {source.level === "page" && <span className="knowledge-tag">page only</span>}
    {source.quote && <q>{source.quote}</q>}
  </li>)}</ul>;
}

/** The store's write log (user-scoped ui_log action). */
export function WriteLog({ host }: Pick<PluginViewProps, "host">) {
  const [entries, setEntries] = useState<LogEntry[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    host.resourceAction("ui_log", { limit: 100 })
      .then(value => { if (active) setEntries((value as { entries: LogEntry[] }).entries); })
      .catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [host]);
  if (error) return <p role="alert">{error}</p>;
  if (!entries) return <p role="status">Loading log…</p>;
  if (!entries.length) return <p className="knowledge-muted">Nothing has been written yet.</p>;
  return <table className="knowledge-table"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Records</th><th>Note</th></tr></thead>
    <tbody>{entries.map((entry, index) => <tr key={index}><td>{entry.at}</td><td>{entry.actor}</td><td>{entry.op}</td>
      <td>{entry.records.slice(0, 5).join(", ")}{entry.records.length > 5 ? ` +${entry.records.length - 5}` : ""}</td><td>{entry.note}</td></tr>)}</tbody></table>;
}
