import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useId, useRef, useState } from "react";
import type { Annotation } from "./PdfReading";
import { StudyCardContent } from "./StudyCardContent";
import "./studyRelations.css";

export type StudyRelationshipType = "supports"|"contradicts"|"depends_on"|"derived_from";
export type StudyRelationship = {id:string;source:string;target:string;type:StudyRelationshipType};
const RELATION_LABELS:Record<StudyRelationshipType,string> = {supports:"支持",contradicts:"反驳",depends_on:"依赖于",derived_from:"源自"};
const RELATION_TYPES=Object.keys(RELATION_LABELS) as StudyRelationshipType[];
type CardBox={x:number;y:number;width:number;height:number};

/** Direction is explicit: source supports/contradicts/depends on/is derived from target. */
export function studyRelationshipError(candidate:StudyRelationship, relationships:readonly StudyRelationship[], annotationIds:ReadonlySet<string>):string|null {
  if(!annotationIds.has(candidate.source)||!annotationIds.has(candidate.target))return "请选择当前画布上的两个摘录";
  if(candidate.source===candidate.target)return "不能将摘录连接到自身";
  if(!RELATION_TYPES.includes(candidate.type))return "请选择关系类型";
  if(relationships.some(item=>item.id!==candidate.id&&item.source===candidate.source&&item.target===candidate.target&&item.type===candidate.type))return "这条关系已经存在";
  return null;
}

/** Keep relation geometry separate from the user's saved annotation positions. */
export function studyRelationshipPath(source:CardBox,target:CardBox):{path:string;x:number;y:number} {
  const horizontal=target.x>=source.x+source.width+24||source.x>=target.x+target.width+24;
  if(horizontal){
    const right=target.x>source.x,start={x:source.x+(right?source.width:0),y:source.y+source.height/2};
    const end={x:target.x+(right?0:target.width),y:target.y+target.height/2};
    const distance=Math.max(36,Math.abs(end.x-start.x)*.45),sign=right?1:-1;
    return {path:`M ${start.x} ${start.y} C ${start.x+sign*distance} ${start.y}, ${end.x-sign*distance} ${end.y}, ${end.x} ${end.y}`,
      x:(start.x+end.x)/2,y:(start.y+end.y)/2};
  }
  const down=target.y>=source.y,start={x:source.x+source.width/2,y:source.y+(down?source.height:0)};
  const end={x:target.x+target.width/2,y:target.y+(down?0:target.height)},distance=Math.max(36,Math.abs(end.y-start.y)*.45),sign=down?1:-1;
  return {path:`M ${start.x} ${start.y} C ${start.x} ${start.y+sign*distance}, ${end.x} ${end.y-sign*distance}, ${end.x} ${end.y}`,
    x:(start.x+end.x)/2,y:(start.y+end.y)/2};
}

type StudyCanvasProps={items:Annotation[];move:(a:Annotation)=>Promise<void>;open:(a:Annotation)=>void;locate:(page:number)=>void;title:string;linked:boolean;layout:(positions:Record<string,{x:number;y:number}>)=>Promise<void>;name:string;rename:(name:string)=>Promise<void>;
  relationships?:StudyRelationship[];saveRelationships?:(relationships:StudyRelationship[])=>Promise<void>;locateSource?:(annotation:Annotation)=>void};

export function StudyCanvas({items,move,open,locate,title,linked,layout,name,rename,relationships=[],saveRelationships,locateSource}:StudyCanvasProps) {
  useLocale();
  const [nameDraft,setNameDraft]=useState(name);
  const [renaming,setRenaming]=useState(false);
  useEffect(()=>setNameDraft(name),[name]);
  async function saveName(){const next=nameDraft.trim()||t("学习画布");setNameDraft(next);if(next===name||renaming)return;setRenaming(true);try{await rename(next);}catch(error){setError(String(error));}finally{setRenaming(false);}}
  const [view,setView]=useState({x:40,y:40,k:1});
  const [draft,setDraft]=useState<{id:string;x:number;y:number}>();
  const drag=useRef<{id?:string;x:number;y:number;ox:number;oy:number;sx:number;sy:number;k:number}>();
  const [pending,setPending]=useState<Record<string,{x:number;y:number}>>({});
  const frame=useRef(0);
  const motion=useRef<{x:number;y:number}>();
  useEffect(()=>()=>cancelAnimationFrame(frame.current),[]);
  function cancelMotion(){cancelAnimationFrame(frame.current);frame.current=0;motion.current=undefined;}
  function dragPoint(clientX:number,clientY:number){const d=drag.current!;return {x:d.ox+(clientX-d.x)/d.sx/d.k,y:d.oy+(clientY-d.y)/d.sy/d.k};}
  const host=useRef<HTMLDivElement>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [relationsOpen,setRelationsOpen]=useState(false);
  const [relationBusy,setRelationBusy]=useState(false);
  const [relationError,setRelationError]=useState("");
  const [relationSource,setRelationSource]=useState("");
  const [relationTarget,setRelationTarget]=useState("");
  const [relationType,setRelationType]=useState<StudyRelationshipType>("supports");
  const [editingRelation,setEditingRelation]=useState<string|null>(null);
  const [cardSizes,setCardSizes]=useState<Record<string,{width:number;height:number}>>({});
  const [canvasSize,setCanvasSize]=useState({width:344,height:552});
  const relationToggle=useRef<HTMLButtonElement>(null);
  const relationPanel=useRef<HTMLDivElement>(null);
  const markerId="study-relation-"+useId().replace(/:/g,"");
  const itemIds=items.map(item=>item.id).join("\0");
  useEffect(()=>{
    setRelationSource(current=>items.some(item=>item.id===current)?current:items[0]?.id??"");
    setRelationTarget(current=>items.some(item=>item.id===current)?current:items[1]?.id??"");
  },[itemIds]);
  useEffect(()=>{
    if(relationsOpen)relationPanel.current?.querySelector<HTMLElement>("select, button")?.focus();
  },[relationsOpen]);
  useEffect(()=>{
    const cards=Array.from(host.current?.querySelectorAll<HTMLElement>("[data-study-id]")??[]);
    function measure(){
      const sizes=Object.fromEntries(cards.map(card=>[card.dataset.studyId!,{width:card.offsetWidth||240,height:card.offsetHeight||100}]));
      setCardSizes(current=>JSON.stringify(current)===JSON.stringify(sizes)?current:sizes);
      const width=host.current?.clientWidth??344,height=host.current?.clientHeight??552;
      setCanvasSize(current=>current.width===width&&current.height===height?current:{width,height});
    }
    measure();
    if(typeof ResizeObserver==="undefined")return;
    const observer=new ResizeObserver(measure);cards.forEach(card=>observer.observe(card));if(host.current)observer.observe(host.current);return ()=>observer.disconnect();
  },[itemIds]);
  const excerpt=(id:string)=>{const item=items.find(item=>item.id===id);return item?`${t("Page {page}",{page:item.page})} · ${(item.title||item.text||item.comment||t("图片摘录")).replace(/\s+/g," ").slice(0,65)}`:t("摘录不在当前画布");};
  function closeRelations(){setRelationsOpen(false);relationToggle.current?.focus();}
  function selectRelation(relation:StudyRelationship){setEditingRelation(relation.id);setRelationSource(relation.source);setRelationTarget(relation.target);setRelationType(relation.type);setRelationError("");setRelationsOpen(true);}
  async function persistRelations(next:StudyRelationship[]){
    if(!saveRelationships||relationBusy)return false;
    setRelationBusy(true);setRelationError("");
    try{await saveRelationships(next);return true;}catch(cause){setRelationError(String(cause));return false;}finally{setRelationBusy(false);}
  }
  async function saveRelation(){
    if(relationBusy||!saveRelationships)return;
    const relation:StudyRelationship={id:editingRelation??crypto.randomUUID(),source:relationSource,target:relationTarget,type:relationType};
    if(editingRelation&&!relationships.some(item=>item.id===editingRelation)){setRelationError(t("关系已更改，请重新选择"));return;}
    const problem=studyRelationshipError(relation,relationships,new Set(items.map(item=>item.id)));
    if(problem){setRelationError(t(problem));return;}
    const next=editingRelation?relationships.map(item=>item.id===editingRelation?relation:item):[...relationships,relation];
    if(await persistRelations(next))setEditingRelation(null);
  }
  async function deleteRelation(id:string){if(await persistRelations(relationships.filter(item=>item.id!==id))&&editingRelation===id)setEditingRelation(null);}
  const point=(a:Annotation)=>draft?.id===a.id?draft:(pending[a.id]??a.position??{x:0,y:0});
  const relationPaths=relationships.flatMap(relation=>{
    const source=items.find(item=>item.id===relation.source),target=items.find(item=>item.id===relation.target);
    if(!source||!target||source.id===target.id||!RELATION_TYPES.includes(relation.type))return [];
    const box=(item:Annotation)=>({...point(item),...(cardSizes[item.id]??{width:240,height:item.collapsed?100:240})});
    return [{relation,...studyRelationshipPath(box(source),box(target))}];
  });
  const pages=[...new Set(items.map(a=>a.page))].sort((a,b)=>a-b);
  const branches=pages.map(page=>{const group=items.filter(a=>a.page===page);return {page,x:Math.min(...group.map(a=>point(a).x))-220,y:group.reduce((sum,a)=>sum+point(a).y+100,0)/group.length};});
  const root={x:Math.min(0,...branches.map(b=>b.x))-260,y:branches.reduce((sum,b)=>sum+b.y,0)/Math.max(1,branches.length)};
  async function arrange(){
    setBusy(true);setError("");
    try{
      const positions:Record<string,{x:number;y:number}>={};let y=0;
      for(const page of pages){
        for(const a of items.filter(a=>a.page===page).sort((a,b)=>(a.rects[0]?.[1]??1)-(b.rects[0]?.[1]??1)||a.id.localeCompare(b.id))){
          const el=Array.from(host.current?.querySelectorAll<HTMLElement>("[data-study-id]")??[]).find(el=>el.dataset.studyId===a.id);
          positions[a.id]={x:480,y};y+=(el?.offsetHeight??360)+44;
        }
        y+=60;
      }
      await layout(positions);
      const width=host.current?.clientWidth??800,height=host.current?.clientHeight??600;
      const k=Math.max(.05,Math.min(1,(width-80)/760,(height-100)/Math.max(1,y)));
      setView({x:40,y:80,k});
    }catch(error){setError(String(error));}finally{setBusy(false);}
  }
  return <div ref={host} className="library-study" onWheel={e=>{
    e.stopPropagation();if(drag.current)return;const box=e.currentTarget.getBoundingClientRect();const x=(e.clientX-box.left)/(box.width/e.currentTarget.offsetWidth),y=(e.clientY-box.top)/(box.height/e.currentTarget.offsetHeight);
    setView(v=>{const k=Math.max(.05,Math.min(3,v.k*Math.exp(-e.deltaY*.001)));return {k,x:x-(x-v.x)*k/v.k,y:y-(y-v.y)*k/v.k};});
  }} onPointerDown={e=>{
    if(busy||e.button!==0||(e.target as HTMLElement).closest("button, input, textarea, select, [data-study-ui]"))return;
    e.stopPropagation();e.currentTarget.setPointerCapture(e.pointerId);
    const id=(e.target as HTMLElement).closest<HTMLElement>("[data-study-id]")?.dataset.studyId;
    const a=items.find(a=>a.id===id),p=a?point(a):view,box=e.currentTarget.getBoundingClientRect();drag.current={id,x:e.clientX,y:e.clientY,ox:p.x,oy:p.y,sx:box.width/e.currentTarget.offsetWidth,sy:box.height/e.currentTarget.offsetHeight,k:id?view.k:1};
  }} onPointerMove={e=>{if(!drag.current)return;motion.current={x:e.clientX,y:e.clientY};if(frame.current)return;frame.current=requestAnimationFrame(()=>{frame.current=0;const d=drag.current,m=motion.current;if(!d||!m)return;const p=dragPoint(m.x,m.y);if(d.id)setDraft({id:d.id,...p});else setView(v=>({...v,...p}));});}}
  onPointerUp={e=>{const d=drag.current;if(!d)return;cancelMotion();const position=dragPoint(e.clientX,e.clientY);if(d.id){const a=items.find(a=>a.id===d.id);if(a){const id=a.id;setPending(current=>({...current,[id]:position}));void move({...a,position}).catch(error=>setError(String(error))).finally(()=>setPending(current=>{if(current[id]!==position)return current;const next={...current};delete next[id];return next;}));}}else setView(v=>({...v,...position}));drag.current=undefined;setDraft(undefined);}}
  onPointerCancel={()=>{cancelMotion();drag.current=undefined;setDraft(undefined);}}>
    <div className="library-study-controls">
      <input className="library-study-name" aria-label={t("学习画布名称")} title={t("点击改名，Enter 保存")} maxLength={100} value={nameDraft} disabled={renaming} onChange={e=>setNameDraft(e.target.value)} onBlur={()=>void saveName()} onKeyDown={e=>{if(e.key==="Enter"&&!e.nativeEvent.isComposing)e.currentTarget.blur();}}/>
      <button aria-label={t("自动排版")} title={busy?t("排版中…"):t("自动排版")} disabled={busy||!items.length} onClick={()=>void arrange()}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="2" y="9" width="6" height="6" rx="1"/><rect x="16" y="3" width="6" height="6" rx="1"/><rect x="16" y="15" width="6" height="6" rx="1"/><path d="M8 12h4M12 6v12M12 6h4M12 18h4"/></svg></button>
      <button aria-label={t("复位")} title={t("复位")} onClick={()=>setView({x:40,y:40,k:1})}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/></svg></button>
      {(saveRelationships||relationships.length>0)&&<button ref={relationToggle} className="library-study-relations-toggle" aria-label={t("摘录关系设置")} title={t("摘录关系设置")} aria-expanded={relationsOpen} onClick={()=>setRelationsOpen(value=>!value)}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="2" y="8" width="6" height="8" rx="2"/><rect x="16" y="8" width="6" height="8" rx="2"/><path d="M8 12h8m-3-3 3 3-3 3"/></svg>{relationships.length>0&&<span>{relationships.length}</span>}</button>}
      {error&&<span role="alert">{error}</span>}
      {relationsOpen&&<div ref={relationPanel} className="library-study-relation-panel" style={{maxWidth:Math.max(0,canvasSize.width-20),maxHeight:Math.max(80,Math.min(480,canvasSize.height-72))}} data-study-ui role="dialog" aria-label={t("摘录关系")} onWheel={event=>event.stopPropagation()} onKeyDown={event=>{if(event.key==="Escape"){event.preventDefault();event.stopPropagation();closeRelations();}}}>
        <header><strong>{t("摘录关系")}</strong><button aria-label={t("关闭关系设置")} onClick={closeRelations}>×</button></header>
        <p>{t("由你标记摘录之间的关系。箭头从来源指向目标。")}</p>
        {saveRelationships&&<form onSubmit={event=>{event.preventDefault();void saveRelation();}}>
          <label>{t("来源摘录")}<select aria-label={t("来源摘录")} value={relationSource} disabled={relationBusy||items.length<2} onChange={event=>setRelationSource(event.target.value)}>{!items.length&&<option value="">{t("暂无摘录")}</option>}{items.map(item=><option key={item.id} value={item.id}>{excerpt(item.id)}</option>)}</select></label>
          <label>{t("关系类型")}<select aria-label={t("关系类型")} value={relationType} disabled={relationBusy} onChange={event=>setRelationType(event.target.value as StudyRelationshipType)}>{RELATION_TYPES.map(type=><option key={type} value={type}>{t(RELATION_LABELS[type])}</option>)}</select></label>
          <label>{t("目标摘录")}<select aria-label={t("目标摘录")} value={relationTarget} disabled={relationBusy||items.length<2} onChange={event=>setRelationTarget(event.target.value)}>{!items.length&&<option value="">{t("暂无摘录")}</option>}{items.map(item=><option key={item.id} value={item.id} disabled={item.id===relationSource}>{excerpt(item.id)}</option>)}</select></label>
          <div className="library-study-relation-actions"><button type="submit" disabled={relationBusy||items.length<2||relationSource===relationTarget}>{relationBusy?t("保存中…"):editingRelation?t("保存关系"):t("添加关系")}</button>{editingRelation&&<button type="button" disabled={relationBusy} onClick={()=>{setEditingRelation(null);setRelationError("");}}>{t("新建关系")}</button>}</div>
          {items.length<2&&<small>{t("先添加至少两个学习摘录。")}</small>}
        </form>}
        {relationError&&<p role="alert" className="library-study-relation-error">{relationError}</p>}
        {relationships.length>0?<ul>{relationships.map(relation=><li key={relation.id} data-relation-type={relation.type}>
          <button className="library-study-relation-entry" aria-label={`${excerpt(relation.source)} ${t(RELATION_LABELS[relation.type])} ${excerpt(relation.target)}`} onClick={()=>selectRelation(relation)} disabled={relationBusy}><span>{excerpt(relation.source)}</span><b>{t(RELATION_LABELS[relation.type])} →</b><span>{excerpt(relation.target)}</span></button>
          {saveRelationships&&<button className="library-study-relation-delete" aria-label={t("删除关系")} title={t("删除关系")} disabled={relationBusy} onClick={()=>void deleteRelation(relation.id)}>×</button>}
        </li>)}</ul>:<small className="library-study-relation-empty">{t("暂无关系")}</small>}
      </div>}
    </div>
    <div style={{transform:`translate(${view.x}px,${view.y}px) scale(${view.k})`,transformOrigin:"0 0"}}>
      {relationPaths.length>0&&<svg className="library-study-semantic-edges" aria-hidden="true"><defs>{RELATION_TYPES.map(type=><marker key={type} id={`${markerId}-${type}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 1 1 L 7 4 L 1 7 Z" className="library-study-relation-arrow" data-relation-type={type}/></marker>)}</defs>{relationPaths.map(({relation,path})=><path key={relation.id} d={path} data-relation-id={relation.id} data-relation-type={relation.type} markerEnd={`url(#${markerId}-${relation.type})`}/>)}</svg>}
      {linked&&items.length>0&&<>
        <svg className="library-study-edges">{branches.map(b=><g key={b.page}>
          <path d={`M ${root.x+180} ${root.y} C ${b.x-40} ${root.y}, ${b.x-40} ${b.y}, ${b.x} ${b.y}`}/>
          {items.filter(a=>a.page===b.page).map(a=>{const p=point(a);return <path key={a.id} d={`M ${b.x+140} ${b.y} C ${p.x-40} ${b.y}, ${p.x-40} ${p.y+60}, ${p.x} ${p.y+60}`}/>;})}
        </g>)}</svg>
        <div className="library-study-root" style={{left:root.x,top:root.y-24}} title={title}>{title}</div>
        {branches.map(b=><button className="library-study-branch" key={b.page} style={{left:b.x,top:b.y-20}} onClick={()=>locate(b.page)}>{t("Page {page}", { page: b.page })}</button>)}
      </>}
      {items.map(a=><article className="library-study-card" data-study-id={a.id} key={a.id} style={{left:0,top:0,transform:`translate3d(${point(a).x}px,${point(a).y}px,0)`,borderTopColor:a.color??"#f4d144"}}>
        <StudyCardContent item={a} save={move}/>
        <button onClick={()=>open(a)}>{t("编辑")}</button><button onClick={()=>locateSource?locateSource(a):locate(a.page)}>{t("原文 ·")} {a.page}</button>
      </article>)}
      {relationPaths.map(({relation,x,y})=><button key={relation.id} className="library-study-relation-label" data-relation-type={relation.type} style={{left:x,top:y}} aria-label={`${excerpt(relation.source)} ${t(RELATION_LABELS[relation.type])} ${excerpt(relation.target)}`} title={`${excerpt(relation.source)} → ${excerpt(relation.target)}`} onClick={()=>selectRelation(relation)}>{t(RELATION_LABELS[relation.type])} <span aria-hidden="true">→</span></button>)}
    </div>
  </div>;
}
