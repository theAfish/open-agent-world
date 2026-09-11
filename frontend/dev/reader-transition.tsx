// Development-only harness. No database writes; all reader saves are intercepted
// only in automated tests. Interactive preview uses the actual selected Paper.
import {useEffect,useRef,useState} from "react";
import {createRoot} from "react-dom/client";
import type {PluginViewProps} from "../src/plugins/sdk";
import {ActiveReader} from "../../plugins/library/frontend";
import {diffusionMask} from "../../plugins/library/frontend/readerTransition";
import "./reader-transition.css";

function Preview(){
  const [open,setOpen]=useState(false),[attempt,setAttempt]=useState(0);
  const [papers,setPapers]=useState<PluginViewProps["card"][]>([]);
  const [paper,setPaper]=useState(new URLSearchParams(location.search).get("paper")??"");
  const [calibrate,setCalibrate]=useState(false),[mix,setMix]=useState(0),[coverage,setCoverage]=useState(.26);
  const dialog=useRef<HTMLDialogElement>(null);
  const [metrics,setMetrics]=useState<number[]>([]);
  const [decode,setDecode]=useState<unknown>();
  useEffect(()=>{if(!open)return;const observer=new PerformanceObserver(list=>setMetrics(values=>[...values,...list.getEntries().map(e=>e.duration)].slice(-40)));observer.observe({type:"longtask"});return()=>observer.disconnect();},[open]);
  useEffect(()=>{void fetch("/api/world").then(r=>r.json()).then(w=>{const p=w.nodes.filter((c:PluginViewProps["card"])=>c.type==="library.paper");setPapers(p);setPaper(id=>id||p[0]?.id||"");});},[]);
  useEffect(()=>{if(calibrate)dialog.current?.showModal();else dialog.current?.close();},[calibrate]);
  const card=papers.find(c=>c.id===paper);
  return <><header><h1>阅读器过渡验收</h1><select aria-label="论文" value={paper} onChange={e=>setPaper(e.target.value)}>{papers.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><button onClick={()=>setOpen(true)} disabled={!card}>打开阅读器</button><button onClick={()=>setCalibrate(true)}>像素混合校准</button><small>同一阅读器组件 · 真实 PDF.js 渲染 · 无复制 PDF</small></header>
    <details><summary>本次预览打开期间的主线程长任务（ms）</summary><output>{JSON.stringify({longTasksMs:metrics,decode})}</output></details>
    <main>{Array.from({length:18},(_,i)=><article key={i}><h2>Paper {i+1} · 清晰文字对照</h2><p>Diffusion, structure and ionic transport.</p><div className="test-pattern"/><p>原界面真实像素 / 未覆盖区域保持清晰</p></article>)}</main>
    {open&&card&&<ActiveReader key={attempt} {...{card,level:"inspector"} as PluginViewProps} onClose={()=>{setDecode(performance.getEntriesByName("oaw:pdf:decode").map(e=>({duration:e.duration,detail:(e as PerformanceMeasure).detail})));setOpen(false);}} onRetry={()=>setAttempt(v=>v+1)}/>}
    <dialog ref={dialog} className="calibration-dialog" onCancel={()=>setCalibrate(false)}>
      <div className="library-reader-glass" style={{backdropFilter:"blur(16px)",opacity:mix,maskImage:diffusionMask(coverage,innerWidth,innerHeight)}}/>
      <nav><label>混合比例<input aria-label="混合比例" type="number" step=".25" min="0" max="1" value={mix} onChange={e=>setMix(Number(e.target.value))}/></label><label>覆盖范围<input aria-label="覆盖范围" type="number" step=".05" min="0" max="1" value={coverage} onChange={e=>setCoverage(Number(e.target.value))}/></label><button onClick={()=>setCalibrate(false)}>关闭校准</button></nav>
    </dialog>
  </>;
}
createRoot(document.getElementById("root")!).render(<Preview/>);
