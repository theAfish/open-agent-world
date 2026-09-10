import { useEffect, useRef, useState, type HTMLAttributes } from "react";
import { TextLayer, type PDFDocumentProxy } from "pdfjs-dist";

export function PdfPageSurface({pdf,number,scale,children,...props}:HTMLAttributes<HTMLDivElement>&{pdf:PDFDocumentProxy;number:number;scale:number}) {
  const host=useRef<HTMLDivElement>(null),canvas=useRef<HTMLCanvasElement>(null),layer=useRef<HTMLDivElement>(null);
  const [near,setNear]=useState(false);
  const [size,setSize]=useState({width:600,height:800});
  const [error,setError]=useState("");
  useEffect(()=>{const el=host.current;if(!el)return;const observer=new IntersectionObserver(entries=>setNear(entries[0].isIntersecting),{root:el.closest(".library-page-scroll"),rootMargin:"800px"});observer.observe(el);return()=>observer.disconnect();},[]);
  useEffect(()=>{
    let active=true;let render:ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]>|undefined;let text:TextLayer|undefined;
    void pdf.getPage(number).then(async page=>{
      if(!active)return;const viewport=page.getViewport({scale});setSize({width:viewport.width,height:viewport.height});
      if(!near||!canvas.current||!layer.current)return;
      const ratio=Math.min(devicePixelRatio||1,2),container=layer.current;
      canvas.current.width=Math.floor(viewport.width*ratio);canvas.current.height=Math.floor(viewport.height*ratio);
      container.replaceChildren();container.style.setProperty("--scale-factor",String(scale));container.style.setProperty("--total-scale-factor",String(scale*page.userUnit));
      render=page.render({canvas:canvas.current,viewport,transform:[ratio,0,0,ratio,0,0]});
      text=new TextLayer({textContentSource:page.streamTextContent(),container,viewport});await Promise.all([render.promise,text.render()]);
    }).catch(e=>{if(active)setError(String(e));});
    return()=>{active=false;render?.cancel();text?.cancel();};
  },[pdf,number,scale,near]);
  return <div {...props} ref={host} data-pdf-page={number} className="library-pdf-page" style={size}>
    {near?<><canvas ref={canvas} style={size}/><div ref={layer} className="textLayer"/>{children}</>:<small>第 {number} 页</small>}
    {error&&<p role="alert">{error}</p>}
  </div>;
}
