import { t, useLocale } from "@oaw/plugin-api";
import type { PluginViewProps } from "@oaw/plugin-api";
import { useEffect, useMemo, useRef, useState } from "react";
import { useWorldStore } from "../../../frontend/src/state/worldStore";
import { ActiveReader } from "./index";
import type { Loc } from "./ExtractionPanel";

type Paper = {paper:string;name:string;title:string|null;authors:string[];year:number|null;venue:string|null;doi:string|null;pages:number;status:string;version:string|null};
type Catalog = {total:number;papers:Paper[];status_counts:Record<string,number>};
type Hit = {paper:string;paper_name:string;title:string|null;year:number|null;kind:string;heading:string;page:number|null;bbox:number[]|null;path:string|null;snippet:string;cite:string};
const MAX_PDF = 25*1024*1024;

async function request(path:string, init?:RequestInit) {
  const response = await fetch(`/api/${path}`, init);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
const post = (path:string, body:unknown) => request(path, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
function encoded(file:File):Promise<string> {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(reader.error);reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.readAsDataURL(file);});
}

/** Create one Paper per PDF inside the library and import it; an import failure removes its empty Paper. */
export async function importPapers(libraryId:string, origin:{x:number;y:number}, files:File[], progress:(message:string)=>void) {
  let imported = 0;
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith(".pdf")) continue;
    if (file.size > MAX_PDF) { progress(t("{name} is larger than 25 MiB", {name:file.name})); continue; }
    progress(t("Importing {name}", {name:file.name}));
    const node = await post("nodes", {type:"library.paper",name:file.name.replace(/\.pdf$/i,"").slice(0,200),parent_id:libraryId,
      position:{x:origin.x+40+(imported%4)*320,y:origin.y+170+Math.floor(imported/4)*240}});
    try { await post(`nodes/${node.id}/resource/import`, {arguments:{filename:file.name,pdf:await encoded(file)}}); }
    catch (error) { await fetch(`/api/nodes/${node.id}`,{method:"DELETE"}); throw error; }
    imported++;
  }
  progress(imported ? t("Imported {count} PDFs", {count:imported}) : t("Choose PDF files"));
  return imported;
}

function useMembers(libraryId:string) {
  const cards = useWorldStore(s=>s.cards);
  const members = useMemo(()=>cards.filter(c=>c.parent_id===libraryId&&c.type==="library.paper"),[cards,libraryId]);
  // Re-read the catalog when membership, names or Papers change.
  const key = members.map(m=>`${m.id}:${m.name}:${m.updated_at}`).join("|");
  return {members, key};
}

function ImportButton({card,onDone,setMessage}:{card:PluginViewProps["card"];onDone:()=>void;setMessage:(m:string)=>void}) {
  const [busy,setBusy]=useState(false);
  return <label className="library-import-button">{busy?t("Importing…"):t("Import PDFs")}
    <input disabled={busy} type="file" accept=".pdf" multiple onChange={e=>{const files=Array.from(e.target.files??[]);e.target.value="";setBusy(true);
      void importPapers(card.id,card.position,files,setMessage).then(onDone).catch(err=>setMessage(String(err))).finally(()=>setBusy(false));}}/></label>;
}

/** Body while member cards are shown on the canvas: counts, import and a pointer to the catalog. */
export function LibraryBody({card}:PluginViewProps) {
  useLocale();
  const {members,key}=useMembers(card.id);
  const [counts,setCounts]=useState<Record<string,number>>({});
  const [message,setMessage]=useState("");
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{let active=true;void post(`nodes/${card.id}/resource/catalog`,{arguments:{limit:1}})
    .then((c:Catalog)=>{if(active)setCounts(c.status_counts);}).catch(()=>{});return()=>{active=false;};},[card.id,key,refresh]);
  return <div className="library-collection-bar nodrag nopan">
    <span><strong>{members.length}</strong> {t("papers")}</span>
    <span title={t("Structured by GROBID")}>{t("{count} structured", {count:counts.structured??0})}</span>
    {!!counts.extracting&&<span>{t("{count} extracting", {count:counts.extracting})}</span>}
    <ImportButton card={card} onDone={()=>setRefresh(n=>n+1)} setMessage={setMessage}/>
    <small role="status">{message||t("Drag Paper cards in, or drop PDFs on the library. Open the workspace to search.")}</small>
  </div>;
}

function authors(list:string[]) {
  return list.length>3?`${list.slice(0,3).join(", ")} et al.`:list.join(", ");
}

function Snippet({text}:{text:string}) {
  // The index marks matches with [ ]; render them without trusting any markup.
  return <>{text.split(/(\[[^\]]*\])/).map((part,i)=>part.startsWith("[")&&part.endsWith("]")?<mark key={i}>{part.slice(1,-1)}</mark>:part)}</>;
}

/** Workspace: the library's catalog and full-text search. */
export function LibraryCatalog(props:PluginViewProps) {
  useLocale();
  const {card,host}=props;
  const {members,key}=useMembers(card.id);
  const [catalog,setCatalog]=useState<Catalog>();
  const [filter,setFilter]=useState("");
  const [query,setQuery]=useState("");
  const [years,setYears]=useState<{from:string;to:string}>({from:"",to:""});
  const [hits,setHits]=useState<Hit[]|null>(null);
  const [message,setMessage]=useState("");
  const [refresh,setRefresh]=useState(0);
  const [reading,setReading]=useState<{id:string;focus?:Loc&{key:number}}>();
  const [attempt,setAttempt]=useState(0);
  const dropRef=useRef<HTMLDivElement>(null);
  const yearArgs = useMemo(()=>{const args:Record<string,number>={};const from=Number(years.from),to=Number(years.to);
    if(years.from&&from>=1000&&from<=3000)args.year_from=from;if(years.to&&to>=1000&&to<=3000)args.year_to=to;return args;},[years]);

  useEffect(()=>{let active=true;const handle=setTimeout(()=>{
    void host.resourceAction("catalog",{text:filter,limit:500,...yearArgs}).then(value=>{if(active){setCatalog(value as Catalog);setMessage("");}})
      .catch(err=>{if(active)setMessage(String(err));});},filter?200:0);
    return()=>{active=false;clearTimeout(handle);};},[card.id,key,refresh,filter,yearArgs,host]);

  async function search(event?:React.FormEvent) {
    event?.preventDefault();
    if(!query.trim()){setHits(null);return;}
    try { setHits(((await host.resourceAction("search",{query,limit:20,...yearArgs})) as {hits:Hit[]}).hits); setMessage(""); }
    catch(err) { setMessage(String(err)); }
  }
  async function remove(paper:Paper) {
    // Moving a Paper out of the library revokes the library's grant on it; its own connections stay.
    const x=card.position.x+card.size.width+40, y=card.position.y+members.findIndex(m=>m.id===paper.paper)*30;
    try { await request(`nodes/${paper.paper}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({parent_id:null,position:{x,y}})}); }
    catch(err) { setMessage(String(err)); }
  }
  useEffect(()=>{
    const zone=dropRef.current; if(!zone)return;
    const over=(e:DragEvent)=>{if(e.dataTransfer?.types.includes("Files")){e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect="copy";}};
    const drop=(e:DragEvent)=>{if(e.dataTransfer?.files.length){e.preventDefault();e.stopPropagation();
      void importPapers(card.id,card.position,Array.from(e.dataTransfer.files),setMessage).then(()=>setRefresh(n=>n+1)).catch(err=>setMessage(String(err)));}};
    zone.addEventListener("dragover",over);zone.addEventListener("drop",drop);
    return()=>{zone.removeEventListener("dragover",over);zone.removeEventListener("drop",drop);};
  },[card.id,card.position]);

  const readingCard = reading && members.find(m=>m.id===reading.id);
  return <div ref={dropRef} className="library-catalog nodrag nopan nowheel" onWheel={e=>e.stopPropagation()}>
    <form className="library-catalog-search" onSubmit={search} role="search">
      <input type="search" aria-label={t("Search the full text of every paper")} placeholder={t("Search the full text of every paper")} value={query} onChange={e=>{setQuery(e.target.value);if(!e.target.value)setHits(null);}}/>
      <input aria-label={t("From year")} placeholder={t("From year")} inputMode="numeric" value={years.from} onChange={e=>setYears(y=>({...y,from:e.target.value}))}/>
      <input aria-label={t("To year")} placeholder={t("To year")} inputMode="numeric" value={years.to} onChange={e=>setYears(y=>({...y,to:e.target.value}))}/>
      <button type="submit">{t("Search")}</button>
      <ImportButton card={card} onDone={()=>setRefresh(n=>n+1)} setMessage={setMessage}/>
    </form>
    {message&&<p className="library-catalog-message" role="status">{message}</p>}
    {hits&&<section aria-label={t("Search results")} className="library-hits">
      <header><strong>{t("{count} passages", {count:hits.length})}</strong><button type="button" onClick={()=>setHits(null)}>{t("Back to catalog")}</button></header>
      {hits.length?hits.map((hit,i)=><article key={`${hit.cite}-${i}`}>
        <button type="button" className="library-hit-source" onClick={()=>setReading({id:hit.paper,focus:hit.page?{page:hit.page,bbox:hit.bbox,key:Date.now()}:undefined})}>
          {hit.title||hit.paper_name}{hit.year?` · ${hit.year}`:""} · {hit.heading}{hit.page?` · ${t("Page {page}",{page:hit.page})}`:""}</button>
        <p><Snippet text={hit.snippet}/></p>
      </article>):<p>{t("No passage matches. Try other words.")}</p>}
    </section>}
    {!hits&&<section aria-label={t("Papers in this library")} className="library-catalog-list">
      <header><input type="search" aria-label={t("Filter by title, author, venue or DOI")} placeholder={t("Filter by title, author, venue or DOI")} value={filter} onChange={e=>setFilter(e.target.value)}/>
        <small>{t("{shown} of {total} papers", {shown:catalog?.papers.length??0,total:members.length})}</small></header>
      <table><thead><tr><th>{t("Title")}</th><th>{t("Authors")}</th><th>{t("Year")}</th><th>{t("Status")}</th><th/></tr></thead>
        <tbody>{catalog?.papers.map(paper=><tr key={paper.paper}>
          <td><button type="button" className="library-paper-link" onClick={()=>setReading({id:paper.paper})}>{paper.title||paper.name}</button>{paper.venue&&<small>{paper.venue}</small>}</td>
          <td>{authors(paper.authors)}</td><td>{paper.year??""}</td>
          <td><span className={`library-status is-${paper.status}`}>{t(`paper-status:${paper.status}`)}</span></td>
          <td><button type="button" onClick={()=>void remove(paper)} title={t("Move this paper out of the library")}>{t("Move out")}</button></td>
        </tr>)}</tbody></table>
      {!members.length&&<p className="library-catalog-empty">{t("Drop PDFs here or drag Paper cards onto the library.")}</p>}
    </section>}
    {readingCard&&<ActiveReader key={`${readingCard.id}-${attempt}`} {...props} card={readingCard} focus={reading?.focus}
      onRetry={()=>setAttempt(n=>n+1)} onClose={()=>setReading(undefined)}/>}
  </div>;
}
