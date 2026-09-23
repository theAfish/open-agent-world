import { useCallback, useEffect, useState } from "react";
import type { PluginViewProps } from "@oaw/plugin-api";
import { Sources, WriteLog, message, type Source } from "./shared";
import "./vectors.css";

type Job = { id: string; state: "running" | "done" | "failed" | "interrupted"; op: string; papers: string[]; model: string;
  passages: number; embedded: number; error: string | null; started_at: string; finished_at: string | null; started_by: string };
type PaperRow = { paper: string; page_passages: number; notes: number; pages: number; with_vectors: number; stale: number; updated_at: string };
export type Status = { mode: "hybrid" | "lexical"; embedding: { configured: boolean; model: string | null; endpoint: string | null };
  passages: number; page_passages: number; notes: number; with_vectors: number; without_vectors: number; max_passages: number;
  papers: PaperRow[]; models: { model: string; dim: number; vectors: number; searched: boolean }[]; jobs: Job[]; hint?: string };
type Hit = { passage: number; kind: "page" | "agent_note"; paper: string; page: number; heading: string; cite: string; text: string;
  score: number; scores: { lexical_rank?: number; bm25?: number; dense_rank?: number; cosine?: number }; stale: boolean; sources?: Source[] };
type SearchResult = { mode: string; model: string | null; results: Hit[]; warning?: string; hint?: string };
type Tab = "search" | "papers" | "jobs" | "log";

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function Preview({ host, card }: PluginViewProps) {
  const [status, setStatus] = useState<Status>();
  useEffect(() => {
    let active = true;
    host.resourceAction("ui_status", {}).then(value => { if (active) setStatus(value as Status); }).catch(() => {});
    return () => { active = false; };
  }, [host, card.id]);
  return <div className="knowledge-preview">
    <span className="knowledge-badge">vector store</span>
    {status ? <>
      <strong>{plural(status.passages, "passage")} · {plural(status.papers.filter(p => p.page_passages).length, "Paper")}</strong>
      <span className="knowledge-muted">{status.mode === "hybrid" ? `hybrid search · ${status.embedding.model}` : "lexical search (BM25)"}</span>
      {status.jobs.some(job => job.state === "running") && <span className="knowledge-muted">embedding…</span>}
    </> : <span className="knowledge-muted">…</span>}
  </div>;
}

export function Workspace({ host }: PluginViewProps) {
  const [status, setStatus] = useState<Status>();
  const [tab, setTab] = useState<Tab>("search");
  const [error, setError] = useState("");
  const refresh = useCallback(() => host.resourceAction("ui_status", {})
    .then(value => { setStatus(value as Status); setError(""); }).catch(reason => setError(message(reason))), [host]);
  useEffect(() => { void refresh(); }, [refresh]);
  const running = !!status?.jobs.some(job => job.state === "running");
  useEffect(() => {  // Follow background embedding until it settles.
    if (!running) return;
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [running, refresh]);
  const remove = async (args: { passages?: number[]; papers?: string[] }) => {
    try { await host.resourceAction("ui_remove", { ...args, note: "Removed in the workspace" }); }
    catch (reason) { setError(message(reason)); return false; }
    await refresh();
    return true;
  };
  return <div className="knowledge-app vectors-app">
    <header className="vectors-header">
      {status ? <>
        <span className={`knowledge-tag vectors-mode vectors-${status.mode}`} title={status.embedding.endpoint ?? "No embedding service configured"}>
          {status.mode === "hybrid" ? "hybrid" : "lexical only"}</span>
        <span>{status.embedding.model ?? "no embedding model"}</span>
        <span className="knowledge-muted">{plural(status.passages, "passage")} ({status.page_passages} page, {status.notes} notes) · {status.with_vectors} with vectors · max {status.max_passages}</span>
        {running && <span role="status">Embedding…</span>}
      </> : <span role="status">Loading…</span>}
      <button type="button" onClick={() => void refresh()}>Refresh</button>
    </header>
    {status?.hint && <p className="knowledge-muted vectors-hint">{status.hint}</p>}
    {error && <p role="alert">{error}</p>}
    <div className="knowledge-tabs" role="tablist">
      {(["search", "papers", "jobs", "log"] as Tab[]).map(name => <button key={name} type="button" role="tab" aria-selected={tab === name}
        onClick={() => setTab(name)}>{name === "papers" ? `Papers (${status?.papers.length ?? 0})` : name[0].toUpperCase() + name.slice(1)}</button>)}
    </div>
    {tab === "search" && <Search host={host} onRemove={passage => remove({ passages: [passage] })} />}
    {tab === "papers" && status && <Papers status={status} onRemove={paper => remove({ papers: [paper] })} />}
    {tab === "jobs" && status && <Jobs status={status} />}
    {tab === "log" && <WriteLog host={host} />}
  </div>;
}

function Search({ host, onRemove }: Pick<PluginViewProps, "host"> & { onRemove: (passage: number) => Promise<boolean> }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("");
  const [result, setResult] = useState<SearchResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (event?: React.FormEvent) => {
    event?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    try {
      setResult(await host.resourceAction("ui_search", { query, limit: 20, ...(mode ? { mode } : {}) }) as SearchResult);
      setError("");
    } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  };
  return <section className="vectors-search">
    <form onSubmit={run} className="vectors-form">
      <input aria-label="Search passages" value={query} placeholder="Search passages…" onChange={event => setQuery(event.target.value)} />
      <select aria-label="Search mode" value={mode} onChange={event => setMode(event.target.value)}>
        <option value="">auto</option><option value="hybrid">hybrid</option><option value="dense">dense</option><option value="lexical">lexical</option>
      </select>
      <button type="submit" disabled={busy || !query.trim()}>Search</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p className="knowledge-muted">Searched {result.mode}{result.model ? ` with ${result.model}` : ""} · {plural(result.results.length, "result")}</p>
      {result.warning && <p className="vectors-warning">{result.warning}</p>}
      {!result.results.length && <p className="knowledge-muted">{result.hint ?? "No matching passages."}</p>}
      <ol className="vectors-results">{result.results.map(hit => <li key={hit.passage}>
        <div className="vectors-hit-head">
          <code>{hit.cite}</code>
          <span className="knowledge-tag">{hit.kind === "page" ? "page text" : "agent note"}</span>
          {hit.stale && <span className="knowledge-tag knowledge-stale">stale</span>}
          {hit.heading && <strong>{hit.heading}</strong>}
          <span className="vectors-scores knowledge-muted">{scoreText(hit)}</span>
          <button type="button" onClick={() => void onRemove(hit.passage).then(removed => { if (removed) setResult(r => r && { ...r, results: r.results.filter(h => h.passage !== hit.passage) }); })}
            aria-label={`Remove passage ${hit.passage}`}>Remove</button>
        </div>
        <p className="vectors-text">{hit.text}</p>
        {hit.sources && <Sources sources={hit.sources} />}
      </li>)}</ol>
    </>}
  </section>;
}

function scoreText(hit: Hit) {
  const parts = [`score ${hit.score.toFixed(4)}`];
  if (hit.scores.bm25 !== undefined) parts.push(`BM25 #${hit.scores.lexical_rank} (${hit.scores.bm25.toFixed(2)})`);
  if (hit.scores.cosine !== undefined) parts.push(`cosine #${hit.scores.dense_rank} (${hit.scores.cosine.toFixed(3)})`);
  return parts.join(" · ");
}

function Papers({ status, onRemove }: { status: Status; onRemove: (paper: string) => Promise<boolean> }) {
  const [confirming, setConfirming] = useState<string>();
  if (!status.papers.length) return <p className="knowledge-muted">No passages yet. A curating Agent adds Papers with ingest_papers.</p>;
  return <table className="knowledge-table"><thead><tr><th>Paper</th><th>Pages</th><th>Passages</th><th>Notes</th><th>With vectors</th><th>Stale</th><th /></tr></thead>
    <tbody>{status.papers.map(row => <tr key={row.paper}>
      <td><code>{row.paper}</code></td><td>{row.pages}</td><td>{row.page_passages}</td><td>{row.notes}</td><td>{row.with_vectors}</td>
      <td>{row.stale ? <span className="knowledge-tag knowledge-stale">{row.stale}</span> : 0}</td>
      <td>{row.page_passages > 0 && (confirming === row.paper
        ? <button type="button" onClick={() => void onRemove(row.paper).then(removed => { if (removed) setConfirming(undefined); })}>Confirm remove</button>
        : <button type="button" onClick={() => setConfirming(row.paper)} aria-label={`Remove pages of ${row.paper}`}>Remove pages</button>)}</td>
    </tr>)}</tbody></table>;
}

function Jobs({ status }: { status: Status }) {
  return <>
    {status.models.length > 0 && <table className="knowledge-table"><thead><tr><th>Model</th><th>Dim</th><th>Vectors</th><th>Searched</th></tr></thead>
      <tbody>{status.models.map(row => <tr key={`${row.model}:${row.dim}`}><td>{row.model}</td><td>{row.dim}</td><td>{row.vectors}</td>
        <td>{row.searched ? "yes" : "no (other model)"}</td></tr>)}</tbody></table>}
    {!status.jobs.length ? <p className="knowledge-muted">No embedding jobs yet.</p>
      : <table className="knowledge-table"><thead><tr><th>Started</th><th>What</th><th>State</th><th>Vectors</th><th>Model</th><th>Error</th></tr></thead>
        <tbody>{status.jobs.map(job => <tr key={job.id}><td>{job.started_at}</td><td>{job.op}{job.papers.length ? ` (${job.papers.length} Papers)` : ""}</td>
          <td><span className={`knowledge-tag vectors-job-${job.state}`}>{job.state}</span></td><td>{job.embedded}/{job.passages}</td><td>{job.model}</td>
          <td>{job.error}</td></tr>)}</tbody></table>}
  </>;
}
