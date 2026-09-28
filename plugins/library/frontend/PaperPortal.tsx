import { useEffect, useState } from "react";
import type { PluginViewProps } from "@oaw/plugin-api";
import { ActiveReader } from "./index";

export type SourceLocation = {page:number;document_version_id:string;rects?:number[][]};

/** Shared entry from research results, snapshots and evidence back to the same Paper. */
export function PaperPortal({paperId,sourceLocation,onClose}:{paperId:string;sourceLocation?:SourceLocation;onClose:()=>void}) {
  const [card,setCard]=useState<PluginViewProps["card"]>(),[error,setError]=useState(""),[attempt,setAttempt]=useState(0);
  useEffect(()=>{let active=true;void fetch(`/api/nodes/${encodeURIComponent(paperId)}`).then(async response=>{if(!response.ok)throw new Error(await response.text());return response.json();}).then(card=>{if(active)setCard(card);}).catch(error=>{if(active)setError(String(error));});return()=>{active=false;};},[paperId]);
  if(error)return <div role="alert">{error}<button onClick={onClose}>×</button></div>;
  return card?.id===paperId?<ActiveReader key={`${paperId}:${attempt}`} {...{card,level:"workspace"} as PluginViewProps} sourceLocation={sourceLocation} onClose={onClose} onRetry={()=>setAttempt(value=>value+1)}/>:null;
}
