import { useEffect, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from "pdfjs-dist";
import { PdfPageSurface } from "./PdfPageSurface";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "pdfjs-dist/web/pdf_viewer.css";
import { useLibrarySettings } from "../../../frontend/src/state/librarySettings";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { availableModels } from "../../../frontend/src/state/modelConnections";
import { StudyCanvas } from "./StudyCanvas";

GlobalWorkerOptions.workerSrc = workerUrl;
export type Annotation = {id:string;page:number;text:string;comment:string;translation:string;rects:number[][];title?:string;color?:string;title_color?:string;collapsed?:boolean;image?:string;learning?:boolean;position?:{x:number;y:number}};
export type ReadingValue = {pdf:string;page:number;pages:number;annotations?:Annotation[];study_layout?:boolean;study_title?:string;filename?:string};
type Selection = {page:number;text:string;rects:number[][];anchor:number[];annotation?:Annotation};
async function request(path:string, init?:RequestInit) {
  const response=await fetch(`/api/${path}`,init);
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"请求失败");
  return data;
}

export function PdfReading({value,save,fullscreen=false,settingsOpen,setSettingsOpen}:{value:ReadingValue;save:(args:Record<string,unknown>)=>Promise<void>;fullscreen?:boolean;settingsOpen:boolean;setSettingsOpen:(open:boolean)=>void}) {
  const [pdf,setPdf]=useState<PDFDocumentProxy>();
  const [page,setPage]=useState(value.page);
  const [continuous,setContinuous]=useState(false);
  const [scale,setScale]=useState(1);
  const [autoFit,setAutoFit]=useState(true);
  useEffect(()=>setAutoFit(true),[fullscreen]);
  const progress=useRef({page:value.page,saved:value.page,save});
  progress.current={page,saved:value.page,save};
  useEffect(()=>()=>{const p=progress.current;if(p.page!==p.saved)void p.save({page:p.page}).catch(()=>{});},[]);
  const [selection,setSelection]=useState<Selection>();
  const [comment,setComment]=useState("");
  const [translation,setTranslation]=useState("");
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const [saving,setSaving]=useState(false);
  const reading=useRef<HTMLDivElement>(null);
  const popup=useRef<HTMLDivElement>(null);
  const scroll=useRef<HTMLDivElement>(null);
  const [popupPosition,setPopupPosition]=useState<{left:number;top:number;visible:boolean}>();
  const selectionEpoch=useRef(0);
  function dismissSelection(){selectionEpoch.current++;setSelection(undefined);setPopupPosition(undefined);}
  const {provider,model:selectedModel,target}=useLibrarySettings();
  const catalog=useWorldStore(state=>state.modelCatalog);
  const legacyModels=useWorldStore(state=>state.modelSettings.models);
  const models=catalog.revision>0?availableModels({...catalog,connections:catalog.connections.filter(c=>c.adapter==="openai"||c.adapter==="legacy")}):legacyModels.map(value=>({value,label:value}));
  const model=models.some(item=>item.value===selectedModel)?selectedModel:(models[0]?.value||"");
  const [sidebarOpen,setSidebarOpen]=useState(false);
  const [annotating,setAnnotating]=useState(false);
  const [tool,setTool]=useState<"text"|"crop">("text");
  const [studyOpen,setStudyOpen]=useState(false);
  const [editing,setEditing]=useState<Annotation>();
  const [crop,setCrop]=useState<number[]>();
  const cropStart=useRef<number[]>();
  const imageInput=useRef<HTMLInputElement>(null);
  const deleting=useRef(false);
  useEffect(()=>{
    const onDelete=(event:KeyboardEvent)=>{
      if(event.key!=="Backspace"&&event.key!=="Delete")return;
      if(!reading.current?.closest("dialog")?.matches(":modal"))return;
      const target=event.target instanceof HTMLElement?event.target:null;
      // Leave native text editing intact; the outer canvas also ignores this dialog.
      if(target?.closest("input, textarea, select, [contenteditable='true']"))return;
      event.preventDefault();event.stopImmediatePropagation();
      const annotation=selection?.annotation;
      if(!annotation||editing||studyOpen||event.repeat||deleting.current||saving)return;
      deleting.current=true;
      void save({delete_annotation:annotation.id}).then(()=>{
        dismissSelection();window.getSelection()?.removeAllRanges();
      }).catch(error=>setError(String(error))).finally(()=>{deleting.current=false;});
    };
    window.addEventListener("keydown",onDelete,true);
    return()=>window.removeEventListener("keydown",onDelete,true);
  },[selection,editing,studyOpen,saving,save]);
  useEffect(()=>{
    if(!editing)return;
    const escape=(event:KeyboardEvent)=>{if(event.key==="Escape"){event.preventDefault();event.stopImmediatePropagation();setEditing(undefined);}};
    window.addEventListener("keydown",escape,true);return()=>window.removeEventListener("keydown",escape,true);
  },[editing]);
  function storeAnnotation(a:Annotation){return save({page:a.page,annotation:a}).catch(e=>{setError(String(e));throw e;});}
  function createExcerpt(text:string,rects:number[][],image="") {
    const a:Annotation={id:crypto.randomUUID(),page,text,rects,image,comment:"",translation:"",color:"#f4d144"};
    setSelection({page,text,rects,anchor:rects[rects.length-1]??[.5,.1,0,0],annotation:a});
    void storeAnnotation(a).catch(()=>{});window.getSelection()?.removeAllRanges();
  }
  function changeExcerpt(patch:Partial<Annotation>){const a=selection?.annotation;if(!a)return;const updated={...a,...patch};setSelection(s=>s?{...s,annotation:updated}:s);void storeAnnotation(updated).catch(()=>{});}
  const canvas=useRef<HTMLCanvasElement|null>(null);
  const layer=useRef<HTMLDivElement|null>(null);
  const sheet=useRef<HTMLDivElement|null>(null);
  const [outline,setOutline]=useState<{title:string;dest:unknown}[]>([]);

  useEffect(()=>{
    if(!selection)return;
    let frame=0;
    const place=()=>{
      frame=0;
      if(!reading.current||!popup.current||!sheet.current||!scroll.current)return;
      const host=reading.current.getBoundingClientRect();const pageBox=sheet.current.getBoundingClientRect();const clip=scroll.current.getBoundingClientRect();
      const [x,y,w,h]=selection.anchor;
      const anchor={left:pageBox.left+x*pageBox.width,top:pageBox.top+y*pageBox.height,right:pageBox.left+(x+w)*pageBox.width,bottom:pageBox.top+(y+h)*pageBox.height};
      const sx=host.width/reading.current.offsetWidth;const sy=host.height/reading.current.offsetHeight;
      const width=popup.current.offsetWidth*sx;const height=popup.current.offsetHeight*sy;const gap=8;
      const left=Math.max(host.left+gap,Math.min((anchor.left+anchor.right-width)/2,host.right-width-gap));
      const below=anchor.bottom+gap;const above=anchor.top-height-gap;
      const preferred=below+height<=host.bottom-gap?below:above;
      const top=Math.max(host.top+gap,Math.min(preferred,host.bottom-height-gap));
      setPopupPosition({left:(left-host.left)/sx,top:(top-host.top)/sy,visible:anchor.bottom>=clip.top&&anchor.top<=clip.bottom&&anchor.right>=clip.left&&anchor.left<=clip.right});
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(place);};
    const observer=new ResizeObserver(schedule);
    [reading.current,popup.current,sheet.current].forEach(el=>{if(el)observer.observe(el);});
    const scroller=scroll.current;scroller?.addEventListener("scroll",schedule,{passive:true});window.addEventListener("resize",schedule);place();
    return()=>{cancelAnimationFrame(frame);observer.disconnect();scroller?.removeEventListener("scroll",schedule);window.removeEventListener("resize",schedule);};
  },[selection,scale]);
  useEffect(()=>{
    if(!selection)return;
    const escape=(e:KeyboardEvent)=>{if(e.key==="Escape"){e.preventDefault();e.stopImmediatePropagation();dismissSelection();}};
    window.addEventListener("keydown",escape,true);return()=>window.removeEventListener("keydown",escape,true);
  },[selection]);

  useEffect(()=>{
    let active=true;
    const task=getDocument({data:Uint8Array.from(atob(value.pdf),c=>c.charCodeAt(0)),isEvalSupported:false});
    void task.promise.then(async doc=>{if(active){setPdf(doc);const items=await doc.getOutline();if(active)setOutline(items??[]);}}).catch(e=>{if(active)setError(String(e));});
    return()=>{active=false;void task.destroy();};
  },[value.pdf]);
  function jumpToPage(next:number){requestAnimationFrame(()=>{const el=scroll.current?.querySelector<HTMLElement>(`[data-pdf-page="${next}"]`);if(el&&scroll.current){const box=scroll.current.getBoundingClientRect();scroll.current.scrollTop+=(el.getBoundingClientRect().top-box.top)/(box.height/scroll.current.offsetHeight);}});}
  useEffect(()=>{
    const el=scroll.current;if(!pdf||!el||!autoFit||studyOpen)return;
    let active=true;
    let size:{width:number;height:number}|undefined;
    const fit=()=>{if(!active||!size||el.clientWidth<40||el.clientHeight<40)return;
      const next=Math.max(.1,Math.min(3,(el.clientWidth-32)/size.width,(el.clientHeight-24)/size.height));
      setScale(current=>Math.abs(current-next)>.002?next:current);
    };
    void pdf.getPage(page).then(p=>{size=p.getViewport({scale:1});fit();}).catch(e=>{if(active)setError(String(e));});
    const observer=new ResizeObserver(fit);observer.observe(el);
    return()=>{active=false;observer.disconnect();};
  },[pdf,page,autoFit,studyOpen,fullscreen]);
  useEffect(()=>{
    if(studyOpen||value.page===page)return;
    const timer=setTimeout(()=>{void save({page}).catch(e=>setError(String(e)));},650);
    return()=>clearTimeout(timer);
  },[continuous,studyOpen,page,value.page,save]);

  function selectText(){
    if(busy||saving)return;
    const selected=window.getSelection();if(!selected?.rangeCount||!sheet.current)return;
    const range=selected.getRangeAt(0);if(!layer.current?.contains(range.commonAncestorContainer))return;
    const text=selected.toString().trim();if(!text)return;
    const bounds=sheet.current.getBoundingClientRect();
    const clamp=(n:number)=>Math.max(0,Math.min(1,n));
    const rects=Array.from(range.getClientRects()).filter(r=>r.width>0&&r.height>0).map(r=>[clamp((r.x-bounds.x)/bounds.width),clamp((r.y-bounds.y)/bounds.height),clamp(r.width/bounds.width),clamp(r.height/bounds.height)]);
    if(!rects.length)return;
    if(annotating){if(tool==="text")createExcerpt(text,rects);return;}
    const focusAtEnd=selected.focusNode===range.endContainer&&selected.focusOffset===range.endOffset;
    selectionEpoch.current++;setPopupPosition(undefined);
    setSelection({page,text,rects,anchor:focusAtEnd?rects[rects.length-1]:rects[0]});setTranslation("");setComment("");
  }
  async function go(next:number){if(busy)return;dismissSelection();setPage(next);jumpToPage(next);try{await save({page:next});}catch(e){setError(String(e));}}
  async function translate(){if(!selection)return;const epoch=selectionEpoch.current;setBusy(true);setError("");try{const result=await request("library/translate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:selection.text,model,target,provider})});if(epoch===selectionEpoch.current)setTranslation(result.translation);}catch(e){if(epoch===selectionEpoch.current)setError(String(e));}finally{setBusy(false);}}
  async function add(){if(!selection||saving)return;setSaving(true);try{await save({page,annotation:{text:selection.text,rects:selection.rects,comment,translation}});dismissSelection();setTranslation("");setComment("");window.getSelection()?.removeAllRanges();}catch(e){setError(String(e));}finally{setSaving(false);}}
  const selectedRects=selection?.rects??[];
  const selectionBounds=selectedRects.length?{
    left:Math.min(...selectedRects.map(r=>r[0])),top:Math.min(...selectedRects.map(r=>r[1])),
    right:Math.max(...selectedRects.map(r=>r[0]+r[2])),bottom:Math.max(...selectedRects.map(r=>r[1]+r[3])),
  }:undefined;
  return <div ref={reading} className={`library-reading ${annotating?"is-annotating":""}`}>
    <nav className="library-reading-nav"><button disabled={page<=1} onClick={()=>void go(page-1)}>上一页</button><span>{page} / {value.pages}</span><button disabled={page>=value.pages} onClick={()=>void go(page+1)}>下一页</button><button onClick={()=>{setAutoFit(false);setScale(s=>Math.max(.1,s-.2));}}>−</button><button title="适应窗口" aria-label="适应窗口" onClick={()=>setAutoFit(true)}>{Math.round(scale*100)}%</button><button onClick={()=>{setAutoFit(false);setScale(s=>Math.min(3,s+.2));}}>＋</button><button aria-label="切换滚动和翻页模式" aria-pressed={continuous} onClick={()=>{dismissSelection();setContinuous(v=>!v);jumpToPage(page);}}>{continuous?"连续滚动":"单页翻页"}</button></nav>
    <div className="library-annotation-tools">
      <button className="library-study-toggle" title={studyOpen?"返回文章":"学习画布"} aria-label={studyOpen?"返回文章":"学习画布"} aria-pressed={studyOpen} onClick={()=>{setStudyOpen(v=>!v);dismissSelection();}}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">{studyOpen?<><path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h8"/></>:<><rect x="2" y="9" width="6" height="6" rx="1"/><rect x="16" y="3" width="6" height="6" rx="1"/><rect x="16" y="15" width="6" height="6" rx="1"/><path d="M8 12h4M12 6v12M12 6h4M12 18h4"/></>}</svg></button>
      <button title="切换阅读 / 批注模式" aria-label="批注模式" aria-pressed={annotating} onClick={()=>{setAnnotating(v=>!v);dismissSelection();}}>✎</button>
      {annotating&&<><button title="划词" aria-label="划词" aria-pressed={tool==="text"} onClick={()=>setTool("text")}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M5 5h14M12 5v12M8 17h8M4 21h16"/></svg></button><button title="框选截图" aria-label="框选截图" aria-pressed={tool==="crop"} onClick={()=>setTool("crop")}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5"/><rect x="7" y="7" width="10" height="10" rx="1" strokeDasharray="2 2"/></svg></button><button title="添加图片" aria-label="添加图片" onClick={()=>imageInput.current?.click()}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M13 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-9M3 17l6-6 5 5 3-3 4 4M19 1v8M15 5h8"/><circle cx="7.5" cy="7.5" r="1"/></svg></button></>}
      <input hidden ref={imageInput} type="file" accept="image/png,image/jpeg" onChange={async e=>{const file=e.target.files?.[0];e.target.value="";if(!file)return;try{
        if(file.size>4*1024*1024)throw new Error("图片请小于 4 MiB");
        const bitmap=await createImageBitmap(file);const c=document.createElement("canvas");const k=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));c.width=Math.max(1,Math.round(bitmap.width*k));c.height=Math.max(1,Math.round(bitmap.height*k));c.getContext("2d")!.drawImage(bitmap,0,0,c.width,c.height);bitmap.close();
        const count=(value.annotations??[]).filter(a=>a.learning).length;
        await storeAnnotation({id:crypto.randomUUID(),page,text:"",title:file.name,comment:"",translation:"",rects:[],image:c.toDataURL("image/jpeg",.85),learning:true,position:{x:(count%3)*280,y:Math.floor(count/3)*320}});setStudyOpen(true);
      }catch(err){setError(String(err));}}}/>
    </div>
    {error&&<p role="alert">{error}</p>}
      {settingsOpen&&<section className="library-settings-popover" role="dialog" aria-label="Library 设置">
        <header><strong>Library 设置</strong><button aria-label="关闭 Library 设置" onClick={()=>setSettingsOpen(false)}>×</button></header>
        <p>对所有文献生效。API 地址和密钥统一在 OAW 总设置中管理。</p>
        <label>翻译服务<select value={provider} onChange={e=>useLibrarySettings.setState({provider:e.target.value})}><option value="openai">OAW 模型连接</option><option value="deepl">DeepL Free</option></select></label>
        {provider==="openai"&&<label>翻译模型<select value={model} onChange={e=>useLibrarySettings.setState({model:e.target.value})}>{models.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
        <label>目标语言<input value={target} onChange={e=>useLibrarySettings.setState({target:e.target.value})}/></label>
        <small>连接配置：返回窗口后，打开 OAW 设置 → Models / DeepL。此处修改不影响 Agent 的模型选择。</small>
      </section>}
    {studyOpen&&<StudyCanvas name={value.study_title??"学习画布"} rename={study_title=>save({study_title})} title={value.filename??"文章"} linked={value.study_layout??false} layout={positions=>save({study_positions:positions})} items={(value.annotations??[]).filter(a=>a.learning)} move={storeAnnotation} open={setEditing} locate={p=>{setStudyOpen(false);void go(p);}}/>}
    <div style={studyOpen?{display:"none"}:undefined} className={`library-reading-grid ${sidebarOpen?"is-sidebar-open":""}`}><div ref={scroll} className="library-page-scroll" onScroll={()=>{
      if(!continuous||selection||cropStart.current||!scroll.current)return;
      const top=scroll.current.getBoundingClientRect().top;
      const el=Array.from(scroll.current.querySelectorAll<HTMLElement>("[data-pdf-page]")).find(el=>el.getBoundingClientRect().bottom>top+80);
      if(el)setPage(Number(el.dataset.pdfPage));
    }}>{pdf&&(continuous?Array.from({length:value.pages},(_,i)=>i+1):[page]).map(pageNumber=><PdfPageSurface key={pageNumber} pdf={pdf} number={pageNumber} scale={scale} onPointerDownCapture={e=>{
      sheet.current=e.currentTarget;canvas.current=e.currentTarget.querySelector("canvas");layer.current=e.currentTarget.querySelector(".textLayer");setPage(pageNumber);
    }} onMouseDown={()=>{if(!busy&&!saving)dismissSelection();}} onMouseUp={selectText} onKeyUp={e=>{if(e.shiftKey)selectText();}} onClick={e=>{
      if(window.getSelection()?.toString())return;const b=e.currentTarget.getBoundingClientRect(),x=(e.clientX-b.left)/b.width,y=(e.clientY-b.top)/b.height;
      const a=(value.annotations??[]).find(a=>a.page===pageNumber&&a.rects.some(r=>x>=r[0]&&x<=r[0]+r[2]&&y>=r[1]&&y<=r[1]+r[3]));
      if(a)setSelection({page:pageNumber,text:a.text,rects:a.rects,anchor:[x,y,0,0],annotation:a});
    }}>
      <div className="library-highlight-layer">{(value.annotations??[]).filter(a=>a.page===pageNumber).flatMap(a=>a.rects.map((r,i)=><span key={`${a.id}-${i}`} style={{background:`${a.color??"#f4d144"}66`,left:`${r[0]*100}%`,top:`${r[1]*100}%`,width:`${r[2]*100}%`,height:`${r[3]*100}%`}}/>))}</div>
      {selectionBounds&&selection?.page===pageNumber&&<div className="library-selected-outline" aria-hidden="true" style={{left:`${selectionBounds.left*100}%`,top:`${selectionBounds.top*100}%`,width:`${(selectionBounds.right-selectionBounds.left)*100}%`,height:`${(selectionBounds.bottom-selectionBounds.top)*100}%`}}/>}
      {annotating&&tool==="crop"&&<div className="library-crop-layer" onPointerDown={e=>{e.stopPropagation();const b=e.currentTarget.getBoundingClientRect();cropStart.current=[(e.clientX-b.left)/b.width,(e.clientY-b.top)/b.height];e.currentTarget.setPointerCapture(e.pointerId);}}
        onPointerMove={e=>{if(!cropStart.current)return;const b=e.currentTarget.getBoundingClientRect(),x=Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y=Math.max(0,Math.min(1,(e.clientY-b.top)/b.height));const [sx,sy]=cropStart.current;setCrop([Math.min(x,sx),Math.min(y,sy),Math.abs(x-sx),Math.abs(y-sy)]);}}
        onPointerUp={e=>{e.stopPropagation();cropStart.current=undefined;if(crop&&crop[2]>.005&&crop[3]>.005&&canvas.current){const source=canvas.current,c=document.createElement("canvas");c.width=Math.max(1,Math.round(crop[2]*source.width));c.height=Math.max(1,Math.round(crop[3]*source.height));c.getContext("2d")!.drawImage(source,crop[0]*source.width,crop[1]*source.height,c.width,c.height,0,0,c.width,c.height);createExcerpt("",[crop],c.toDataURL("image/jpeg",.85));}setCrop(undefined);}}
        onPointerCancel={()=>{cropStart.current=undefined;setCrop(undefined);}}>{crop&&pageNumber===page&&<span style={{left:`${crop[0]*100}%`,top:`${crop[1]*100}%`,width:`${crop[2]*100}%`,height:`${crop[3]*100}%`}}/>}</div>}
    </PdfPageSurface>)}</div>
    <button type="button" className="library-sidebar-toggle" aria-label={sidebarOpen?"收起目录与批注":"展开目录与批注"} title={sidebarOpen?"收起目录与批注":"展开目录与批注"} aria-expanded={sidebarOpen} onClick={()=>setSidebarOpen(open=>!open)}>{sidebarOpen?"›":"‹"}</button>
    <aside className="library-reading-sidebar" aria-label="目录与批注" aria-hidden={!sidebarOpen}>
      <details><summary>目录（{outline.length}）</summary>{outline.map((item,i)=><button key={i} onClick={async()=>{if(!pdf)return;try{const dest=typeof item.dest==="string"?await pdf.getDestination(item.dest):item.dest as any[];if(dest){const first=dest[0];await go(typeof first==="number"?first+1:await pdf.getPageIndex(first)+1);}}catch(e){setError(String(e));}}}>{item.title}</button>)}</details>
      <h4>批注（{value.annotations?.length??0}）</h4>{(value.annotations??[]).map(a=><article key={a.id}><button onClick={()=>void go(a.page)}>第 {a.page} 页 · 定位</button><blockquote>{a.text}</blockquote><p>{a.comment}</p><p className="library-translation">{a.translation}</p><button onClick={()=>void save({delete_annotation:a.id}).catch(e=>setError(String(e)))}>删除批注</button></article>)}
    </aside></div>
    {selection&&<div ref={popup} role="dialog" aria-label="划词批注与翻译" aria-modal="false" className={`library-selection-popup nodrag nopan nowheel ${selection.annotation?"is-excerpt-bar":""}`} style={{left:popupPosition?.left??0,top:popupPosition?.top??0,visibility:popupPosition?.visible?"visible":"hidden"}}>
      {selection.annotation?<>
        <button onClick={()=>{const count=(value.annotations??[]).filter(a=>a.learning).length;changeExcerpt({learning:true,position:selection.annotation?.position??{x:(count%3)*280,y:Math.floor(count/3)*320}});}}>{selection.annotation.learning?"已添加到学习":"添加到学习"}</button>
        <label title="调色盘" className="library-color-picker">◉<input type="color" aria-label="调色盘" value={selection.annotation.color??"#f4d144"} onChange={e=>changeExcerpt({color:e.target.value})}/></label>
        {["#f4d144","#71c98b","#6aaef5","#f08080"].map((color,i)=><button key={color} aria-label={["黄色","绿色","蓝色","红色"][i]} className="library-color-dot" style={{background:color}} onClick={()=>changeExcerpt({color})}/>)}
        <button onClick={()=>{setEditing(selection.annotation);dismissSelection();}}>编辑</button><button aria-label="关闭摘录浮窗" onClick={dismissSelection}>×</button>
      </>:<>
      <header><strong>划词批注 · 第 {page} 页</strong><button aria-label="关闭划词浮窗" disabled={saving} onClick={dismissSelection}>×</button></header>
      <blockquote>{selection.text}</blockquote>
      <label>批注<textarea placeholder="写下想法，留空则仅保存高亮" value={comment} onChange={e=>setComment(e.target.value)}/></label>
      <div className="library-popup-actions"><button disabled={busy||saving} onClick={()=>void add()}>{saving?"保存中…":"保存高亮与批注"}</button><button disabled={busy||saving} onClick={()=>void translate()}>{busy?"翻译中…":"翻译选段"}</button></div>
      {translation&&<><p className="library-translation">{translation}</p><button onClick={()=>void navigator.clipboard.writeText(translation).catch(e=>setError(String(e)))}>复制译文</button><small>点击保存，将译文和批注一起归档。</small></>}
      {error&&<p role="alert">{error}</p>}
      <small>仅点击翻译时发送选中文字 · {provider==="deepl"?"DeepL Free":model}</small>
      </>}
    </div>}
    {editing&&<div className="library-edit-backdrop"><section className="library-excerpt-editor" role="dialog" aria-label="编辑摘录">
      <header><strong>编辑摘录</strong><button aria-label="关闭编辑" onClick={()=>setEditing(undefined)}>×</button></header>
      <label>标题<input value={editing.title??""} onChange={e=>setEditing({...editing,title:e.target.value})}/></label>
      {editing.image?<img src={editing.image}/>:<blockquote>{editing.text}</blockquote>}
      <label>批注<textarea value={editing.comment} onChange={e=>setEditing({...editing,comment:e.target.value})}/></label>
      <button disabled={saving} onClick={async()=>{setSaving(true);try{await storeAnnotation(editing);setEditing(undefined);}catch{}finally{setSaving(false);}}}>{saving?"保存中…":"保存"}</button>
    </section></div>}
  </div>;
}
