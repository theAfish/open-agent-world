import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useState } from "react";

type Version = {sha256:string;size_bytes:number;created_at:string|null;current:boolean};
type Snapshot = {current:boolean;value:{filename?:string;pages?:number;page?:number;notes?:string;annotations?:{id:string;page:number;text?:string;comment?:string;translation?:string;image?:string}[]}};
async function read(url:string) { const response=await fetch(url); if(!response.ok)throw new Error(await response.text());return response.json(); }

/** Archived reading state is read-only; the active Paper keeps its stable ID. */
export function PaperHistory({paperId,onClose,onImport}:{paperId:string;onClose:()=>void;onImport:(file:File)=>Promise<void>}) {
  useLocale();
  const [versions,setVersions]=useState<Version[]>([]),[selected,setSelected]=useState("");
  const [snapshot,setSnapshot]=useState<Snapshot>(),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const root=`/api/nodes/${encodeURIComponent(paperId)}/document/binaries/pdf`;
  useEffect(()=>{const controller=new AbortController();void fetch(root,{signal:controller.signal}).then(async response=>{if(!response.ok)throw new Error(await response.text());return response.json();}).then(result=>{setVersions(result.items);setSelected(result.current??"");}).catch(error=>{if(!controller.signal.aborted)setError(String(error));});return()=>controller.abort();},[root]);
  useEffect(()=>{let active=true;setSnapshot(undefined);if(selected)void read(`${root}/${selected}/snapshot`).then(value=>{if(active)setSnapshot(value);}).catch(error=>{if(active)setError(String(error));});return()=>{active=false;};},[root,selected]);
  useEffect(()=>{const escape=(event:KeyboardEvent)=>{if(event.key==="Escape"){event.preventDefault();event.stopImmediatePropagation();onClose();}};window.addEventListener("keydown",escape,true);return()=>window.removeEventListener("keydown",escape,true);},[onClose]);
  return <div className="library-edit-backdrop library-history-backdrop" onClick={onClose}>
    <section className="library-history" role="dialog" aria-modal="true" aria-label={t("文件版本与阅读记录")} onClick={event=>event.stopPropagation()}>
      <header><h2>{t("文件版本与阅读记录")}</h2><button onClick={onClose} aria-label={t("关闭")}>×</button></header>
      <p>{t("替换 PDF 会保留旧文件及其阅读记录，新版本使用独立批注。")}</p>
      <label className="library-history-import">{busy?t("正在导入…"):t("导入 PDF 新版本")}<input type="file" accept=".pdf,application/pdf" disabled={busy} onChange={async event=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;if(file.size>25*1024*1024){setError(t("每个 PDF 最大 25 MiB"));return;}setBusy(true);setError("");try{await onImport(file);}catch(error){setError(String(error));}finally{setBusy(false);}}}/></label>
      {error&&<p role="alert">{error}</p>}
      <div className="library-history-grid"><nav aria-label={t("文件版本")}>{[...versions].reverse().map(version=><button key={version.sha256} aria-pressed={version.sha256===selected} onClick={()=>{setError("");setSelected(version.sha256);}}><strong>{version.sha256.slice(0,12)}{version.current?` · ${t("当前")}`:""}</strong><small>{(version.size_bytes/1024/1024).toFixed(2)} MiB · {version.created_at?new Date(version.created_at).toLocaleString():t("导入时间未知")}</small></button>)}</nav>
      <article>{snapshot&&<><h3>{snapshot.value.filename}</h3><a href={`${root}/${selected}`} download={snapshot.value.filename||"paper.pdf"}>{t("下载此版本 PDF")}</a><p>{snapshot.value.pages} {t("页")} · {t("阅读位置")} {snapshot.value.page}</p>
        <h4>{t("笔记")}</h4><p className="library-history-text">{snapshot.value.notes||t("暂无笔记")}</p>
        <h4>{t("批注")}</h4>{snapshot.value.annotations?.map(item=><blockquote key={item.id}><small>{t("Page {page}",{page:item.page})}</small>{item.image&&<img src={item.image} alt={t("截图批注")}/>}<p>{item.text}</p>{item.translation&&<p>{item.translation}</p>}{item.comment&&<p>{item.comment}</p>}</blockquote>)}</>}
      </article></div>
    </section>
  </div>;
}
