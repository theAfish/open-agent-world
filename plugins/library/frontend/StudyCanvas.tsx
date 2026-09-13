import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useRef, useState } from "react";
import type { Annotation } from "./PdfReading";
import { StudyCardContent } from "./StudyCardContent";

export function StudyCanvas({items,move,open,locate,title,linked,layout,name,rename}:{items:Annotation[];move:(a:Annotation)=>Promise<void>;open:(a:Annotation)=>void;locate:(page:number)=>void;title:string;linked:boolean;layout:(positions:Record<string,{x:number;y:number}>)=>Promise<void>;name:string;rename:(name:string)=>Promise<void>}) {
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
  const point=(a:Annotation)=>draft?.id===a.id?draft:(pending[a.id]??a.position??{x:0,y:0});
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
    if(busy||e.button!==0||(e.target as HTMLElement).closest("button, input, textarea, select"))return;
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
      {error&&<span role="alert">{error}</span>}
    </div>
    <div style={{transform:`translate(${view.x}px,${view.y}px) scale(${view.k})`,transformOrigin:"0 0"}}>
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
        <button onClick={()=>open(a)}>{t("编辑")}</button><button onClick={()=>locate(a.page)}>{t("原文 ·")} {a.page}</button>
      </article>)}
    </div>
  </div>;
}
