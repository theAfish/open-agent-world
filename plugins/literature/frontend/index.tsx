import { t, useLocale, type FrontendPlugin, type PluginViewProps } from "@oaw/plugin-api";
import { useEffect, useState } from "react";
import { PaperPortal, type SourceLocation } from "../../library/frontend/PaperPortal";
import { safePaperUrl } from "../../library/frontend/PaperMetadata";
import "./style.css";
import { ScopeEvidence } from "./ScopeEvidence";
import { ScopeSnapshots } from "./ScopeSnapshots";
import { ScopeFrontiers } from "./ScopeFrontiers";
import {PaperIntakeReview} from './PaperIntakeReview';
import {ResearchBudgetStatus,researchError} from './ResearchBudgetStatus';
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { ExplorationPanel, ExplorationSummary, ExplorationView, useExplorationScope } from "./Exploration";
import type { ExplorationEntity, ExplorationLink, ExplorationRoad, PathCamp } from "./explorationModel";

export type ResearchScopeDoc = {revision:number;value:{current_revision:number;revisions:{question:string;boundaries:string;inclusion:string[];exclusion:string[];seed_paper_ids:string[];start_year?:number;end_year?:number;budget:{max_searches?:number;max_papers?:number;max_duration_seconds?:number}}[];paper_ids:string[];search_runs:SearchRecord[];paused:boolean;archived_results?:unknown[];evidence_source_status?:Record<string,string>;evidence:any[];methods:any[];snapshots:any[];frontiers:any[];knowledge_id?:string;task_board_id?:string;exploration_nodes?:ExplorationEntity[];exploration_links?:ExplorationLink[];exploration_roads?:ExplorationRoad[];path_camps?:PathCamp[];search_budgets:Record<string,{reservations:unknown[];max_searches:number;max_candidate_slots:number}>}};
type SearchRecord={request_id:string;status:string;error?:string;scope_revision:number;paper_ids:string[];request:{query?:string;doi?:string};candidates?:{paper_id?:string;intake_status?:string;metadata:{title:string;doi?:string;authors:string[];year?:number;source_abstract?:string;source_url?:string};abstract_status:string;fulltext_status:string;fulltext_links:{url:string}[]}[]};
async function api(path:string,body?:unknown) {const response=await fetch(`/api/${path}`,body===undefined?undefined:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(researchError(result));return result;}

export function ScopePanel({scopeId,compact=false,hub=false}:{scopeId:string;compact?:boolean;hub?:boolean}) {
  useLocale();
  const [doc,setDoc]=useState<ResearchScopeDoc>(),[error,setError]=useState(""),[busy,setBusy]=useState(false),[query,setQuery]=useState("");
  const [question,setQuestion]=useState(""),[boundaries,setBoundaries]=useState(""),[maxSearches,setMaxSearches]=useState(10),[maxPapers,setMaxPapers]=useState(30),[maxMinutes,setMaxMinutes]=useState(10);
  const [editing,setEditing]=useState(false),[paper,setPaper]=useState<{id:string;source?:SourceLocation}>();
  const [seeds,setSeeds]=useState<string[]>([]);
  const availablePapers=useWorldStore(state=>state.cards).filter(card=>card.type==="library.paper");
  async function reload(){const value=await api(`literature/scopes/${scopeId}`);setDoc(value);return value as ResearchScopeDoc;}
  useEffect(()=>{const update=(event:Event)=>{if((event as CustomEvent).detail===scopeId)void reload().catch(reason=>setError(researchError(reason instanceof Error?reason.message:reason)));};window.addEventListener('oaw-research-updated',update);return()=>window.removeEventListener('oaw-research-updated',update);},[scopeId]);
  useEffect(()=>{let active=true;void api(`literature/scopes/${scopeId}`).then(value=>{if(active){setDoc(value);const current=value.value.revisions.at(-1);setSeeds(current?.seed_paper_ids??[]);setQuestion(current?.question??"");setBoundaries(current?.boundaries??"");setMaxSearches(current?.budget.max_searches??10);setMaxPapers(current?.budget.max_papers??30);setMaxMinutes((current?.budget.max_duration_seconds??600)/60);setEditing(!current);}}).catch(error=>{if(active)setError(String(error));});return()=>{active=false;};},[scopeId]);
  const current=doc?.value.revisions.at(-1),ledger=doc?.value.search_budgets[String(doc.value.current_revision)];
  async function saveScope(event:React.FormEvent){event.preventDefault();if(!doc)return;setBusy(true);setError("");try{await api(`nodes/${scopeId}/actions/revise`,{expected_revision:doc.revision,arguments:{...current,question,boundaries,seed_paper_ids:seeds,budget:{...current?.budget,max_searches:maxSearches,max_papers:maxPapers,max_duration_seconds:Math.round(maxMinutes*60),max_parallelism:1}}});await reload();setEditing(false);}catch(error){setError(String(error));}finally{setBusy(false);}}
  async function search(event:React.FormEvent){event.preventDefault();if(!doc||!current)return;setBusy(true);setError("");try{let live=await reload();if(live.value.paused)live=await api(`literature/scopes/${scopeId}/resume`,{expected_revision:live.revision,arguments:{}});await api(`literature/scopes/${scopeId}/search`,{expected_revision:live.revision,arguments:{request_id:crypto.randomUUID(),scope_revision:live.value.current_revision,query:query.trim()||current.question,rows:Math.min(5,maxPapers),...(current.start_year?{from_year:current.start_year}:{}),...(current.end_year?{until_year:current.end_year}:{})}});await reload();}catch(error){setError(String(error));await reload().catch(()=>{});}finally{setBusy(false);}}
  return <div className={`literature-scope nodrag nopan nowheel ${compact?"is-compact":""}`}>
    <header><div><small>{t("研究范围")} · r{doc?.value.current_revision??0}</small><h3>{current?.question||t("提出一个有边界的文献问题")}</h3></div><button disabled={busy} onClick={()=>{if(!editing&&current){setQuestion(current.question);setBoundaries(current.boundaries);setSeeds(current.seed_paper_ids);setMaxSearches(current.budget.max_searches??10);setMaxPapers(current.budget.max_papers??30);setMaxMinutes((current.budget.max_duration_seconds??600)/60);}setEditing(value=>!value);}}>{t("范围与预算")}</button></header>
    {error&&<p role="alert">{error}</p>}
    {editing&&<form className="literature-scope-form" onSubmit={saveScope}>
      <label>{t("研究问题")}<textarea required maxLength={10000} value={question} onChange={event=>setQuestion(event.target.value)}/></label>
      <label>{t("纳入边界与排除条件")}<textarea maxLength={20000} value={boundaries} onChange={event=>setBoundaries(event.target.value)}/></label>
      {availablePapers.length>0&&<label>{t("已有种子论文")}<select aria-label={t("已有种子论文")} multiple value={seeds} onChange={event=>setSeeds(Array.from(event.target.selectedOptions,option=>option.value))}>{availablePapers.map(card=><option value={card.id} key={card.id}>{card.name}</option>)}</select></label>}
      <div><label>{t("检索次数上限")}<input type="number" min="1" max="100" value={maxSearches} onChange={event=>setMaxSearches(Number(event.target.value))}/></label><label>{t("候选篇数预算")}<input type="number" min="1" max="500" value={maxPapers} onChange={event=>setMaxPapers(Number(event.target.value))}/></label></div>
      <label>{t("检索时间窗口（分钟）")}<input type="number" min="1" max="1440" value={maxMinutes} onChange={event=>setMaxMinutes(Number(event.target.value))}/></label><small>{t("保存为新范围版本；保留原版本预算、路标和检索记录。时间窗口从首次检索开始。")}</small>
      <button disabled={busy} type="submit">{t("保存研究范围")}</button>
    </form>}
    {doc && <ResearchBudgetStatus doc={doc} onReview={()=>setEditing(true)}/> }
    {current&&<><p className="literature-boundary">{current.boundaries}</p><div className="literature-scope-stats"><span>{doc?.value.paper_ids.length} {t("篇文献")}</span><span>{t("已预约检索")} {ledger?.reservations.length??0}/{ledger?.max_searches??current.budget.max_searches??20}</span><span>{doc?.value.paused?t("已暂停"):t("可检索")}</span></div>
      <form className="literature-search" onSubmit={search}><input aria-label={t("检索词")} placeholder={t("输入检索词；留空使用研究问题")} value={query} onChange={event=>setQuery(event.target.value)}/><button disabled={busy||!doc}>{busy?t("正在检索…"):t("检索 Crossref")}</button></form>
      <small>{t("仅检索公开题录。摘要来自登记来源；不会上传 PDF 或调用付费模型。")}</small>
      <button onClick={async()=>{try{const live=await reload();await api(`literature/scopes/${scopeId}/${live.value.paused?"resume":"pause"}`,{expected_revision:live.revision,arguments:{}});await reload();window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));}catch(error){setError(String(error));}}}>{doc?.value.paused?t("恢复检索"):t("暂停检索")}</button>
    </>}
    {!!doc?.value.archived_results?.length&&<p>{t("这是复制的研究范围；旧结果保留为历史档案，当前检索与证据从新记录开始。")}</p>}
    {doc&&!hub&&<ExplorationSummary scopeId={scopeId} doc={doc} reload={reload}/>}
    {doc&&<ScopeFrontiers scopeId={scopeId} doc={doc} reload={reload}/>}
    {doc&&<ScopeSnapshots scopeId={scopeId} doc={doc} reload={reload} openPaper={(id,source)=>setPaper({id,source})}/>}
    {doc&&<ScopeEvidence scopeId={scopeId} doc={doc} reload={reload} openPaper={(id,source)=>setPaper({id,source})}/>}
    <div className="literature-results">{[...(doc?.value.search_runs??[])].reverse().map(run=><section key={run.request_id}>
      <header><strong>{run.request.query||run.request.doi}</strong><small>{run.status} · r{run.scope_revision}</small></header>{run.error&&<p>{run.error}</p>}
      {run.candidates?.map((candidate,index)=>{const id=candidate.paper_id,source=safePaperUrl(candidate.metadata.source_url);return <article key={candidate.metadata.doi??index}>
        <button className="literature-paper-title" disabled={!id} onClick={()=>id&&setPaper({id})}>{candidate.metadata.title}</button>
        <small>{candidate.metadata.authors.slice(0,3).join(" · ")} · {candidate.metadata.year??"?"}</small>
        {candidate.intake_status==="outside_scope_years"&&<small>{t("超出年份范围，未纳入")}</small>}
        {!id&&candidate.intake_status!=="outside_scope_years"&&run.status==="complete"&&<small>{t("需要手动关联已有论文")}</small>}
        {candidate.metadata.source_abstract?<details><summary>{t("来源摘要")}</summary><p>{candidate.metadata.source_abstract}</p></details>:<small>{t("暂无来源摘要")}</small>}
        <footer>{id&&<button onClick={()=>setPaper({id})}>{t("打开 Paper / 导入全文")}</button>}{source&&<a href={source} target="_blank" rel="noopener noreferrer">{t("原文页面")} ↗</a>}{candidate.fulltext_links?.slice(0,1).map(link=>{const url=safePaperUrl(link.url);return url&&<a key={url} href={url} target="_blank" rel="noopener noreferrer">{t("登记的全文链接（访问未核验）")} ↗</a>;})}</footer>
      </article>;})}
      {run.status==="complete"&&!run.candidates?.length&&<p>{t("本次检索未返回候选；不代表该方向没有研究。")}</p>}
    </section>)}</div>
    {paper&&<PaperPortal paperId={paper.id} sourceLocation={paper.source} onClose={()=>setPaper(undefined)}/>}
  </div>;
}

export function ResearchHub({scopeId,compact=false,cardId,initialTab='directory'}:{scopeId:string;compact?:boolean;cardId?:string;initialTab?:'directory'|'settings'}) {
  useLocale();
  const [tab,setTab]=useState<'directory'|'settings'|'intake'>(initialTab);
  const [paper,setPaper]=useState<string>();
  const {doc,error,reload}=useExplorationScope(scopeId);
  const current=doc?.value.revisions.at(-1);
  const ledger=doc?.value.search_budgets[String(doc.value.current_revision)];
  return <section className="research-hub nodrag nopan nowheel" aria-label={t('研究中枢')}>
    <header className="research-hub-heading"><small>{t('研究中枢')}</small><strong>{current?.question ?? t('设置研究问题与预算')}</strong>
      {doc && <span>{doc.value.paper_ids.length} {t('篇文献')} · {t('已预约检索')} {ledger?.reservations.length ?? 0}/{ledger?.max_searches ?? current?.budget.max_searches ?? '—'} · {t(doc.value.paused ? '已暂停' : '可检索')}</span>}
    </header>
    <nav className="research-hub-tabs" aria-label={t('研究中枢栏目')}><button aria-pressed={tab==='directory'} onClick={()=>setTab('directory')}>{t('目录与道路')}</button><button aria-pressed={tab==='settings'} onClick={()=>setTab('settings')}>{t('问题与预算')}</button><button aria-pressed={tab==='intake'} onClick={()=>setTab('intake')}>{t('文献整理')}</button></nav>
    {tab==='directory' ? <ExplorationPanel scopeId={scopeId} compact={compact} cardId={cardId} onSettings={()=>setTab('settings')}/> : tab==='intake' ? <PaperIntakeReview scopeId={scopeId} revision={doc?.revision} onChanged={reload} openPaper={setPaper}/> : <ScopePanel scopeId={scopeId} compact={compact} hub/>}
    {paper && <PaperPortal paperId={paper} onClose={()=>setPaper(undefined)}/>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
function Scope({card,level}:PluginViewProps){return <ResearchHub scopeId={card.id} compact={level!=="workspace"} cardId={card.id}/>;}
export default {apiVersion:1,views:{scope:Scope,index:ExplorationView,trail:ExplorationView,finding:ExplorationView}} satisfies FrontendPlugin;
