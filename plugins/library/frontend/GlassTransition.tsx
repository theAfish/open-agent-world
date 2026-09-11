import {useEffect,useRef,type RefObject} from "react";
import {blurMix,clamp,diffusionMask,READER_TRANSITION as config} from "./readerTransition";
import type {EntrancePhase} from "./useReaderEntrance";
export function ReaderTransition({phase,reduced,content,onComplete}:{phase:EntrancePhase;reduced:boolean;content:RefObject<HTMLDivElement|null>;onComplete:()=>void}){
  const glass=useRef<HTMLDivElement>(null),started=useRef<number|undefined>(undefined);
  const complete=useRef(onComplete);complete.current=onComplete;
  useEffect(()=>{
    const layer=glass.current,target=content.current;if(!layer||!target)return;
    const low=navigator.hardwareConcurrency>0&&navigator.hardwareConcurrency<=4;
    const supported=CSS.supports("backdrop-filter","blur(1px)")&&CSS.supports("mask-image","linear-gradient(#000,#000)");
    layer.style.backdropFilter=supported?`blur(${low?10:config.blurPx}px)`:"none";
    let frame=0,width=innerWidth,height=innerHeight;
    const resize=()=>{width=innerWidth;height=innerHeight;cancelAnimationFrame(frame);frame=requestAnimationFrame(tick);};window.addEventListener("resize",resize);
    const begin=performance.now();started.current??=begin;
    function tick(now:number){
      if(!layer||!target)return;
      if(phase==="concealing"||phase==="retracting"){
        const duration=phase==="concealing"?config.exitRevealMs:config.exitSpreadMs;
        const p=reduced||!supported?0:1-clamp((now-begin)/duration);
        const coverage=phase==="concealing"?1:p;
        layer.style.maskImage=diffusionMask(coverage,width,height,false,low);
        layer.style.opacity=String(blurMix(coverage*config.spreadMs));
        if(phase==="concealing"){
          target.style.visibility=p>0?"visible":"hidden";
          target.style.maskImage=diffusionMask(p,width,height,true,low);
          target.style.filter=`blur(${config.revealBlurPx*(1-p)*(1-p)}px)`;
        }else target.style.visibility="hidden";
        if(p===0){complete.current();return;}
        frame=requestAnimationFrame(tick);return;
      }
      const elapsed=now-started.current!;
      const spread=reduced?1:clamp(elapsed/config.spreadMs);
      layer.style.maskImage=diffusionMask(spread,width,height,false,low);
      layer.style.opacity=String(reduced?config.opacityEnd:blurMix(elapsed));
      if(phase==="revealing"){
        const p=reduced||!supported?1:clamp((now-begin)/config.revealMs);
        target.style.visibility="visible";
        target.style.maskImage=diffusionMask(p,width,height,true,low);
        target.style.filter=p===1?"none":`blur(${config.revealBlurPx*(1-p)*(1-p)}px)`;
        if(p===1){target.style.maskImage="none";complete.current();return;}
      }else{target.style.visibility="hidden";target.style.maskImage="none";target.style.filter="none";}
      if(phase==="waiting"&&spread===1)return;
      frame=requestAnimationFrame(tick);
    }
    frame=requestAnimationFrame(tick);
    return()=>{cancelAnimationFrame(frame);window.removeEventListener("resize",resize);};
  },[phase,reduced,content]);
  return <div ref={glass} className="library-reader-glass" aria-hidden="true"/>;
}
