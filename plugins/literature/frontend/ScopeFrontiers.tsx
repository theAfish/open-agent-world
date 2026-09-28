import { useState } from "react";
import { useLocale, t } from "@oaw/plugin-api";
import { PaperPortal } from "../../library/frontend/PaperPortal";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import type { ResearchScopeDoc } from "./index";
import "./ScopeFrontiers.css";
import {ResearchBudgetStatus,researchError} from './ResearchBudgetStatus';
import {useAutoResearch} from '../../../frontend/src/state/autoResearch';
import {useNodeSurfaceStore} from '../../../frontend/src/state/nodeSurfaces';

export type FrontierRecord = {
  id: string; scope_revision: number; query: string; rationale: string;
  missing_evidence: string[]; source_paper_ids: string[]; paper_ids?: string[];
  proposed_by?: string; proposal_kind?: string; discovery_state: string; evidence_state: string;
  stale?: boolean; request_ids?: string[]; budget_used?: { searches?: number; papers?: number };
  budget?: { max_searches?: number; max_papers?: number }; error?: string;
  evaluation?: { status: string; reason_code?: string; rubric_version?: string; stale?: boolean; evaluated_at?: string;
    rule_priority?: { score: number; explanations?: Record<string,string> };
    jev_score?: { model: string; weighted_score: number } | null } | null;
};

export const discoveryLabel = (state: string) => ({ unsearched:"待探索", searching:"正在检索", found:"已找到题录", no_results:"已检索 · 无结果", filtered_empty:"已检索 · 无符合条件结果", failed:"检索失败", cancelled:"已取消" }[state] ?? state);
export const evidenceLabel = (state: string) => ({ none:"尚无证据", abstract_only:"仅来源摘要", located_unreviewed:"已定位 · 待核验", reviewed_support:"有核验记录", conflicted:"存在冲突", insufficient:"证据不足" }[state] ?? state);

// Keep an uncertain submission's idempotency key when a view is closed/reopened.
// Authoritative runs, budget reservations and their status still live in Scope.
const pendingExplorations = new Map<string,string>();

export async function scopeRequest(scopeId: string, action?: string, body?: unknown): Promise<any> {
  const response = await fetch(`/api/literature/scopes/${encodeURIComponent(scopeId)}${action ? `/${action}` : ""}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(researchError(value));
  return value;
}

export function ScopeFrontiers({ scopeId, doc, reload, selectedId, onSelect, onlySelected = false, creationOnly = false }: {
  scopeId: string; doc: ResearchScopeDoc; reload: () => Promise<ResearchScopeDoc>;
  selectedId?: string; onSelect?: (id: string) => void; onlySelected?: boolean; creationOnly?: boolean;
}) {
  useLocale();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(creationOnly);
  const [query, setQuery] = useState("");
  const [gap, setGap] = useState("");
  const [rationale, setRationale] = useState("");
  const [paperId, setPaperId] = useState<string>();
  const [continueFrom,setContinueFrom] = useState<string>();
  const frontiers = (doc.value.frontiers ?? []) as FrontierRecord[];
  const visible = creationOnly ? [] : onlySelected && selectedId ? frontiers.filter(route => route.id === selectedId) : frontiers;
  const current = doc.value.revisions.at(-1);
  const names = useWorldStore(state => state.cards);

  async function act(action: "explore" | "evaluate_frontier", route: FrontierRecord) {
    setBusy(`${route.id}:${action}`); setError("");
    try {
      const live = await reload();
      const arguments_: Record<string,unknown> = { frontier_id: route.id };
      const requestKey = `${scopeId}:${route.id}`;
      if (action === "explore") {
        if (!pendingExplorations.has(requestKey)) pendingExplorations.set(requestKey, crypto.randomUUID());
        arguments_.request_id = pendingExplorations.get(requestKey);
      }
      await scopeRequest(scopeId, action, { expected_revision: live.revision, arguments: arguments_ });
      if (action === "explore") pendingExplorations.delete(requestKey);
      await reload();
      if (action === "explore") await useWorldStore.getState().refreshWorld();
      window.dispatchEvent(new CustomEvent("oaw-research-updated", { detail: scopeId }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      await reload().catch(() => {});
    } finally { setBusy(""); }
  }

  async function resumeScope() {
    setBusy("resume"); setError("");
    try {
      const live = await reload();
      await scopeRequest(scopeId,"resume",{expected_revision:live.revision,arguments:{}});
      await reload();
      window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault(); setBusy("create"); setError("");
    try {
      const live = await reload();
      const next: ResearchScopeDoc = await scopeRequest(scopeId, "frontier", { expected_revision: live.revision,
        arguments: { query: query.trim(), missing_evidence: gap.split("\n").map(value => value.trim()).filter(Boolean), rationale: rationale.trim(), ...(continueFrom ? {continue_from:continueFrom} : {}) } });
      await reload(); await useWorldStore.getState().refreshWorld(); setAdding(false); setContinueFrom(undefined); setQuery(""); setGap(""); setRationale("");
      const created = next.value.frontiers.at(-1); if (created?.id) onSelect?.(created.id);
      window.dispatchEvent(new CustomEvent("oaw-research-updated", { detail: scopeId }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  }

  return <section className="scope-frontiers nodrag nopan nowheel" aria-label={t("探索方向")}>
    <header><div><small>{t("探索方向")} · {frontiers.length}</small><h4>{t("从证据缺口继续")}</h4></div><button type="button" disabled={!current || !!busy} onClick={() => { setAdding(value => !value); setContinueFrom(undefined); setQuery(""); setGap(""); setRationale(""); }}>{adding ? t("取消") : t("添加路标")}</button></header>
    {!current && <p>{t("先保存研究问题、边界和预算，再提出探索方向。")}</p>}
    {current && <ResearchBudgetStatus doc={doc} onReview={()=>{if(useAutoResearch.getState().enabled)useAutoResearch.setState({scopeId,panel:'scope'});else useNodeSurfaceStore.getState().openWorkspace(scopeId);}}/>}
    {doc.value.paused && current && <div><p>{t("研究范围已暂停。恢复范围后，可手动选择方向发起检索。")}</p><button type="button" disabled={!!busy} onClick={() => void resumeScope()}>{t("恢复研究范围")}</button></div>}
    {error && <p role="alert" className="scope-frontiers-error">{error}</p>}
    {adding && <form onSubmit={create}>
      {continueFrom && <p>{t('续接会创建当前范围的后续路标，并保留原路标、文献和检索记录。请确认检索问题仍适用。')}</p>}
      <label>{t("下一步检索问题")}<input required maxLength={1000} value={query} onChange={event => setQuery(event.target.value)} placeholder={current?.question}/></label>
      <label>{t("尚缺什么证据（每行一项）")}<textarea required maxLength={5000} value={gap} onChange={event => setGap(event.target.value)}/></label>
      <label>{t("为什么沿此方向探索")}<textarea required maxLength={5000} value={rationale} onChange={event => setRationale(event.target.value)}/></label>
      <small>{t("继承当前范围边界与预算。保存路标不会立即发起检索。")}</small>
      <button disabled={!!busy || !query.trim() || !gap.trim() || !rationale.trim()}>{busy === "create" ? t("正在保存…") : t("保存探索方向")}</button>
    </form>}
    {current && !frontiers.length && !adding && <p>{t("目前没有探索路标。写下一个缺失证据及其检索问题，把它作为下一步；不会自动生成研究结论。")}</p>}
    {visible.map(route => {
      const stale = route.stale || route.scope_revision !== doc.value.current_revision;
      const running = route.discovery_state === "searching";
      const exhausted = (route.budget?.max_searches !== undefined && (route.budget_used?.searches ?? 0) >= route.budget.max_searches) || (route.budget?.max_papers !== undefined && (route.budget_used?.papers ?? 0) >= route.budget.max_papers);
      const pending = pendingExplorations.has(`${scopeId}:${route.id}`);
      const evaluation = route.evaluation;
      const sources = [...new Set([...(route.source_paper_ids ?? []), ...(route.paper_ids ?? [])])];
      return <article key={route.id} data-selected={selectedId === route.id}>
        <button className="scope-frontier-heading" type="button" onClick={() => onSelect?.(route.id)}><strong>{route.query}</strong><small>r{route.scope_revision}{stale ? ` · ${t("范围已变更")}` : ""}</small></button>
        <div className="scope-frontier-states"><span>{t(discoveryLabel(route.discovery_state))}</span><span>{t(evidenceLabel(route.evidence_state))}</span></div>
        <p>{route.rationale}</p>
        <ul>{route.missing_evidence.map((item,index) => <li key={index}>{item}</li>)}</ul>
        <small>{t("已用检索")} {route.budget_used?.searches ?? 0}/{route.budget?.max_searches ?? "—"} · {t("候选名额")} {route.budget_used?.papers ?? 0}/{route.budget?.max_papers ?? "—"}</small>
        {sources.length > 0 && <details><summary>{t("查看依据与已有文献")} · {sources.length}</summary>{sources.map(id => <button key={id} type="button" className="scope-frontier-paper" onClick={() => setPaperId(id)}>{names.find(card => card.id === id)?.name ?? id}</button>)}</details>}
        {evaluation && <details className="scope-frontier-evaluation"><summary>{evaluation.stale ? t("历史评分 · 需要重新评估") : <>{t("规则优先级")} {evaluation.rule_priority ? `${evaluation.rule_priority.score.toFixed(1)} / 100` : t("未计算")}</>}</summary>
          {evaluation.stale && <p>{t("来源或研究记录已改变；下列评分仅保留为历史记录。")}{evaluation.rule_priority ? ` ${evaluation.rule_priority.score.toFixed(1)} / 100` : ""}</p>}
          <small>{t("排序依据，不是正确率或发现概率。")}</small>
          {Object.entries(evaluation.rule_priority?.explanations ?? {}).map(([key,value]) => <p key={key}><b>{({relevance:t("范围相关"),evidence_gap:t("证据缺口"),testability:t("可检验性"),cost:t("成本")})[key] ?? key}</b> · {value}</p>)}
          <p>{evaluation.jev_score ? `${t("Jev 量表分")} ${evaluation.jev_score.weighted_score.toFixed(3)} / 1 · ${evaluation.jev_score.model}` : `${t("Jev 不可用")} · ${evaluation.reason_code ?? evaluation.status}`}</p>
          <small>{evaluation.rubric_version}</small>
          {evaluation.evaluated_at && <small> · {evaluation.evaluated_at}</small>}
        </details>}
        {route.error && <p role="status">{route.error}</p>}
        {stale && <small>{t("此路标属于旧范围版本，可审阅后续接到当前范围。")}</small>}
        {(stale || exhausted || route.discovery_state === 'failed' || route.discovery_state === 'cancelled') && <button type="button" disabled={!!busy} onClick={()=>{setContinueFrom(route.id);setQuery(route.query);setGap(route.missing_evidence.join('\n'));setRationale(route.rationale);setAdding(true);}}>{t('审阅并续接方向')}</button>}
        {exhausted && <small>{t("本方向预算已用完。继续探索前，请重新审阅范围与预算。")}</small>}
        <footer><button type="button" disabled={!!busy || stale || running || doc.value.paused || (exhausted && !pending)} onClick={() => void act("explore",route)}>{running || busy === `${route.id}:explore` ? t("正在检索…") : pending && exhausted ? t("确认上次检索结果") : t("沿此方向探索")}</button><button type="button" disabled={!!busy || stale} onClick={() => void act("evaluate_frontier",route)}>{busy === `${route.id}:evaluate_frontier` ? t("正在评估…") : t("评估优先级")}</button></footer>
      </article>;
    })}
    <small>{t("题录、来源证据与科学核验分开记录。无检索结果不代表没有相关研究。")}</small>
    {paperId && <PaperPortal paperId={paperId} onClose={() => setPaperId(undefined)}/>}
  </section>;
}
