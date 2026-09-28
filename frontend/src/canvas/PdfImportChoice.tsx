import {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {t} from "../i18n";
import type {PdfImportTarget} from "./importPdf";
import "./PdfImportChoice.css";

export type IntakePaper = {id:string;title:string;doi?:string;has_pdf:boolean;parent_id?:string};
export type IntakeScope = {id:string;title:string;paper_ids:string[];configured:boolean};
export type IntakeOptions = {papers:IntakePaper[];scopes:IntakeScope[]};
export async function readIntakeOptions(doi?:string):Promise<IntakeOptions> {
  const response=await fetch(`/api/literature/papers/intake-options${doi?`?doi=${encodeURIComponent(doi)}`:""}`);
  if(!response.ok)throw new Error(await response.text());
  return response.json();
}

export function PdfImportChoice({file,targetPaperId,onDone}:{file:File;targetPaperId?:string;onDone:(target?:PdfImportTarget)=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [papers,setPapers]=useState<IntakePaper[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(true);
  const [paperId,setPaperId]=useState(targetPaperId??""),[query,setQuery]=useState(""),[kind,setKind]=useState<"main"|"supplement">("main");
  useEffect(()=>{dialog.current?.showModal();return()=>dialog.current?.close();},[]);
  useEffect(()=>{let active=true;void readIntakeOptions().then(result=>{if(active)setPapers(result.papers);}).catch(error=>{if(active)setError(String(error));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[]);
  const selected=papers.find(paper=>paper.id===paperId);
  const choices=papers.filter(paper=>paper.id===targetPaperId||!paper.has_pdf).filter(paper=>`${paper.title} ${paper.doi??""}`.toLowerCase().includes(query.toLowerCase()));
  return <dialog ref={dialog} className="pdf-import-choice" onCancel={event=>{event.preventDefault();onDone();}} aria-label={t("关联 PDF 与论文")}>
    <form onSubmit={event=>{event.preventDefault();onDone({paperId:paperId||undefined,kind:paperId?kind:"main"});}}>
      <header><strong>{t("关联 PDF 与论文")}</strong><button type="button" onClick={()=>onDone()} aria-label={t("关闭")}>×</button></header>
      <p>{file.name}</p>
      {!targetPaperId&&<><label>{t("查找已有题录")}<input value={query} onChange={event=>{setQuery(event.target.value);setPaperId("");setKind("main");}} placeholder={t("标题或 DOI")}/></label>
        <label>{t("导入到")}<select value={paperId} disabled={loading} onChange={event=>{setPaperId(event.target.value);setKind("main");}}><option value="">{t("新建 Paper")}</option>{choices.map(paper=><option key={paper.id} value={paper.id}>{paper.title}{paper.doi?` · ${paper.doi}`:""}</option>)}</select></label>
        <small>{t("已有题录需手动选择；相似文件名不会自动合并。")}</small></>}
      {selected&&<><p>{t("所属论文")}：<strong>{selected.title}</strong></p><label>{t("文件用途")}<select value={kind} onChange={event=>setKind(event.target.value as "main"|"supplement")}><option value="main">{selected.has_pdf?t("主文新版本（保留旧版与批注）"):t("主文 PDF")}</option><option value="supplement">{t("补充材料 SI（独立保存）")}</option></select></label></>}
      {loading&&<p role="status">{t("正在读取…")}</p>}{error&&<p role="alert">{error}</p>}
      <footer><button type="button" onClick={()=>onDone()}>{t("取消")}</button><button disabled={loading||!!error||!!targetPaperId&&!selected}>{t("确认导入")}</button></footer>
    </form>
  </dialog>;
}

/** Explicit choice before writes; cancellation never allocates a Paper. */
export function choosePdfImport(file:File,options:{targetPaperId?:string}={}):Promise<PdfImportTarget|undefined> {
  return new Promise(resolve=>{
    const host=document.createElement("div");document.body.appendChild(host);const root=createRoot(host);
    const finish=(target?:PdfImportTarget)=>{resolve(target);queueMicrotask(()=>{root.unmount();host.remove();});};
    root.render(<PdfImportChoice file={file} targetPaperId={options.targetPaperId} onDone={finish}/>);
  });
}
