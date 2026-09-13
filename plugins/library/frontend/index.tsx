import { t, useLocale } from "@oaw/plugin-api";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import "./style.css";
import type { ReadingValue } from "./PdfReading";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { getNodeType } from "../../../frontend/src/state/catalog";
import { useReaderEntrance } from "./useReaderEntrance";
import { ReaderTransition } from "./GlassTransition";
import { loadPaper, loadPaperPreview, rememberPaper, forgetPaper, type PaperPreview } from "./paperCache";
const PdfReading = lazy(() => import("./PdfReading").then(module => ({default:module.PdfReading})));

async function api(path: string, body?: unknown) {
  const response = await fetch(`/api/${path}`, body === undefined ? undefined : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function encoded(file: File): Promise<string> {
  return new Promise((resolve,reject) => { const reader=new FileReader(); reader.onerror=()=>reject(reader.error); reader.onload=()=>resolve(String(reader.result).split(",")[1]); reader.readAsDataURL(file); });
}
type Doc = {revision:number;value:ReadingValue & {thumbnail:string;notes:string}};
function usePaper(id:string,enabled=true) {
  const [doc,setDoc]=useState<Doc>(); const [error,setError]=useState("");
  useEffect(()=>{if(!enabled)return;let active=true; void loadPaper(id).then(d=>{if(active)setDoc(d);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[id,enabled]);
  useEffect(()=>{if(doc)rememberPaper(id,doc);},[id,doc]);
  return {doc,setDoc,error,setError};
}
function Thumbnail({card}:PluginViewProps) {
  useLocale();
  const [doc,setDoc]=useState<PaperPreview>(); const [error,setError]=useState("");
  useEffect(()=>{let active=true;void loadPaperPreview(card.id).then(value=>{if(active){setDoc(value);setError("");}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[card.id,card.updated_at]);
  return <div className="library-paper-thumb">{doc?.value.thumbnail&&<img draggable={false} src={doc.value.thumbnail} alt={t("PDF cover")}/>}<div><strong>{card.name}</strong><p>{String(card.config.authors??"")} {String(card.config.year??"")}</p><small>{doc?.value.pages??0} {t("页 ·")} {error||"PDF"}</small></div></div>;
}
function Reader(props:PluginViewProps) {
  useLocale();
  // CardFrame mounts both preview and body. CSS-hidden bodies must not load PDFs.
  return props.level === "inspector" || props.level === "workspace" ? <PaperMagazine {...props}/> : null;
}
function PaperMagazine(props:PluginViewProps) {
  useLocale();
  const {card}=props;
  const root=useRef<HTMLDivElement>(null);
  const [open,setOpen]=useState(false);
  const [attempt,setAttempt]=useState(0);
  const [summary,setSummary]=useState<PaperPreview & {value:{annotations:NonNullable<ReadingValue["annotations"]>;page:number}}>();
  const [error,setError]=useState("");
  const cards=useWorldStore(s=>s.cards), edges=useWorldStore(s=>s.edges), catalog=useWorldStore(s=>s.catalog);
  const connected=new Set(edges.flatMap(e=>e.source===card.id?[e.target]:e.target===card.id?[e.source]:[]));
  const agents=cards.filter(c=>connected.has(c.id)&&getNodeType(catalog,c.type)?.traits.includes("core.agent")).length;
  useEffect(()=>{let active=true;void api(`library/papers/${card.id}/preview?details=true`).then(d=>{if(active){setSummary(d);setError("");}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[card.id,card.updated_at,open]);
  useEffect(()=>{const el=root.current?.closest(".world-card");const expand=()=>setOpen(true);el?.addEventListener("oaw:expand-reader",expand);return()=>el?.removeEventListener("oaw:expand-reader",expand);},[]);
  const annotations=summary?.value.annotations??[];
  return <div ref={root} className="library-magazine nodrag nopan nowheel">
    <h3>{card.name}</h3>
    {error&&<p role="alert">{error}</p>}
    <div className="library-magazine-summary">
      <div>{summary?.value.thumbnail?<img src={summary.value.thumbnail} alt={t("论文封面快照")} draggable={false}/>:<p>{t("尚未导入 PDF")}</p>}<small>{summary?.value.pages??0} {t("页 · 封面快照")}</small></div>
      <div className="library-magazine-stats">{[[agents,t("连接 Agent")],[annotations.length,t("批注")],[annotations.filter(a=>a.learning).length,t("学习卡片")]].map(([n,label])=><div key={label}><strong>{n}</strong><small>{label}</small></div>)}</div>
    </div>
    <h4>{t("批注 ·")} {annotations.length}</h4>
    <div className="library-magazine-notes nowheel" tabIndex={0} aria-label={t("批注列表")} onWheel={e=>e.stopPropagation()}>
      {annotations.length?annotations.map(a=><article key={a.id}><small>{t("Page {page}", { page: a.page })}{a.title?` · ${a.title}`:""}</small>{a.image&&<img src={a.image} alt={t("截图批注")}/>}{a.text&&<blockquote>{a.text}</blockquote>}{a.translation&&<p>{a.translation}</p>}{a.comment&&<p>{a.comment}</p>}</article>):<p>{t("暂无批注")}</p>}
    </div>
    {open&&<ActiveReader key={attempt} {...props} onRetry={()=>setAttempt(n=>n+1)} onClose={()=>setOpen(false)}/>}
  </div>;
}
export function ActiveReader({card,onClose,onRetry}:PluginViewProps & {onClose:()=>void;onRetry:()=>void}) {
  useLocale();
  const {phase,mountReader,failure,reduced,markReady,invalidate,fail,finish,cancel}=useReaderEntrance();
  const {doc,setDoc,error}=usePaper(card.id,mountReader);
  const contentRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(contentRef.current)contentRef.current.inert=phase!=="complete";},[phase]);
  useEffect(()=>{if(error)fail(error);else if(doc&&!doc.value.pdf)fail(t("此节点尚未导入 PDF。"));},[doc,error,fail]);
  const latest=useRef(doc); latest.current=doc;
  const writes=useRef<Promise<void>>(Promise.resolve());
  function updateDocument(args:Record<string,unknown>){
    const next=writes.current.catch(()=>{}).then(async()=>{const current=latest.current;if(!current)return;
      const updated=await api(`nodes/${card.id}/actions/annotate`,{arguments:args,expected_revision:current.revision});latest.current=updated;setDoc(updated);
    });writes.current=next;return next;
  }
  const readerRef=useRef<HTMLDialogElement>(null);
  const fullscreen=true;
  const [settingsOpen,setSettingsOpen]=useState(false);
  const closed=useRef(false);
  useEffect(()=>{if(phase==="cancelled"&&!closed.current){closed.current=true;void writes.current.catch(()=>{}).then(onClose);}},[phase,onClose]);
  function resizeReader(_expanded:boolean){cancel();}
  useEffect(()=>{readerRef.current?.showModal();return()=>readerRef.current?.close();},[]);
  useEffect(()=>{const escape=(e:KeyboardEvent)=>{if(e.key==="Escape"&&readerRef.current?.matches(":modal")){if(readerRef.current.querySelector(".library-selection-popup, .library-edit-backdrop"))return;e.preventDefault();e.stopImmediatePropagation();resizeReader(false);}};window.addEventListener("keydown",escape,true);return()=>window.removeEventListener("keydown",escape,true);},[]);
  return createPortal(<dialog ref={readerRef} aria-label={card.name} data-entrance-phase={phase} className="library-reader nodrag nopan nowheel" onCancel={e=>{e.preventDefault();resizeReader(false);}}>
    {["spreading","waiting","revealing","concealing","retracting"].includes(phase)&&<ReaderTransition phase={phase} reduced={reduced} content={contentRef} onComplete={finish}/>}
    {phase!=="complete"&&<button className="library-transition-cancel" disabled={["concealing","retracting","cancelled"].includes(phase)} aria-label={t("返回窗口")} title={t("返回窗口")} onClick={()=>resizeReader(false)}>↶</button>}
    {phase==="failed"&&<div className="library-reader-failure" role="alert"><p>{failure}</p>
      {doc&&!doc.value.pdf&&<label>{t("导入 PDF")}<input type="file" accept=".pdf" onChange={async event=>{const file=event.target.files?.[0];if(!file)return;try{const updated=await api(`nodes/${card.id}/actions/import`,{arguments:{filename:file.name,pdf:await encoded(file)},expected_revision:doc.revision});rememberPaper(card.id,updated);onRetry();}catch(error){fail(String(error));}}}/></label>}
      <button onClick={()=>{forgetPaper(card.id);onRetry();}}>{t("重试")}</button><button onClick={()=>resizeReader(false)}>{t("返回窗口")}</button></div>}
    <div ref={contentRef} className="library-reader-content" aria-hidden={phase!=="complete"}>
    <div className="library-reader-toolbar">
      <span title={card.name}>{card.name}</span>
      {doc?.value.pdf&&<button type="button" aria-label={t("Library 设置")} title={t("Library 设置")} aria-expanded={settingsOpen} onClick={()=>setSettingsOpen(open=>!open)}>⚙</button>}
      {fullscreen&&<button type="button" aria-label={t("返回窗口")} title={t("返回窗口")} onClick={()=>resizeReader(false)}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 4-5 5 5 5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></svg></button>}
    </div>
    {error&&<p role="alert">{error}</p>}
    {doc?.value.pdf&&mountReader&&phase!=="failed"&&<Suspense fallback={null}><PdfReading onReady={markReady} onPreparing={invalidate} onLoadError={fail} value={doc.value} save={updateDocument} fullscreen={fullscreen} settingsOpen={settingsOpen} setSettingsOpen={setSettingsOpen}/></Suspense>}
    </div>
  </dialog>,document.body);
}
function Region({card,host}:PluginViewProps) {
  useLocale();
  const [message,setMessage]=useState(""); const [busy,setBusy]=useState(false);
  const toolsRef=useRef<HTMLDivElement>(null);
  const importing=useRef(false);
  async function upload(files:FileList|null) {
    if(!files||importing.current)return;importing.current=true;setBusy(true);
    try {let i=0;for(const file of Array.from(files)) {if(!file.name.toLowerCase().endsWith(".pdf"))continue;
      if(file.size>25*1024*1024)throw new Error(t("每个 PDF 最大 25 MiB"));
      setMessage(t("正在导入 {v0}", { v0: String(file.name) }));
      const node=await api("nodes",{type:"library.paper",name:file.name.replace(/\.pdf$/i,"").slice(0,200),parent_id:card.id,position:{x:card.position.x+50+(i%3)*320,y:card.position.y+180+Math.floor(i/3)*240}});
      try{const d=await api(`nodes/${node.id}/document`);await api(`nodes/${node.id}/actions/import`,{arguments:{filename:file.name,pdf:await encoded(file)},expected_revision:d.revision});}
      catch(error){await fetch(`/api/nodes/${node.id}`,{method:"DELETE"});throw error;}i++;
    }setMessage(i?t("已导入 {v0} 篇 PDF", { v0: String(i) }):t("请选择 PDF 文件"));}catch(e){setMessage(String(e));}finally{importing.current=false;setBusy(false);}
  }
  // Native file events only: do not intercept OAW pointer-based node movement.
  useEffect(()=>{
    const frame=toolsRef.current?.closest(".container-frame");
    if(!frame)return;
    const over=(event:Event)=>{const e=event as DragEvent;if(e.dataTransfer?.types.includes(t("Files"))){e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect="copy";}};
    const drop=(event:Event)=>{const e=event as DragEvent;if(e.dataTransfer?.files.length){e.preventDefault();e.stopPropagation();void upload(e.dataTransfer.files);}};
    frame.addEventListener("dragover",over);frame.addEventListener("drop",drop);
    return()=>{frame.removeEventListener("dragover",over);frame.removeEventListener("drop",drop);};
  },[card.id,card.position.x,card.position.y]);
  return <div ref={toolsRef} className="library-region-tools nodrag nopan">
    <input aria-label={t("Library name")} defaultValue={card.name} onBlur={async e=>{try{const name=e.target.value.trim();if(!name)throw new Error(t("名称不能为空"));const r=await fetch(`/api/nodes/${card.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});if(!r.ok)throw new Error(await r.text());}catch(err){setMessage(String(err));}}}/>
    <input aria-label={t("Library description")} defaultValue={String(card.config.description??"")} onBlur={e=>void host.updateConfig({description:e.target.value}).catch(err=>setMessage(String(err)))}/>
    <label>{t("导入 / 拖入 PDF")}<input disabled={busy} type="file" accept=".pdf" multiple onChange={e=>{void upload(e.target.files);e.target.value="";}}/></label><small role="status">{message}</small>
  </div>;
}
export default {apiVersion:1,views:{region:Region,thumbnail:Thumbnail,reader:Reader}} satisfies FrontendPlugin;
