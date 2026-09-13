import { t, useLocale } from "../i18n";
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { Bot, Box, FileText, X } from "lucide-react";
import { memo, useEffect, useId, useRef, useMemo } from "react";
import type { CanvasNode } from "./types";
import { useWorldStore } from "../state/worldStore";
import { useNodeSurfaceStore, surfaceLevelForNode } from "../state/nodeSurfaces";
import { collectionCounts, collectionState, shadowPresentation, shadowPoints, SHADOW, useCollectionHover, useCollectionDrag, useCollectionRelease } from "../state/shadowCollection";
import { ContainerActions, AddSelectedMembers } from "./ContainerFrame";
import "./shadowCollection.css";
import { ShadowGasBoundary } from "../effects/ShadowGasBoundary";

function ShadowCollectionComponent({data,selected}:NodeProps<CanvasNode>) {
  useLocale();
  const card=data.card;
  const cards=useWorldStore(s=>s.cards),catalog=useWorldStore(s=>s.catalog);
  const surfaces=useNodeSurfaceStore(s=>s.surfaceLevels);
  const levels=useMemo(()=>new Map(cards.map(c=>[c.id,surfaceLevelForNode(c.id,surfaces)])),[cards,surfaces]);
  const members=useMemo(()=>cards.filter(c=>c.parent_id===card.id),[cards,card.id]);
  const state=collectionState(card),counts=collectionCounts(members,catalog);
  const releasing=useCollectionRelease(s=>Boolean(s.active[card.id]));
  useEffect(()=>{if(state!=="expanded")useCollectionRelease.getState().set(card.id,false);},[state,card.id]);
  useEffect(()=>()=>useCollectionRelease.getState().set(card.id,false),[card.id]);
  const positions=useCollectionDrag(s=>s.positions);
  const rect=useMemo(()=>shadowPresentation(card,cards,levels,positions,catalog),[card,cards,levels,positions,catalog]);
  const live=state==="expanded"&&members.some(member=>Boolean(positions[member.id]));
  const origin=data.shadowOrigin as {x:number;y:number}|undefined;
  const width=live?rect.width:Number(data.shadowWidth??rect.width),height=live?rect.height:Number(data.shadowHeight??rect.height);
  const points=useMemo(()=>live?shadowPoints(rect.width,rect.height,rect.rects):(data.shadowOutline as {x:number;y:number}[]??shadowPoints(width,height,rect.rects)),[live,rect,data.shadowOutline,width,height]);
  const path=useMemo(()=>points.map((p,i)=>`${i?"L":"M"}${p.x},${p.y}`).join(" ")+" Z",[points]);
  const update=useWorldStore(s=>s.updateCard);
  const syncing=useWorldStore(s=>s.positionCommitBusy||s.syncState==="syncing");
  const hover=useCollectionHover(s=>s.members[card.id]);
  const title=members.find(c=>c.id===hover)?.name??"";
  const lastCount=useRef(members.length);
  const pointer=useRef<{x:number;y:number}>();
  const internals=useUpdateNodeInternals();
  const id=useId().replaceAll(":","");
  useEffect(()=>()=>{useCollectionHover.getState().set(card.id);},[card.id]);
  useEffect(()=>{internals(card.id);},[width,height,card.id,internals]);
  useEffect(()=>{
    if(syncing)return;
    if(members.length>lastCount.current&&state==="minimal")void update(card.id,{config:{...card.config,display_state:"stacked"}});
    lastCount.current=members.length;
  },[members.length,state,card.id,card.config,update,syncing]);
  const advance=()=>void update(card.id,{config:{...card.config,display_state:state==="minimal"?"stacked":"expanded"}});
  return <section className={`shadow-collection container-frame ${selected?"is-selected":""} ${releasing?"is-releasing":""}`} data-card-id={card.id} data-card-type={card.type} data-state={state} aria-label={t("{v0} collection", { v0: String(card.name) })} style={{width,height,transform:live&&origin?`translate(${rect.x-origin.x}px,${rect.y-origin.y}px)`:undefined}}>
    <svg className="shadow-silhouette" width={width} height={height} style={{overflow:"visible"}} aria-hidden="true">
      <defs><filter id={`feather-${id}`} x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation={SHADOW.feather}/>
      </filter></defs>
      <path className="shadow-feather" d={path} filter={`url(#feather-${id})`} />
      <ShadowGasBoundary points={points} width={width} height={height} active={releasing} expanded={state==="expanded"}/>
      <path className="shadow-core container-drag-region" d={path}
        onPointerDown={e=>{pointer.current={x:e.clientX,y:e.clientY};}}
        onClick={e=>{if(pointer.current&&Math.hypot(e.clientX-pointer.current.x,e.clientY-pointer.current.y)<5&&state!=="expanded")advance();pointer.current=undefined;}} />
    </svg>
    <div className="shadow-counts container-drag-region" role={state==="minimal"?"button":undefined} tabIndex={state==="minimal"?0:undefined} aria-label={state==="minimal"?t("展开集合堆叠"):undefined}
      onPointerDown={e=>{pointer.current={x:e.clientX,y:e.clientY};}}
      onClick={e=>{if(state!=="expanded"&&pointer.current&&Math.hypot(e.clientX-pointer.current.x,e.clientY-pointer.current.y)<5)advance();}}
      onKeyDown={e=>{if((e.key==="Enter"||e.key===" ")&&state!=="expanded"){e.preventDefault();advance();}}}>
      <span title={t("PDF")}><FileText size={17}/>{counts.pdf}</span>
      {state!=="minimal"&&<span title={t("Other objects")}><Box size={17}/>{counts.object}</span>}
      <span title={t("Agents")}><Bot size={17}/>{counts.agent}</span>
    </div>
    {state!=="minimal"&&<button className="shadow-close nodrag nopan" aria-label={state==="expanded"?t("收起为堆叠"):t("收起为数字节点")} onClick={e=>{e.stopPropagation();void update(card.id,{config:{...card.config,display_state:state==="expanded"?"stacked":"minimal"}});}}><X size={19}/></button>}
    {state==="stacked"&&<div className={`shadow-title ${title?"is-visible":""}`}>{title}</div>}
    {state==="expanded"&&<div className="shadow-actions nodrag nopan"><AddSelectedMembers card={card}/><ContainerActions card={card} releaseMode={{active:releasing,toggle:()=>useCollectionRelease.getState().set(card.id,!releasing)}}/></div>}
    {([[Position.Top,"top",72],[Position.Right,"right",0],[Position.Bottom,"bottom",24],[Position.Left,"left",48]] as const).map(([position,side,index])=><Handle key={side} type="source" id={`boundary-${side}`} position={position} style={{left:points[index].x,top:points[index].y,right:"auto",bottom:"auto",transform:"translate(-50%,-50%)"}} className="shadow-port nodrag" data-connection-side={side} aria-label={t("Connect {v0} {v1}", { v0: String(card.name), v1: String(side) })}/>)}
  </section>;
}
// React Flow moves the wrapper; translation does not change the local silhouette.
export const ShadowCollectionNode=memo(ShadowCollectionComponent,(a,b)=>a.data===b.data&&a.selected===b.selected);
