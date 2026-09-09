import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";
import "./style.css";
import type { ReadingValue } from "./PdfReading";
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
function usePaper(id:string) {
  const [doc,setDoc]=useState<Doc>(); const [error,setError]=useState("");
  useEffect(()=>{let active=true; void api(`nodes/${id}/document`).then(d=>{if(active)setDoc(d);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[id]);
  return {doc,setDoc,error,setError};
}
function Thumbnail({card}:PluginViewProps) {
  const {doc,error}=usePaper(card.id);
  return <div className="library-paper-thumb">{doc?.value.thumbnail&&<img draggable={false} src={doc.value.thumbnail} alt="PDF cover"/>}<div><strong>{card.name}</strong><p>{String(card.config.authors??"")} {String(card.config.year??"")}</p><small>{doc?.value.pages??0} 页 · {error||"PDF"}</small></div></div>;
}
function Reader({card}:PluginViewProps) {
  const {doc,setDoc,error,setError}=usePaper(card.id);
  const latest=useRef(doc); latest.current=doc;
  const writes=useRef<Promise<void>>(Promise.resolve());
  function updateDocument(args:Record<string,unknown>){
    const next=writes.current.catch(()=>{}).then(async()=>{const current=latest.current;if(!current)return;
      const updated=await api(`nodes/${card.id}/actions/annotate`,{arguments:args,expected_revision:current.revision});latest.current=updated;setDoc(updated);
    });writes.current=next;return next;
  }
  const readerRef=useRef<HTMLDialogElement>(null);
  const [fullscreen,setFullscreen]=useState(false);
  const [settingsOpen,setSettingsOpen]=useState(false);
  function resizeReader(expanded:boolean){const reader=readerRef.current;if(!reader)return;reader.close();if(expanded)reader.showModal();else reader.show();setFullscreen(expanded);}
  useEffect(()=>{const cardElement=readerRef.current?.closest(".world-card");const expand=()=>resizeReader(true);cardElement?.addEventListener("oaw:expand-reader",expand);return()=>cardElement?.removeEventListener("oaw:expand-reader",expand);},[]);
  useEffect(()=>{const escape=(e:KeyboardEvent)=>{if(e.key==="Escape"&&readerRef.current?.matches(":modal")){if(readerRef.current.querySelector(".library-selection-popup, .library-edit-backdrop"))return;e.preventDefault();e.stopImmediatePropagation();resizeReader(false);}};window.addEventListener("keydown",escape,true);return()=>window.removeEventListener("keydown",escape,true);},[]);
  return <dialog open ref={readerRef} aria-label={card.name} className="library-reader nodrag nopan nowheel" onCancel={e=>{e.preventDefault();resizeReader(false);}}>
    <div className="library-reader-toolbar">
      <span title={card.name}>{card.name}</span>
      {doc?.value.pdf&&<button type="button" aria-label="Library 设置" title="Library 设置" aria-expanded={settingsOpen} onClick={()=>setSettingsOpen(open=>!open)}>⚙</button>}
      {fullscreen&&<button type="button" aria-label="返回窗口" title="返回窗口" onClick={()=>resizeReader(false)}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 4-5 5 5 5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></svg></button>}
    </div>
    {error&&<p role="alert">{error}</p>}
    {doc?.value.pdf?<Suspense fallback={<p>正在加载阅读器…</p>}><PdfReading value={doc.value} save={updateDocument} fullscreen={fullscreen} settingsOpen={settingsOpen} setSettingsOpen={setSettingsOpen}/></Suspense>:<p>从 Library 导入 PDF，或选择文件：<input type="file" accept=".pdf" onChange={async e=>{const f=e.target.files?.[0];if(!f||!doc)return;try{setDoc(await api(`nodes/${card.id}/actions/import`,{arguments:{filename:f.name,pdf:await encoded(f)},expected_revision:doc.revision}));}catch(err){setError(String(err));}}}/></p>}
  </dialog>;
}
function Region({card,host}:PluginViewProps) {
  const [message,setMessage]=useState(""); const [busy,setBusy]=useState(false);
  const toolsRef=useRef<HTMLDivElement>(null);
  const importing=useRef(false);
  async function upload(files:FileList|null) {
    if(!files||importing.current)return;importing.current=true;setBusy(true);
    try {let i=0;for(const file of Array.from(files)) {if(!file.name.toLowerCase().endsWith(".pdf"))continue;
      if(file.size>25*1024*1024)throw new Error("每个 PDF 最大 25 MiB");
      setMessage(`正在导入 ${file.name}`);
      const node=await api("nodes",{type:"library.paper",name:file.name.replace(/\.pdf$/i,"").slice(0,200),parent_id:card.id,position:{x:card.position.x+50+(i%3)*320,y:card.position.y+180+Math.floor(i/3)*240}});
      try{const d=await api(`nodes/${node.id}/document`);await api(`nodes/${node.id}/actions/import`,{arguments:{filename:file.name,pdf:await encoded(file)},expected_revision:d.revision});}
      catch(error){await fetch(`/api/nodes/${node.id}`,{method:"DELETE"});throw error;}i++;
    }setMessage(i?`已导入 ${i} 篇 PDF`:"请选择 PDF 文件");}catch(e){setMessage(String(e));}finally{importing.current=false;setBusy(false);}
  }
  // Native file events only: do not intercept OAW pointer-based node movement.
  useEffect(()=>{
    const frame=toolsRef.current?.closest(".container-frame");
    if(!frame)return;
    const over=(event:Event)=>{const e=event as DragEvent;if(e.dataTransfer?.types.includes("Files")){e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect="copy";}};
    const drop=(event:Event)=>{const e=event as DragEvent;if(e.dataTransfer?.files.length){e.preventDefault();e.stopPropagation();void upload(e.dataTransfer.files);}};
    frame.addEventListener("dragover",over);frame.addEventListener("drop",drop);
    return()=>{frame.removeEventListener("dragover",over);frame.removeEventListener("drop",drop);};
  },[card.id,card.position.x,card.position.y]);
  return <div ref={toolsRef} className="library-region-tools nodrag nopan">
    <input aria-label="Library name" defaultValue={card.name} onBlur={async e=>{try{const name=e.target.value.trim();if(!name)throw new Error("名称不能为空");const r=await fetch(`/api/nodes/${card.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});if(!r.ok)throw new Error(await r.text());}catch(err){setMessage(String(err));}}}/>
    <input aria-label="Library description" defaultValue={String(card.config.description??"")} onBlur={e=>void host.updateConfig({description:e.target.value}).catch(err=>setMessage(String(err)))}/>
    <label>导入 / 拖入 PDF<input disabled={busy} type="file" accept=".pdf" multiple onChange={e=>{void upload(e.target.files);e.target.value="";}}/></label><small role="status">{message}</small>
  </div>;
}
export default {apiVersion:1,views:{region:Region,thumbnail:Thumbnail,reader:Reader}} satisfies FrontendPlugin;
