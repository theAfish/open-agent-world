import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Extraction } from "./paperCache";

export type Loc = {page:number;bbox:number[]|null};
type Manifest = {filename:string;extractions:Extraction[];active:string|null;extracting:boolean;extraction_error?:{message:string;at:string}|null};
type Located = {path:string;loc:Loc|null};
type Overview = {version:string;metadata:{title:string|null;authors:{name:string}[];identifiers:{doi:string|null}};
  sections:(Located&{number:string|null;heading:string;level:number})[];figures:(Located&{label:string})[];tables:(Located&{label:string})[];
  references:number;warnings:string[]};

async function resource<T>(id:string, action:string, args:Record<string,unknown>={}):Promise<T> {
  const response=await fetch(`/api/nodes/${id}/resource/${action}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({arguments:args})});
  if(!response.ok)throw new Error(await response.text());
  return response.json();
}

const SOURCE: Record<Extraction["source"],string> = {grobid:"GROBID", agent:"Agent", user:"Manual"};

/** Versioned structured content of a Paper, produced by GROBID in the background. Agents revise through the Curate structure connection. */
export function ExtractionPanel({paperId,name,active,onChanged,onLocate}:{paperId:string;name:string;active:string|null;onChanged:()=>void;onLocate:(loc:Loc)=>void}) {
  useLocale();
  const [manifest,setManifest]=useState<Manifest>();
  const [overview,setOverview]=useState<Overview>();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [reload,setReload]=useState(0);
  useEffect(()=>{let live=true;
    void resource<Manifest>(paperId,"manifest")
      .then(async m=>[m,m.active?await resource<Overview>(paperId,"structure"):undefined] as const)
      .then(([m,o])=>{if(live){setManifest(m);setOverview(o);setError("");}})
      .catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[paperId,active,reload]);
  // GROBID runs after import returns; poll until the job settles, then let the card refresh.
  const extracting=manifest?.extracting??false;
  useEffect(()=>{if(!extracting)return;const timer=setTimeout(()=>setReload(n=>n+1),2500);return()=>clearTimeout(timer);},[extracting,manifest]);
  const wasExtracting=useRef(false);
  useEffect(()=>{if(wasExtracting.current&&!extracting)onChanged();wasExtracting.current=extracting;},[extracting,onChanged]);
  async function run(action:string,args:Record<string,unknown>){
    setBusy(true);
    try{await resource(paperId,action,args);setReload(n=>n+1);onChanged();}catch(e){setError(String(e));}finally{setBusy(false);}
  }
  const current=manifest?.extractions?.find(item=>item.id===manifest.active);
  return <section className="library-extraction nowheel" aria-label={t("Structured extraction")} onWheel={e=>e.stopPropagation()}>
    <header>
      <h4>{t("Structured extraction")}</h4>
      <select aria-label={t("Extraction version")} disabled={busy||!manifest||extracting} value={manifest?.active??""} onChange={e=>void run("activate",{version:e.target.value})}>
        {(manifest?.extractions??[]).slice().reverse().map(item=><option key={item.id} value={item.id}>
          {item.id} · {t(SOURCE[item.source])} · {item.extractor}</option>)}
      </select>
      <button type="button" disabled={busy||extracting} onClick={()=>void run("extract",{})}>{t("Re-extract")}</button>
      <button type="button" disabled={busy} onClick={()=>setReload(n=>n+1)}>{t("Refresh")}</button>
      {current&&<a href={`/api/nodes/${paperId}/files/${current.key}?download=${encodeURIComponent(`${name}.${current.id}.json`)}`}>{t("Download JSON")}</a>}
    </header>
    {error&&<p role="alert">{error}</p>}
    {extracting&&<p className="library-extraction-status" role="status">{t("GROBID is extracting the structure…")}</p>}
    {manifest?.extraction_error&&<p className="library-extraction-warning" role="status">
      {t(manifest.active?"The last extraction failed: {error}":"No structured extraction yet: {error}",{error:manifest.extraction_error.message})}</p>}
    {current?.note&&<p className="library-extraction-note">{current.note}</p>}
    {overview?.metadata&&<>
      <dl>
        <dt>{t("Title")}</dt><dd>{overview.metadata.title??"—"}</dd>
        <dt>{t("Authors")}</dt><dd>{overview.metadata.authors.map(author=>author.name).join(", ")||"—"}</dd>
        <dt>DOI</dt><dd>{overview.metadata.identifiers.doi??"—"}</dd>
        <dt>{t("Contents")}</dt><dd>{t("{sections} sections · {figures} figures · {tables} tables · {references} references",{
          sections:String(overview.sections.length),figures:String(overview.figures.length),tables:String(overview.tables.length),references:String(overview.references)})}</dd>
      </dl>
      {overview.warnings.map(warning=><p key={warning} className="library-extraction-warning">{warning}</p>)}
      <ol className="library-extraction-outline">{overview.sections.map(section=><li key={section.path} style={{paddingLeft:(section.level-1)*12}}>
        <Target loc={section.loc} onLocate={onLocate}>{section.number?`${section.number} `:""}{section.heading||t("(untitled)")}</Target></li>)}</ol>
      {[...overview.figures,...overview.tables].some(item=>item.loc)&&<p className="library-extraction-targets">
        {[...overview.figures,...overview.tables].filter(item=>item.loc).map(item=><Target key={item.path} loc={item.loc} onLocate={onLocate}>{item.label||item.path}</Target>)}</p>}
    </>}
  </section>;
}

/** Opens the reader at an element's page and marks its GROBID bounding box. */
function Target({loc,onLocate,children}:{loc:Loc|null;onLocate:(loc:Loc)=>void;children:ReactNode}) {
  if(!loc)return <span>{children}</span>;
  return <button type="button" className="library-extraction-target" title={t("Page {page}",{page:loc.page})} onClick={()=>onLocate(loc)}>{children}</button>;
}
