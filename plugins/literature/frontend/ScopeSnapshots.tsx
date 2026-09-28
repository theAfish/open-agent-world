import {t,useLocale} from "@oaw/plugin-api";
import {useEffect,useState} from "react";
import {useWorldStore} from "../../../frontend/src/state/worldStore";
import type {SourceLocation} from "../../library/frontend/PaperPortal";
import type {ResearchScopeDoc} from "./index";
import "./ScopeSnapshots.css";

type SnapshotSource={paper_id:string;basis:"metadata"|"abstract"|"fulltext";document_version_id?:string;metadata_sha256:string;inclusion_rationale:string};
type Snapshot={id:string;version:number;scope_revision:number;cutoff_at:string;sources:SnapshotSource[];limitations:string[];core_paper_ids:string[];
  coverage:{request_id:string;query:string;state:string;candidate_count:number;paper_ids:string[]}[];
  claims:{id:string;text:string;basis:string;kind:string;review_state:string;paper_ids:string[];limitations:string[]}[];
  narrative:{text:string}[];
  recommendations:{id:string;paper_id:string;level:"metadata"|"abstract"|"paragraph";reason:string;rationale:string;anchor?:SourceLocation}[];
};
type SnapshotItem={snapshot:Snapshot;recorded_by:string;mode:string;freshness:{status:"current"|"stale";reasons:string[]};diff?:{from_version:number;to_version:number;scope_revision_changed:boolean;added_paper_ids:string[];removed_paper_ids:string[];changed_evidence_ids:string[];added_claim_ids:string[];removed_claim_ids:string[];changed_claim_ids:string[]}};
type SnapshotList={revision:number;current_version:number;items:SnapshotItem[]};
const levels:Record<string,string>={metadata:"题录",abstract:"来源摘要",fulltext:"持有全文",paragraph:"已定位段落"};
const coverageStates:Record<string,string>={found:"已找到候选",no_results:"检索无结果",filtered_empty:"筛选后为空",failed:"检索失败",cancelled:"已取消"};
const reasons:Record<string,string>={located_evidence:"查看证据原文",method_coverage:"补全方法阅读",conflict:"检查相反证据",missing_evidence:"补齐原文证据"};
async function api(path:string,body:unknown,signal?:AbortSignal){
  const response=await fetch(`/api/${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal});
  const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==="string"?value.detail:JSON.stringify(value.detail??value));return value;
}

/** Host-frozen coverage and attributed records; never synthesize scientific claims in the UI. */
export function ScopeSnapshots({scopeId,doc,reload,openPaper}:{scopeId:string;doc:ResearchScopeDoc;reload:()=>Promise<unknown>;openPaper:(id:string,source?:SourceLocation)=>void}){
  useLocale();const cards=useWorldStore(state=>state.cards);
  const [result,setResult]=useState<SnapshotList>(),[selected,setSelected]=useState<number>(),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const root=`literature/scopes/${encodeURIComponent(scopeId)}`,hasScope=doc.value.revisions.length>0;
  useEffect(()=>{setSelected(undefined);setResult(undefined);setError("");},[scopeId]);
  useEffect(()=>{
    if(!hasScope)return;const controller=new AbortController();
    void api(`${root}/snapshots`,{arguments:{}},controller.signal).then((value:SnapshotList)=>{setResult(value);setSelected(previous=>value.items.some(item=>item.snapshot.version===previous)?previous:value.current_version);setError("");}).catch(error=>{if(!controller.signal.aborted)setError(String(error));});
    return()=>controller.abort();
  },[root,doc.revision,hasScope]);
  if(!hasScope)return null;
  const members=new Set([...doc.value.paper_ids,...(doc.value.revisions.at(-1)?.seed_paper_ids??[])]);
  const completed=doc.value.search_runs.some(run=>run.scope_revision===doc.value.current_revision&&["complete","failed","cancelled"].includes(run.status));
  const item=result?.items.find(value=>value.snapshot.version===selected),snapshot=item?.snapshot;
  const title=(id:string)=>cards.find(card=>card.id===id)?.name??doc.value.search_runs.flatMap(run=>run.candidates??[]).find(candidate=>candidate.paper_id===id)?.metadata.title??id;
  const paperLink=(id:string,source?:SourceLocation)=><button type="button" disabled={!members.has(id)} onClick={()=>openPaper(id,source)}>{title(id)}{source?` · p${source.page}`:""}</button>;
  async function create(){
    if(!result)return;setBusy(true);setError("");
    try{const created=await api(`${root}/snapshot`,{expected_revision:doc.revision,arguments:{mode:"bootstrap",expected_version:result.current_version}});await reload();setSelected(created.version);}
    catch(error){setError(String(error));}finally{setBusy(false);}
  }
  return <section className="literature-snapshots" aria-label={t("领域快照")}>
    <header><div><h4>{t("领域快照")}</h4><small>{t("冻结来源与覆盖；科学结论保留核验状态。")}</small></div><button type="button" disabled={busy||!completed||!result} onClick={()=>void create()}>{busy?t("正在保存…"):t("保存记录快照")}</button></header>
    {!completed&&<p>{t("当前范围完成一次检索后，才可生成有来源的快照。")}</p>}
    {error&&<p role="alert">{error}</p>}
    {result?.items.length===0&&completed&&<p>{t("尚无快照。保存后可比较后续文献与证据的变化。")}</p>}
    {!!result?.items.length&&<nav aria-label={t("快照版本")}>{[...result.items].reverse().map(entry=><button key={entry.snapshot.version} type="button" aria-pressed={entry.snapshot.version===selected} onClick={()=>setSelected(entry.snapshot.version)}>v{entry.snapshot.version} · r{entry.snapshot.scope_revision}{entry.freshness.status==="stale"?` · ${t("待更新")}`:""}</button>)}</nav>}
    {snapshot&&item&&<article>
      <header><strong>v{snapshot.version} · {new Date(snapshot.cutoff_at).toLocaleString()}</strong><span className={item.freshness.status==="stale"?"is-stale":""}>{item.freshness.status==="current"?t("与当前记录一致"):t("来源或范围已变化")}</span></header>
      <small>{t("记录者")}: {item.recorded_by} · {item.mode==="bootstrap"?t("本地记录快照"):t("提交的综合版本")}</small>
      {!!item.freshness.reasons.length&&<details><summary>{t("需要更新的原因")}</summary><ul>{item.freshness.reasons.map((reason,index)=><li key={index}>{reason}</li>)}</ul></details>}
      {snapshot.narrative.map((block,index)=><p key={index}>{block.text}</p>)}
      <details><summary>{t("检索覆盖")} · {snapshot.coverage.length}</summary><ul>{snapshot.coverage.map(run=><li key={run.request_id}><strong>{run.query}</strong> · {t(coverageStates[run.state]??run.state)} · {run.candidate_count} {t("候选")}</li>)}</ul></details>
      {item.diff&&<details open><summary>{t("版本变化")} · v{item.diff.from_version} → v{item.diff.to_version}</summary><p>{t("文献新增")} {item.diff.added_paper_ids.length} · {t("移出")} {item.diff.removed_paper_ids.length} · {t("证据变化")} {item.diff.changed_evidence_ids.length} · {t("主张新增 / 移除 / 变化")} {item.diff.added_claim_ids.length}/{item.diff.removed_claim_ids.length}/{item.diff.changed_claim_ids.length}</p>{item.diff.scope_revision_changed&&<p>{t("研究范围已修订；不同版本的覆盖边界不同。")}</p>}</details>}
      <details><summary>{t("来源层级")} · {snapshot.sources.length}</summary><ul>{snapshot.sources.map(source=><li key={source.paper_id}>{paperLink(source.paper_id)}<small>{t(levels[source.basis])}{source.document_version_id?` · ${source.document_version_id.slice(0,12)}`:""}</small><p>{source.inclusion_rationale}</p></li>)}</ul></details>
      {!!snapshot.core_paper_ids.length&&<details><summary>{t("提交者选定的核心文献")}</summary>{snapshot.core_paper_ids.map(id=><div key={id}>{paperLink(id)}</div>)}</details>}
      {!!snapshot.claims.length&&<details><summary>{t("有来源的主张")} · {snapshot.claims.length}</summary>{snapshot.claims.map(claim=><section key={claim.id}><strong>{claim.text}</strong><small>{t(levels[claim.basis]??claim.basis)} · {claim.kind} · {claim.review_state==="reviewed"?t("来源已记录科学复核"):t("尚未完成科学复核")}</small>{claim.paper_ids.map(id=><div key={id}>{paperLink(id)}</div>)}{claim.limitations.map((limitation,index)=><p key={index}>{limitation}</p>)}</section>)}</details>}
      {!!snapshot.recommendations.length&&<details open><summary>{t("下一步阅读")} · {snapshot.recommendations.length}</summary>{snapshot.recommendations.map(recommendation=><section key={recommendation.id}>{paperLink(recommendation.paper_id,recommendation.anchor)}<small>{t(levels[recommendation.level])} · {t(reasons[recommendation.reason]??recommendation.reason)}</small><p>{recommendation.rationale}</p></section>)}</details>}
      <details open><summary>{t("覆盖边界与限制")}</summary><ul>{snapshot.limitations.map((limitation,index)=><li key={index}>{limitation}</li>)}</ul></details>
    </article>}
  </section>;
}
