import { t, useLocale } from "@oaw/plugin-api";
import { useState } from "react";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import type { ResearchScopeDoc } from "./index";
import type { SourceLocation } from "../../library/frontend/PaperPortal";
import { EvidenceClassification, evidenceKinds, evidenceRelations, type EvidenceKind, type EvidenceRelation } from "./EvidenceClassification";

async function api(path:string,body?:unknown) {const response=await fetch(`/api/${path}`,body===undefined?undefined:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail??result));return result;}

export function ScopeEvidence({scopeId,doc,reload,openPaper}:{scopeId:string;doc:ResearchScopeDoc;reload:()=>Promise<unknown>;openPaper:(id:string,source?:SourceLocation)=>void}) {
  useLocale();
  const cards=useWorldStore(state=>state.cards);
  const seeds=doc.value.revisions.at(-1)?.seed_paper_ids??[];
  const members=[...new Set([...doc.value.paper_ids,...seeds])];
  const [choosing,setChoosing]=useState(false),[paper,setPaper]=useState(members[0]??""),[page,setPage]=useState(1),[sources,setSources]=useState<any[]>([]),[selected,setSelected]=useState("");
  const [claim,setClaim]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [kind,setKind]=useState<EvidenceKind>("user_hypothesis"),[relation,setRelation]=useState<EvidenceRelation>("insufficient");
  const [editing,setEditing]=useState<any>(),[reviewing,setReviewing]=useState<any>(),[rationale,setRationale]=useState(""),[decision,setDecision]=useState<EvidenceRelation>("insufficient");
  async function changed(){await reload();window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));}
  async function loadSources(){setBusy(true);setError("");try{const result=await api(`literature/scopes/${scopeId}/paper`,{arguments:{paper_id:paper,view:"anchors",page}});setSources(result.sources);setSelected("");if(!result.sources.length)setError(t("本页没有可定位原文。"));}catch(error){setError(String(error));}finally{setBusy(false);}}
  async function addEvidence(event:React.FormEvent){event.preventDefault();const source=sources.find(item=>item.id===selected);if(!source)return;setBusy(true);setError("");try{const current=await api(`literature/scopes/${scopeId}`);await api(`literature/scopes/${scopeId}/record`,{expected_revision:current.revision,arguments:{kind:"evidence",value:{id:crypto.randomUUID(),claim,kind,relation,sources:[source],extracted_by:"desktop"}}});await changed();setClaim("");setChoosing(false);}catch(error){setError(String(error));}finally{setBusy(false);}}
  async function reviseEvidence(event:React.FormEvent){event.preventDefault();if(!editing)return;setBusy(true);setError("");try{
    const current=await api(`literature/scopes/${scopeId}`);
    const {id,revision,claim,kind,relation,conditions,sources}=editing;
    await api(`literature/scopes/${scopeId}/record`,{expected_revision:current.revision,arguments:{kind:"evidence",item_revision:revision,value:{id,claim,kind,relation,conditions,sources,extracted_by:"desktop"}}});
    await changed();setEditing(undefined);
  }catch(error){setError(String(error));}finally{setBusy(false);}}
  async function reviewEvidence(event:React.FormEvent){event.preventDefault();if(!reviewing)return;setBusy(true);setError("");try{
    const current=await api(`literature/scopes/${scopeId}`);
    await api(`literature/scopes/${scopeId}/review`,{expected_revision:current.revision,arguments:{evidence_id:reviewing.id,item_revision:reviewing.revision,decision,rationale:rationale.trim()}});
    await changed();setReviewing(undefined);setRationale("");
  }catch(error){setError(String(error));}finally{setBusy(false);}}
  return <section className="literature-evidence"><header><h4>{t("精读与方法")}</h4><button disabled={!members.length} onClick={()=>{setPaper(members[0]??"");setSources([]);setSelected("");setChoosing(value=>!value);}}>{t("从原文记录证据")}</button></header>
    {error&&<p role="alert">{error}</p>}
    {choosing&&<form className="literature-scope-form" onSubmit={addEvidence}>
      <div><select aria-label={t("选择论文")} disabled={busy} value={paper} onChange={event=>{setPaper(event.target.value);setSources([]);setSelected("");}}>{members.map(id=><option key={id} value={id}>{cards.find(card=>card.id===id)?.name??id}</option>)}</select><input type="number" aria-label={t("页码")} disabled={busy} min="1" value={page} onChange={event=>{setPage(Number(event.target.value));setSources([]);setSelected("");}}/><button type="button" disabled={busy} onClick={()=>void loadSources()}>{t("读取段落")}</button></div>
      {sources.length>0&&<><select required aria-label={t("原文段落")} value={selected} onChange={event=>setSelected(event.target.value)}><option value="">{t("选择原文段落")}</option>{sources.map((source,index)=><option key={source.id} value={source.id}>{index+1}. {source.quote.slice(0,110)}</option>)}</select><blockquote>{sources.find(item=>item.id===selected)?.quote}</blockquote><label>{t("主张或待核验问题")}<textarea required maxLength={20000} value={claim} onChange={event=>setClaim(event.target.value)}/></label><EvidenceClassification kind={kind} relation={relation} onKind={setKind} onRelation={setRelation} disabled={busy}/><small>{t("原文定位与科学核验分开记录；新增主张保持未核验。")}</small><button disabled={busy||!selected||!claim.trim()} type="submit">{t("保存有来源的记录")}</button></>}
    </form>}
    {doc.value.evidence.map(item=><article key={item.id}><strong>{item.claim}</strong><small>{t(evidenceKinds[item.kind as EvidenceKind]??item.kind)} · {t(evidenceRelations[item.relation as EvidenceRelation]??item.relation)} · {item.scientific_verification==="reviewed"?t("已记录复核意见"):t("待复核")} · r{item.revision}</small>{item.sources?.map((source:any)=><div key={source.id}><blockquote>{source.quote}</blockquote><button onClick={()=>openPaper(source.paper_id,source)}>{t("回到原文")} · p{source.page}</button><small>{source.document_version_id.slice(0,12)} · {doc.value.evidence_source_status?.[source.id]??source.status}</small></div>)}
      <footer><button disabled={busy} onClick={()=>{setReviewing(undefined);setEditing({...item});}}>{t("编辑主张与分类")}</button><button disabled={busy} onClick={()=>{setEditing(undefined);setReviewing(item);setRationale("");setDecision("insufficient");}}>{t("单独复核")}</button></footer>
      {editing?.id===item.id&&<form className="literature-scope-form" onSubmit={reviseEvidence}><label>{t("主张或待核验问题")}<textarea required maxLength={20000} value={editing.claim} onChange={event=>setEditing({...editing,claim:event.target.value})}/></label><EvidenceClassification kind={editing.kind} relation={editing.relation} onKind={kind=>setEditing({...editing,kind})} onRelation={relation=>setEditing({...editing,relation})} disabled={busy}/><small>{t("修改后生成新修订，先前复核意见不再适用。")}</small><div><button disabled={busy||!editing.claim.trim()}>{t("保存修订")}</button><button type="button" disabled={busy} onClick={()=>setEditing(undefined)}>{t("取消")}</button></div></form>}
      {reviewing?.id===item.id&&<form className="literature-scope-form" onSubmit={reviewEvidence}><p>{t("请核对上方主张和原文，单独记录你的判断。记录复核意见不等于证明主张正确。")}</p><label>{t("复核判断")}<select value={decision} disabled={busy} onChange={event=>setDecision(event.target.value as EvidenceRelation)}>{Object.entries(evidenceRelations).map(([key,label])=><option key={key} value={key}>{t(label)}</option>)}</select></label><label>{t("复核理由")}<textarea required maxLength={20000} disabled={busy} value={rationale} onChange={event=>setRationale(event.target.value)}/></label><small>{t("以当前桌面用户身份记录；意见绑定当前证据修订。")}</small><div><button disabled={busy||!rationale.trim()}>{t("提交复核意见")}</button><button type="button" disabled={busy} onClick={()=>setReviewing(undefined)}>{t("取消")}</button></div></form>}
      {item.scientific_reviews?.map((review:any)=><details key={review.id}><summary>{t("复核意见")} · {t(evidenceRelations[review.decision as EvidenceRelation])} · {review.reviewer}</summary><p>{review.rationale}</p><small>{review.reviewed_at}</small></details>)}
    </article>)}
    {doc.value.methods.map(item=><article key={item.id}><strong>{item.name}</strong><small>{item.status} · r{item.revision}</small><p>{item.purpose}</p>{item.missing?.length>0&&<p>{t("待补信息")}: {item.missing.join("; ")}</p>}
      <footer><a href={`/api/literature/scopes/${scopeId}/methods/${item.id}/export`}>{t("导出方法 Skill")}</a>{doc.value.knowledge_id&&<button onClick={async()=>{setBusy(true);setError("");try{const current=await api(`literature/scopes/${scopeId}`),knowledge=await api(`nodes/${doc.value.knowledge_id}/document`);await api(`literature/scopes/${scopeId}/assimilate_method`,{expected_revision:current.revision,arguments:{method_id:item.id,knowledge_revision:knowledge.revision}});setError(t("已按来源快照导入 KDG。"));}catch(error){setError(String(error));}finally{setBusy(false);}}} disabled={busy}>{t("加入 KDG")}</button>}</footer>
    </article>)}
  </section>;
}
