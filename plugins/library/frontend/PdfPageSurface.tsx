import { t, useLocale } from "@oaw/plugin-api";
import { useEffect, useRef, useState, type HTMLAttributes } from "react";
import { TextLayer, type PDFDocumentProxy } from "pdfjs-dist";

export function PdfPageSurface({pdf,number,scale,children,onReady,onPreparing,onLoadError,layoutVersion=0,...props}:HTMLAttributes<HTMLDivElement>&{pdf:PDFDocumentProxy;number:number;scale:number;layoutVersion?:number;onReady?:(scale:number)=>void;onPreparing?:()=>void;onLoadError?:(error:string)=>void}) {
  useLocale();
  const readyRef=useRef(onReady);readyRef.current=onReady;
  const preparingRef=useRef(onPreparing);preparingRef.current=onPreparing;
  const errorRef=useRef(onLoadError);errorRef.current=onLoadError;
  const [dpr,setDpr]=useState(()=>devicePixelRatio||1);
  useEffect(()=>{let query:MediaQueryList;const update=()=>{query?.removeEventListener("change",update);setDpr(devicePixelRatio||1);query=matchMedia(`(resolution: ${devicePixelRatio||1}dppx)`);query.addEventListener("change",update);};update();return()=>query.removeEventListener("change",update);},[]);
  const host=useRef<HTMLDivElement>(null),canvas=useRef<HTMLCanvasElement>(null),layer=useRef<HTMLDivElement>(null);
  const [near,setNear]=useState(false);
  const [size,setSize]=useState({width:600,height:800});
  const [error,setError]=useState("");
  useEffect(()=>{const el=host.current;if(!el)return;const observer=new IntersectionObserver(entries=>setNear(entries[0].isIntersecting),{root:el.closest(".library-page-scroll"),rootMargin:"800px"});observer.observe(el);return()=>observer.disconnect();},[]);
  useEffect(()=>{
    let active=true,frame1=0,frame2=0;let render:ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]>|undefined;let text:TextLayer|undefined;
    preparingRef.current?.();
    void pdf.getPage(number).then(async page=>{
      if(!active)return;const viewport=page.getViewport({scale});setSize({width:viewport.width,height:viewport.height});
      if(!near||!canvas.current||!layer.current)return;
      const ratio=Math.min(dpr,2),container=layer.current;
      canvas.current.width=Math.floor(viewport.width*ratio);canvas.current.height=Math.floor(viewport.height*ratio);
      container.replaceChildren();container.style.setProperty("--scale-factor",String(scale));container.style.setProperty("--total-scale-factor",String(scale*page.userUnit));
      render=page.render({canvas:canvas.current,viewport,transform:[ratio,0,0,ratio,0,0]});
      text=new TextLayer({textContentSource:page.streamTextContent(),container,viewport});await Promise.all([render.promise,text.render()]);
      // The canvas and selectable text must both be committed at this exact size.
      if(active)frame1=requestAnimationFrame(()=>{frame2=requestAnimationFrame(()=>{if(active)readyRef.current?.(scale);});});
    }).catch(e=>{if(active){setError(String(e));errorRef.current?.(String(e));}});
    return()=>{active=false;cancelAnimationFrame(frame1);cancelAnimationFrame(frame2);render?.cancel();text?.cancel();};
  },[pdf,number,scale,near,dpr,layoutVersion]);
  return <div {...props} ref={host} data-pdf-page={number} className="library-pdf-page" style={size}>
    {near?<><canvas ref={canvas} style={size}/><div ref={layer} className="textLayer"/>{children}</>:<small>{t("第")} {number} {t("页")}</small>}
    {error&&<p role="alert">{error}</p>}
  </div>;
}
