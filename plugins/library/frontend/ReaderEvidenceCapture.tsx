import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { useAutoResearch } from "../../../frontend/src/state/autoResearch";
import { EvidenceClassification, type EvidenceKind, type EvidenceRelation } from "../../literature/frontend/EvidenceClassification";
import type { ResearchScopeDoc } from "../../literature/frontend/index";
import "./readerEvidence.css";

async function api(path:string,body?:unknown) {
  const response=await fetch(`/api/${path}`,body===undefined?undefined:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail??result));
  return result;
}
type Paragraph={id:string;quote:string;paper_id:string;page:number;document_version_id:string};

/** Browser selections are suggestions only; save the explicitly confirmed host paragraph. */
export function ReaderEvidenceCapture({paperId,documentVersionId,page,text,comment="",onOpenChange}:{paperId:string;documentVersionId?:string;page:number;text:string;comment?:string;onOpenChange?:(open:boolean)=>void}) {
  useLocale();
  const cards=useWorldStore(state=>state.cards);
  const [scopes,setScopes]=useState(()=>cards.filter(card=>card.type==="literature.scope").map(card=>({id:card.id,title:card.name})));
  const activeScope=useAutoResearch(state=>state.scopeId);
  const [open,setOpen]=useState(false),[scopeId,setScopeId]=useState(activeScope??scopes[0]?.id??"");
  const [scope,setScope]=useState<ResearchScopeDoc>(),[sources,setSources]=useState<Paragraph[]>([]),[selected,setSelected]=useState("");
  const [confirmed,setConfirmed]=useState(false),[claim,setClaim]=useState(comment||text);
  const [kind,setKind]=useState<EvidenceKind>(comment?"user_hypothesis":"author_statement"),[relation,setRelation]=useState<EvidenceRelation>("insufficient");
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[saved,setSaved]=useState(false),[match,setMatch]=useState("");
  const generation=useRef(0);
  const openedOnce=useRef(false);
  const [refresh,setRefresh]=useState(0);
  const isMember=!!scope&&[...scope.value.paper_ids,...(scope.value.revisions.at(-1)?.seed_paper_ids??[])].includes(paperId);
  useEffect(()=>{
    if(!open)return;
    let live=true;
    void api("literature/papers/intake-options").then(result=>{
      if(!live)return;
      const choices=result.scopes.filter((item:{configured:boolean})=>item.configured);
      setScopes(choices);setScopeId(current=>choices.some((item:{id:string})=>item.id===current)?current:choices.find((item:{paper_ids:string[]})=>item.paper_ids.includes(paperId))?.id??choices[0]?.id??"");
    }).catch(reason=>{if(live)setError(String(reason));});
    return()=>{live=false;};
  },[open,paperId]);
  useEffect(()=>{
    if(!open||!scopeId)return;
    const epoch=++generation.current;
    setScope(undefined);setSources([]);setSelected("");setConfirmed(false);setError("");setSaved(false);setBusy(true);
    void api(`literature/scopes/${encodeURIComponent(scopeId)}`).then(async current=>{
      if(epoch!==generation.current)return;
      setScope(current);
      if(![...current.value.paper_ids,...(current.value.revisions.at(-1)?.seed_paper_ids??[])].includes(paperId))return;
      const result=await api(`literature/scopes/${encodeURIComponent(scopeId)}/paper`,{arguments:{paper_id:paperId,view:"relocate",page,selected_text:text,document_version_id:documentVersionId}});
      if(epoch!==generation.current)return;
      setSources(result.sources);setSelected(result.matching_source_ids.length===1?result.matching_source_ids[0]:"");setMatch(result.match_status);
      if(!result.sources.length)setError(t("本页没有可定位原文。"));
    }).catch(reason=>{if(epoch===generation.current)setError(String(reason));}).finally(()=>{if(epoch===generation.current)setBusy(false);});
    return()=>{generation.current++;};
  },[open,scopeId,paperId,documentVersionId,page,text,refresh]);
  async function linkPaper(){
    if(!scope||busy)return;
    setBusy(true);setError("");
    try{
      const current=await api(`literature/scopes/${encodeURIComponent(scopeId)}`);
      await api(`literature/scopes/${encodeURIComponent(scopeId)}/link_paper`,{expected_revision:current.revision,arguments:{paper_id:paperId}});
      window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));setRefresh(value=>value+1);
    }catch(reason){setError(String(reason));}finally{setBusy(false);}
  }
  async function saveEvidence(){
    const source=sources.find(item=>item.id===selected);
    if(!confirmed||!source||!claim.trim()||busy)return;
    setBusy(true);setError("");
    try {
      const current=await api(`literature/scopes/${encodeURIComponent(scopeId)}`);
      await api(`literature/scopes/${encodeURIComponent(scopeId)}/record`,{expected_revision:current.revision,arguments:{kind:"evidence",value:{id:crypto.randomUUID(),claim:claim.trim(),kind,relation,sources:[source],extracted_by:"desktop"}}});
      setSaved(true);window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));
    }catch(reason){setError(String(reason));}finally{setBusy(false);}
  }
  function toggle(){const next=!open;if(next&&!openedOnce.current){setClaim(comment||text);setKind(comment?"user_hypothesis":"author_statement");openedOnce.current=true;}setOpen(next);onOpenChange?.(next);}
  return <section className="library-reader-evidence" style={open?{width:"100%",flexBasis:"100%"}:undefined}>
    <button type="button" disabled={!text.trim()||!documentVersionId} aria-expanded={open} onClick={toggle}>{open?t("收起证据记录"):t("记录研究证据")}</button>
    {open&&<div>
      {!scopes.length?<p>{t("请先创建研究范围，再记录证据。")}</p>:<>
        <label>{t("研究范围")}<select disabled={busy||saved} value={scopeId} onChange={event=>setScopeId(event.target.value)}><option value="">{t("选择研究范围")}</option>{scopes.map(card=><option key={card.id} value={card.id}>{card.title}</option>)}</select></label>
        {scope&&!isMember&&<><p>{t("这篇论文尚未纳入所选范围。")}</p><button type="button" disabled={busy} onClick={()=>void linkPaper()}>{t("纳入此范围并定位原文")}</button></>}
        {sources.length>0&&!saved&&<>
          <label>{t("核对原文段落")}<select disabled={busy} value={selected} onChange={event=>{setSelected(event.target.value);setConfirmed(false);}}><option value="">{t("选择原文段落")}</option>{sources.map((source,index)=><option key={source.id} value={source.id}>{index+1}. {source.quote.slice(0,100)}</option>)}</select></label>
          {match!=="unique_text_match"&&<small>{t("选段未能唯一对应一个段落，请人工选择并核对。")}</small>}
          <blockquote>{sources.find(source=>source.id===selected)?.quote}</blockquote>
          <label className="library-evidence-confirm"><input type="checkbox" disabled={!selected||busy} checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>{t("已核对：此原文段落对应我要记录的来源")}</label>
          <label>{t("主张或待核验问题")}<textarea maxLength={20000} disabled={busy} value={claim} onChange={event=>setClaim(event.target.value)}/></label>
          <EvidenceClassification kind={kind} relation={relation} onKind={setKind} onRelation={setRelation} disabled={busy}/>
          <small>{t("原文定位与科学复核分开；此记录保存后仍待复核。")}</small>
          <button type="button" disabled={busy||!confirmed||!claim.trim()} onClick={()=>void saveEvidence()}>{busy?t("保存中…"):t("保存研究证据")}</button>
        </>}
      </>}
      {busy&&<small role="status">{t("正在处理…")}</small>}
      {saved&&<p role="status">{t("已保存研究证据，等待单独复核。")}</p>}
      {error&&<p role="alert">{error}</p>}
    </div>}
  </section>;
}
