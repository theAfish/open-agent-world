import {useEffect,useState} from "react";
import {t} from "@oaw/plugin-api";
import {readIntakeOptions,type IntakeOptions} from "../../../frontend/src/canvas/PdfImportChoice";
import {PaperPortal} from "./PaperPortal";
import type {CitationReference} from "./citationIndex";
import {useAutoResearch} from "../../../frontend/src/state/autoResearch";
import {defaultCitationRoad,type CitationRoad} from "./citationRoads";
import "./paperIntake.css";

export function CitationCollect({paperId,documentVersionId,reference}:{paperId:string;documentVersionId?:string;reference:CitationReference}) {
  const [options,setOptions]=useState<IntakeOptions>(),[scopeId,setScopeId]=useState(""),[targetId,setTargetId]=useState("");
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState(""),[open,setOpen]=useState("");
  const [roads,setRoads]=useState<CitationRoad[]>([]),[roadId,setRoadId]=useState(""),[roadsScope,setRoadsScope]=useState("");
  const activeScopeId=useAutoResearch(state=>state.scopeId),selectedFrontierId=useAutoResearch(state=>state.selectedFrontierId);
  useEffect(()=>{let active=true;if(!reference.doi)return;void readIntakeOptions(reference.doi).then(result=>{if(!active)return;setOptions(result);setTargetId(result.papers.length===1?result.papers[0].id:"");const scopes=result.scopes.filter(scope=>scope.configured);const activeScope=useAutoResearch.getState().scopeId;setScopeId(scopes.find(scope=>scope.id===activeScope)?.id??scopes.find(scope=>scope.paper_ids.includes(paperId))?.id??(scopes.length===1?scopes[0].id:""));}).catch(error=>{if(active)setError(String(error));});return()=>{active=false;};},[reference.doi,paperId]);
  useEffect(()=>{
    let active=true;setRoadsScope("");setRoadId("");setRoads([]);if(!scopeId)return;
    void fetch(`/api/literature/scopes/${encodeURIComponent(scopeId)}`).then(async response=>{if(!response.ok)throw new Error(await response.text());return response.json();}).then(current=>{
      if(!active)return;const choices:CitationRoad[]=current.value.exploration_roads?.length?current.value.exploration_roads:[{id:"trunk",title:t("初始检索与种子文献"),member_ids:[]}];
      setRoads(choices);setRoadId(defaultCitationRoad(choices,scopeId,paperId,activeScopeId,selectedFrontierId));setRoadsScope(scopeId);
    }).catch(error=>{if(active)setError(String(error));});return()=>{active=false;};
  },[scopeId,paperId,activeScopeId,selectedFrontierId]);
  if(!reference.doi)return <small>{t("此引用未识别到 DOI，可先通过文献链接核对题录。")}</small>;
  async function collect(){
    if(!scopeId||!roadId||roadsScope!==scopeId)return;setBusy(true);setError("");setMessage("");
    try {
      const snapshot=await fetch(`/api/literature/scopes/${encodeURIComponent(scopeId)}`);if(!snapshot.ok)throw new Error(await snapshot.text());const current=await snapshot.json();
      const response=await fetch(`/api/literature/scopes/${encodeURIComponent(scopeId)}/collect_reference`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:current.revision,arguments:{source_paper_id:paperId,source_document_version_id:documentVersionId,reference_page:reference.page,reference_text:reference.text,doi:reference.doi,target_paper_id:targetId||undefined,road_id:roadId}})});
      if(!response.ok)throw new Error(await response.text());const result=await response.json();setTargetId(result.paper_id);setMessage(t(result.citation?.navigation==="retained_existing_road"?"已连接引用，保留目标论文原有路径；题录与科学结论待核验。":"已收录到所选路径并连接引用；题录与科学结论待核验。"));
      window.dispatchEvent(new CustomEvent("oaw-research-updated",{detail:scopeId}));setOptions(await readIntakeOptions(reference.doi));
    }catch(error){setError(String(error));}finally{setBusy(false);}
  }
  const scopes=options?.scopes.filter(scope=>scope.configured)??[];
  const targetRoad=roads.find(road=>targetId&&road.member_ids.includes(`paper:${targetId}`));
  return <div className="library-citation-collect">
    {!!options?.papers.length&&<><small>{t("库中已有此 DOI")}</small>{options.papers.length>1&&<select aria-label={t("选择已有论文")} value={targetId} onChange={event=>setTargetId(event.target.value)}><option value="">{t("选择已有论文")}</option>{options.papers.map(paper=><option key={paper.id} value={paper.id}>{paper.title}</option>)}</select>}{targetId&&<button type="button" onClick={()=>setOpen(targetId)}>{t("打开库中 Paper")}</button>}</>}
    <label>{t("加入研究范围")}<select aria-label={t("加入研究范围")} value={scopeId} disabled={busy} onChange={event=>{setRoadsScope("");setRoadId("");setScopeId(event.target.value);}}><option value="">{t("选择研究范围")}</option>{scopes.map(scope=><option key={scope.id} value={scope.id}>{scope.title}</option>)}</select></label>
    {scopeId&&<label>{t("收藏到路径")}<select aria-label={t("收藏到路径")} value={roadId} disabled={busy||roadsScope!==scopeId} onChange={event=>setRoadId(event.target.value)}><option value="">{t("选择路径")}</option>{roads.map(road=><option key={road.id} value={road.id}>{road.title}</option>)}</select></label>}
    {targetRoad&&targetRoad.id!==roadId&&<small>{t("目标论文已有路径，将保留原位置并增加跨路径引用关联。")} {targetRoad.title}</small>}
    {options&&!scopes.length&&<small>{t("请先配置一个研究范围。")}</small>}
    {scopeId&&!scopes.find(scope=>scope.id===scopeId)?.paper_ids.includes(paperId)&&<small>{t("将同时纳入当前来源论文，保留引用出处。")}</small>}
    <button type="button" disabled={busy||!scopeId||!roadId||roadsScope!==scopeId||!documentVersionId||!!options&&options.papers.length>1&&!targetId} onClick={()=>void collect()}>{busy?t("正在保存…"):t("收录并连接引用")}</button>
    {message&&<small role="status">{message}</small>}{error&&<small role="alert">{error}</small>}
    {open&&<PaperPortal paperId={open} onClose={()=>setOpen("")}/>}
  </div>;
}
