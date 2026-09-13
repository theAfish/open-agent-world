import { t } from "@oaw/plugin-api";
import {useCallback,useEffect,useRef,useState} from "react";
import {READER_TRANSITION} from "./readerTransition";
export type EntrancePhase="spreading"|"waiting"|"revealing"|"complete"|"concealing"|"retracting"|"failed"|"cancelled";
export function useReaderEntrance() {
  const [phase,setPhase]=useState<EntrancePhase>("spreading");
  const [mountReader,setMountReader]=useState(false);
  const [failure,setFailure]=useState("");
  const [reduced]=useState(()=>window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const alive=useRef(true),ready=useRef(false),start=useRef(0),phaseRef=useRef(phase);
  const timers=useRef<ReturnType<typeof setTimeout>[]>([]);
  const change=useCallback((next:EntrancePhase)=>{if(alive.current){phaseRef.current=next;setPhase(next);}},[]);
  const beginReveal=useCallback(()=>{
    if(!alive.current||!ready.current||!["spreading","waiting"].includes(phaseRef.current))return;
    change("revealing");
  },[change]);
  const markReady=useCallback(()=>{
    if(!alive.current||["concealing","retracting"].includes(phaseRef.current))return;ready.current=true;
    const remaining=(reduced?0:READER_TRANSITION.fastMinimumMs)-(performance.now()-start.current);
    if(remaining<=0||phaseRef.current==="waiting")beginReveal();else timers.current.push(setTimeout(beginReveal,remaining));
  },[beginReveal,reduced]);
  const invalidate=useCallback(()=>{
    if(!alive.current||["complete","concealing","retracting","failed","cancelled"].includes(phaseRef.current))return;
    ready.current=false;if(phaseRef.current==="revealing")change("waiting");
  },[change]);
  const fail=useCallback((error:string)=>{if(alive.current&&!["concealing","retracting"].includes(phaseRef.current)){setFailure(error);change("failed");}},[change]);
  const finish=useCallback(()=>{
    if(ready.current&&phaseRef.current==="revealing")change("complete");
    else if(phaseRef.current==="concealing")change("retracting");
    else if(phaseRef.current==="retracting"){change("cancelled");alive.current=false;}
  },[change]);
  const cancel=useCallback(()=>{
    if(!alive.current||["concealing","retracting"].includes(phaseRef.current))return;
    timers.current.forEach(clearTimeout);ready.current=false;
    if(phaseRef.current==="complete")change("concealing");
    else{change("cancelled");alive.current=false;}
  },[change]);
  useEffect(()=>{
    alive.current=true;start.current=performance.now();
    let second=0;
    const first=requestAnimationFrame(()=>{second=requestAnimationFrame(()=>{if(alive.current)setMountReader(true);});});
    timers.current.push(setTimeout(()=>{
      if(phaseRef.current!=="spreading")return;
      if(ready.current)beginReveal();else change("waiting");
    },reduced?0:READER_TRANSITION.spreadMs));
    // A stalled renderer is a recoverable failure, never fake readiness.
    timers.current.push(setTimeout(()=>{if(["spreading","waiting"].includes(phaseRef.current))fail(t("PDF 加载未完成，请重试或返回窗口。"));},READER_TRANSITION.failureMs));
    return()=>{alive.current=false;cancelAnimationFrame(first);cancelAnimationFrame(second);timers.current.forEach(clearTimeout);};
  },[beginReveal,change,fail,reduced]);
  return {phase,mountReader,failure,reduced,markReady,invalidate,fail,finish,cancel};
}
