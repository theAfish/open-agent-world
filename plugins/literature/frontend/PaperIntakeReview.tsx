import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useMemo, useState } from "react";
import "./PaperIntakeReview.css";

type Metadata = { title: string; doi?: string; authors: string[]; year?: number };
type Paper = { paper_id: string; paper_revision: number; revision: number; metadata: Metadata; canonical_metadata: Metadata;
  screening_status: string; screening_reason: string; reading_status: string; has_pdf: boolean; components: {doi: string; metadata: Metadata}[] };
type Preview = {id: string; status: string; proposals: {paper_id: string; status: string; canonical_doi?: string;
  original_metadata?: Metadata; candidate?: {canonical_metadata?: Metadata; metadata: Metadata}}[]};
type Intake = {revision: number; papers: Paper[]; previews: Preview[]; pending_candidates: {metadata: Metadata; parent_resolution_status?: string}[]};

async function api(path: string, body?: unknown) {
  const response = await fetch(`/api/literature/${path}`, body === undefined ? undefined : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail ?? value));
  return value;
}

function PaperRow({paper, busy, save, openPaper}: {paper: Paper; busy: boolean; save: (paper: Paper, status: string, reason: string, reading: string) => Promise<void>; openPaper?: (id: string) => void}) {
  const [status, setStatus] = useState(paper.screening_status), [reason, setReason] = useState(paper.screening_reason), [reading, setReading] = useState(paper.reading_status);
  useEffect(() => {setStatus(paper.screening_status);setReason(paper.screening_reason);setReading(paper.reading_status);}, [paper]);
  const metadata = paper.canonical_metadata;
  const component = metadata.doi !== paper.metadata.doi;
  return <article className="paper-intake-row">
    <header><strong>{metadata.title || metadata.doi || t("题录待补全")}</strong><span>{paper.has_pdf ? t("已有本地 PDF") : t("题录")}</span></header>
    <p className="paper-intake-meta">{metadata.authors.slice(0, 3).join("; ") || t("作者待核定")} · {metadata.year ?? t("年份待核定")}
      {metadata.doi && <> · <a href={`https://doi.org/${encodeURIComponent(metadata.doi)}`} target="_blank" rel="noopener noreferrer">{metadata.doi}</a></>}</p>
    {component && <p className="paper-intake-association">{t("保留的附件卡片")}：{paper.metadata.title || paper.metadata.doi} · {paper.metadata.doi}</p>}
    {paper.components.length > 0 && <details><summary>{t("关联补充材料")} ({paper.components.length})</summary>{paper.components.map(item => <p key={item.doi}><a href={`https://doi.org/${encodeURIComponent(item.doi)}`} target="_blank" rel="noopener noreferrer">{item.metadata.title || item.doi}</a> · {item.doi}</p>)}</details>}
    <div className="paper-intake-controls">
      <label>{t("筛选")}<select aria-label={t("文献筛选状态")} value={status} onChange={event => setStatus(event.target.value)} disabled={busy}>
        <option value="pending">{t("待筛选")}</option><option value="included">{t("纳入")}</option><option value="excluded">{t("排除")}</option></select></label>
      <label>{t("阅读进度")}<select aria-label={t("文献阅读进度")} value={reading} onChange={event => setReading(event.target.value)} disabled={busy}>
        <option value="unread">{t("未读")}</option><option value="screened">{t("已初筛")}</option><option value="close_read" disabled={!paper.has_pdf}>{t("已精读")}</option>{paper.reading_status === "verified" && <option value="verified">{t("既有核验状态")}</option>}</select></label>
      <input aria-label={t("文献筛选理由")} value={reason} maxLength={5000} placeholder={t("记录纳入或排除的理由")} onChange={event => setReason(event.target.value)} disabled={busy}/>
      <button disabled={busy || (status !== "pending" && !reason.trim())} onClick={() => void save(paper, status, reason, reading)}>{t("保存")}</button>
      {openPaper && <button onClick={() => openPaper(paper.paper_id)}>{component ? t("打开附件卡片") : t("打开 Paper")}</button>}
    </div>
  </article>;
}

export function PaperIntakeReview({scopeId, revision, onChanged, openPaper}: {scopeId: string; revision?: number; onChanged?: () => void | Promise<unknown>; openPaper?: (id: string) => void}) {
  useLocale();
  const [data, setData] = useState<Intake | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [filter, setFilter] = useState("all"), [preview, setPreview] = useState<Preview | null>(null);
  useEffect(() => {let active = true;setError("");void api(`scopes/${encodeURIComponent(scopeId)}/intake`).then(value => {if (active) setData(value);}).catch(cause => {if (active) setError(String(cause));});return () => {active = false;};}, [scopeId, revision]);
  const groups = useMemo(() => new Set(data?.papers.map(item => item.canonical_metadata.doi || item.paper_id)).size, [data]);
  async function refresh() {const result = await api(`scopes/${encodeURIComponent(scopeId)}/intake`);setData(result);await onChanged?.();}
  async function save(paper: Paper, status: string, reason: string, reading: string) {
    if (!data) return;setBusy(true);setError("");
    try {setData(await api(`scopes/${encodeURIComponent(scopeId)}/review_paper`, {expected_revision: data.revision,
      arguments: {paper_id: paper.paper_id, item_revision: paper.revision, paper_revision: paper.paper_revision,
        screening_status: status, screening_reason: reason, reading_status: reading}}));await onChanged?.();}
    catch(cause) {setError(String(cause));} finally {setBusy(false);}
  }
  async function normalize(apply: boolean) {
    if (!data) return;setBusy(true);setError("");
    try {const result = await api(`scopes/${encodeURIComponent(scopeId)}/${apply ? "repair_apply" : "repair_preview"}`, {
      expected_revision: data.revision, arguments: apply ? {preview_id: preview?.id} : {paper_ids: data.papers.slice(0, 20).map(paper => paper.paper_id)}});
      setPreview(result.preview);await refresh();}
    catch(cause) {setError(String(cause));} finally {setBusy(false);}
  }
  return <section className="paper-intake-review" aria-label={t("文献入库与筛选")}>
    <header><h4>{t("文献入库与筛选")}</h4><span>{groups} {t("项研究")} · {data?.papers.length ?? 0} {t("张卡片")}</span>
      <button disabled={busy} onClick={() => void refresh().catch(cause => setError(String(cause)))}>{t("刷新")}</button>
      <button disabled={busy || !data?.papers.length} onClick={() => void normalize(false)}>{busy ? t("正在处理…") : t("预览主文 / 附件整理")}</button></header>
    <p className="paper-intake-help">{t("整理最多检查前 20 张卡片，并使用当前范围的检索预算。先预览再应用；原有 PDF、笔记和连接均保留。筛选与阅读进度不代表科学结论已核验。")}</p>
    {error && <p role="alert">{error}</p>}
    {preview && <aside className="paper-intake-preview"><strong>{t("整理预览")}</strong><ul>{preview.proposals.map(item => <li key={item.paper_id}>
      {item.original_metadata?.doi || item.paper_id} → {item.canonical_doi || item.status}
      {item.candidate && <span> · {(item.candidate.canonical_metadata || item.candidate.metadata).title}</span>}</li>)}</ul>
      {preview.status === "applied" ? <p>{t("已应用；原卡片保留。")}</p> : <button disabled={busy || !preview.proposals.some(item => item.status === "ready")} onClick={() => void normalize(true)}>{t("应用这份整理")}</button>}</aside>}
    <label className="paper-intake-filter">{t("显示")}<select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">{t("全部")}</option><option value="pending">{t("待筛选")}</option><option value="included">{t("已纳入")}</option><option value="excluded">{t("已排除")}</option></select></label>
    {data?.papers.filter(paper => filter === "all" || paper.screening_status === filter).map(paper => <PaperRow key={paper.paper_id} paper={paper} busy={busy} save={save} openPaper={openPaper}/>)}
    {data?.pending_candidates.length ? <details><summary>{t("待解析主文的候选")} ({data.pending_candidates.length})</summary>{data.pending_candidates.map((item, index) => <p key={index}>{item.metadata.title || item.metadata.doi} · {item.parent_resolution_status}</p>)}</details> : null}
    {data && !data.papers.length && <p>{t("检索或关联文献后，可在此逐篇记录筛选理由与阅读进度。")}</p>}
  </section>;
}
