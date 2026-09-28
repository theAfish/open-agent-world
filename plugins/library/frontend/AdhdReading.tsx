import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { version as pdfjsVersion, type PDFDocumentProxy } from "pdfjs-dist";
import { assemblePageText, mapScoredTokens } from "./scoreMapping";
import { measureTextRange } from "./textGeometry";

type Mapping = ReturnType<typeof mapScoredTokens>;
export type ReadingScores = {page:number;mapping:Mapping;cached:boolean;result:{model:{model_id?:string;repository?:string;revision?:string;language_scope?:unknown};score_seconds:number;token_count:number;scored_count:number}};
type ScoreState = {status:"off"|"loading"|"ready"|"failed"|"empty";data?:ReadingScores;progress?:{scored_tokens:number;total_tokens:number};error?:string};
const parserVersion=`pdfjs-text-items-v1/${pdfjsVersion}/items-newline-v1`;
async function json(response:Response) { const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail??data));return data; }

export function useReadingScores(pdf:PDFDocumentProxy|undefined,paperId:string,digest:string|undefined,page:number,enabled:boolean) {
  const [state,setState]=useState<ScoreState>({status:"off"});
  const cache=useRef(new Map<string,ReadingScores>());
  useEffect(()=>{cache.current.clear();},[pdf,digest]);
  useEffect(()=>{
    if(!enabled||!pdf){setState({status:"off"});return;}
    if(!digest){setState({status:"failed",error:t("此文档尚无版本标识，请重新打开论文。")});return;}
    const key=`${digest}:${page}`,cached=cache.current.get(key);
    if(cached){setState({status:"ready",data:{...cached,cached:true}});return;}
    let disposed=false,jobId:string|undefined,timer:ReturnType<typeof setTimeout>;
    const abort=new AbortController(),root=`/api/library/papers/${encodeURIComponent(paperId)}/reading-scores`;
    setState({status:"loading"});
    const cancel=()=>{if(jobId)void fetch(`${root}/${jobId}`,{method:"DELETE"}).catch(()=>{});};
    const run=async()=>{
      try {
        const content=await(await pdf.getPage(page)).getTextContent();
        const assembled=assemblePageText(content.items);
        if(disposed)return;
        if(!assembled.text.trim()){setState({status:"empty"});return;}
        const capability=await fetch("/api/library/reading-scorer",{signal:abort.signal}).then(json);
        if(!capability.configured)throw new Error(t("本地原文评分器不可用；未生成热力图。"));
        // Keep the creation response so an in-flight toggle can cancel its actual job.
        const job=await fetch(root,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({document_sha256:digest,page,text:assembled.text,text_parser_version:parserVersion,context_tokens:256,stride:128})}).then(json);
        jobId=job.id;
        if(disposed){cancel();return;}
        const poll=async()=>{
          try {
            const current=await fetch(`${root}/${jobId}`,{signal:abort.signal}).then(json);
            if(disposed)return;
            if(current.status==="complete") {
              const data:ReadingScores={page,mapping:mapScoredTokens(current.result.tokens,assembled),result:current.result,cached:current.cached};
              if(cache.current.size>=8)cache.current.delete(cache.current.keys().next().value!);
              cache.current.set(key,data);setState({status:"ready",data});
            } else if(current.status==="failed"||current.status==="cancelled")setState({status:"failed",error:current.error||t("本次原文评分已取消")});
            else {setState({status:"loading",progress:current.progress});timer=setTimeout(()=>void poll(),350);}
          } catch(error) {if(!disposed)setState({status:"failed",error:String(error)});}
        };
        await poll();
      }catch(error){if(!disposed)setState({status:"failed",error:String(error)});}
    };
    timer=setTimeout(()=>void run(),250);
    return()=>{disposed=true;clearTimeout(timer);abort.abort();cancel();};
  },[pdf,paperId,digest,page,enabled]);
  return state;
}

export function AdhdStatus({state,intensity,setIntensity}:{state:ScoreState;intensity:number;setIntensity:(value:number)=>void}) {
  useLocale();
  if(state.status==="off")return null;
  return <div className="library-adhd-status" role="status">
    {state.status==="loading"&&<span>{t("正在本地评分原文…")}{state.progress?` ${state.progress.scored_tokens}/${state.progress.total_tokens}`:""}</span>}
    {state.status==="failed"&&<span>{state.error}</span>}
    {state.status==="empty"&&<span>{t("本页无可定位文字；扫描件需要 OCR。")}</span>}
    {state.status==="ready"&&<><span className="library-adhd-gradient" aria-hidden="true"/><span>{t("低 → 高惊讶度")} · 0–16+ bits</span>
      <label>{t("强度")}<input aria-label={t("ADHD 颜色强度")} type="range" min="0.08" max="0.35" step="0.01" value={intensity} onChange={event=>setIntensity(Number(event.target.value))}/></label>
      <small>{state.data?.cached?t("已使用本地缓存"):t("仅在本机计算")}</small>
      <details><summary>{t("评分说明")}</summary><div>
        <p>{t("颜色表示本地语言模型对原文词元的惊讶度，不表示论文重点或证据强度。")}</p>
        <p>SmolLM2-135M · CPU · {t("英语模型；中文效果尚未验收")}</p>
        <p>{t("上下文 256 词元，步长 128；每页首词元无前文，不评分。")}</p>
        <p>{t("已映射")} {state.data?.mapping.stats.mapped} · {t("未映射")} {state.data?.mapping.stats.unmapped} · {state.data?.result.score_seconds.toFixed(2)} s</p>
      </div></details></>}
  </div>;
}

/** Visual-only canvas; PDF text, selection, copying and citation targets stay native. */
export function SurprisalOverlay({data,intensity}:{data:ReadingScores;intensity:number}) {
  const canvas=useRef<HTMLCanvasElement>(null);
  useLayoutEffect(()=>{
    const element=canvas.current,page=element?.parentElement,textLayer=page?.querySelector<HTMLElement>(".textLayer");
    if(!element||!page||!textLayer)return;
    let frame=0,disposed=false;
    const draw=()=>{
      frame=0;if(disposed)return;
      const width=page.clientWidth,height=page.clientHeight,dpr=Math.min(devicePixelRatio||1,2);
      element.width=Math.ceil(width*dpr);element.height=Math.ceil(height*dpr);element.style.width=`${width}px`;element.style.height=`${height}px`;
      const context=element.getContext("2d");if(!context)return;context.scale(dpr,dpr);
      const start=performance.now();let mapped=0;
      for(const source of data.mapping.ranges) {
        const amount=Math.max(0,Math.min(1,source.bits/16));
        // Blue -> amber, continuous and fixed across pages (no per-page rank colors).
        const red=Math.round(67+171*amount),green=Math.round(144+20*amount),blue=Math.round(199-135*amount);
        context.fillStyle=`rgba(${red},${green},${blue},${intensity})`;
        for(const rect of measureTextRange(source,page,textLayer)) {context.fillRect(rect.x*width,rect.y*height,rect.width*width,rect.height*height);mapped++;}
      }
      element.dataset.glyphRects=String(mapped);
      element.dataset.measureMs=(performance.now()-start).toFixed(2);
    };
    const schedule=()=>{if(!disposed&&!frame)frame=requestAnimationFrame(draw);};
    const mutations=new MutationObserver(schedule);mutations.observe(textLayer,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:["style","data-text-item-index"]});
    const resize=new ResizeObserver(schedule);resize.observe(page);resize.observe(textLayer);
    window.addEventListener("resize",schedule);document.fonts?.addEventListener("loadingdone",schedule);draw();
    return()=>{disposed=true;cancelAnimationFrame(frame);mutations.disconnect();resize.disconnect();window.removeEventListener("resize",schedule);document.fonts?.removeEventListener("loadingdone",schedule);};
  },[data,intensity]);
  return <canvas ref={canvas} className="library-surprisal-overlay" aria-hidden="true" data-page={data.page}/>;
}
