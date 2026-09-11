import { create } from "zustand";
import type { WorldCard, PluginCatalog } from "../types/world";
import { NODE_SURFACE_SIZE, type NodeSurfaceLevel } from "./nodeSurfaces";
import { positionSurfaceAtNodeCenter } from "../canvas/nodeDisplacement";

export const SHADOW = { type:"core.shadow-collection", hoverMs:150, absorbMs:300, expandMs:500, collapseMs:400,
  hoverPx:4, hoverDegrees:2, padding:72, hullPadding:52, feather:18,
  stackWidth:380, stackHeight:350 } as const;
export type CollectionState = "minimal" | "stacked" | "expanded";
export const useCollectionRelease=create<{active:Record<string,boolean>;set:(id:string,on:boolean)=>void}>(set=>({active:{},set:(id,on)=>set(s=>{const active={...s.active};if(on)active[id]=true;else delete active[id];return {active};})}));
export function canReleaseMember(enabled:boolean,point:{x:number;y:number},rect:ShadowRect & {rects:ShadowRect[]}) {
  if(!enabled)return false;
  if(!insideShadow(point.x,point.y,rect))return true;
  const points=shadowPoints(rect.width,rect.height,rect.rects);
  return points.some(p=>Math.hypot(point.x-rect.x-p.x,point.y-rect.y-p.y)<28);
}
export const isShadow = (card?:WorldCard) => card?.type===SHADOW.type;
export const collectionState = (card:WorldCard):CollectionState => card.config.display_state==="expanded"?"expanded":card.config.display_state==="stacked"?"stacked":"minimal";
export const useCollectionHover = create<{members:Record<string,string|undefined>;set:(id:string,member?:string)=>void}>(set=>({members:{},set:(id,member)=>set(s=>{const members={...s.members};if(member)members[id]=member;else delete members[id];return {members};})}));
export const useCollectionDrag=create<{positions:Record<string,{x:number;y:number}>;set:(id?:string,position?:{x:number;y:number})=>void}>(set=>({positions:{},set:(id,position)=>set({positions:id&&position?{[id]:position}:{}})}));
export function collectionCounts(members:WorldCard[],catalog:PluginCatalog) {
  let pdf=0,agent=0,object=0;
  for(const member of members) {
    const traits=catalog.node_types.find(t=>t.id===member.type)?.traits??[];
    if(traits.includes("core.agent"))agent++;
    else if(traits.includes("library.readable")||traits.includes("core.pdf"))pdf++;
    else object++;
  }
  return {pdf,agent,object};
}
export function foldedAncestor(card:WorldCard,cards:WorldCard[]) {
  let parent=cards.find(c=>c.id===card.parent_id),result:WorldCard|undefined;
  const seen=new Set<string>();
  while(parent&&!seen.has(parent.id)) {
    seen.add(parent.id);
    if(isShadow(parent)&&collectionState(parent)!=="expanded")result=parent;
    parent=cards.find(c=>c.id===parent!.parent_id);
  }
  return result;
}
/** Presentation only: never change the edge's persisted endpoints. */
export function hiddenCollectionEdge(sourceId:string,targetId:string,cards:WorldCard[]) {
  const source=cards.find(card=>card.id===sourceId),target=cards.find(card=>card.id===targetId);
  const a=source&&foldedAncestor(source,cards),b=target&&foldedAncestor(target,cards);
  return Boolean(a&&b&&a.id===b.id);
}
export type ShadowRect={x:number;y:number;width:number;height:number};
/** Never mutate React Flow's live position while converting to a persisted anchor. */
export function collectionAnchorFromSurface(surface:{x:number;y:number},anchor:{x:number;y:number},origin:{x:number;y:number}) {
  return {x:surface.x+anchor.x-origin.x,y:surface.y+anchor.y-origin.y};
}
export function shadowLayout(card:WorldCard,cards:WorldCard[],levels=new Map<string,NodeSurfaceLevel>(),catalog?:PluginCatalog):ShadowRect & {rects:ShadowRect[]} {
  const state=collectionState(card);
  if(state!=="expanded")return {...card.position,width:state==="minimal"?132:SHADOW.stackWidth,height:state==="minimal"?128:SHADOW.stackHeight,rects:[] as ShadowRect[]};
  const rects=cards.filter(c=>c.parent_id===card.id).map(c=>{
    if(isShadow(c))return shadowLayout(c,cards,levels,catalog);
    const container=catalog?.node_types.find(t=>t.id===c.type)?.container;
    if(container)return {...c.position,width:Math.max(c.size.width,container.min_size[0]),height:Math.max(c.size.height,container.min_size[1])};
    const level=levels.get(c.id)??"preview";
    return {...positionSurfaceAtNodeCenter(c.position,level),...NODE_SURFACE_SIZE[level]};
  });
  const x=Math.min(card.position.x,...rects.map(r=>r.x-SHADOW.padding));
  const y=Math.min(card.position.y,...rects.map(r=>r.y-90));
  const right=Math.max(card.position.x+380,...rects.map(r=>r.x+r.width+SHADOW.padding));
  const bottom=Math.max(card.position.y+320,...rects.map(r=>r.y+r.height+90));
  return {x,y,width:right-x,height:bottom-y,rects:rects.map(r=>({...r,x:r.x-x,y:r.y-y}))};
}
export function shadowPresentation(card:WorldCard,cards:WorldCard[],levels:Map<string,NodeSurfaceLevel>,positions:Record<string,{x:number;y:number}>,catalog?:PluginCatalog) {
  const live=collectionState(card)==="expanded"?cards.map(c=>positions[c.id]?{...c,position:positions[c.id]}:c):cards;
  return shadowLayout(card,live,levels,catalog);
}

/** Outer hull bridges the gaps between members instead of creating radial spokes. */
function memberHull(width:number,height:number,rects:ShadowRect[]) {
  const bounds=[...rects,{x:width/2-110,y:20,width:width/2+85,height:40},{x:width/2-80,y:height-54,width:160,height:36}];
  const points=bounds.flatMap(r=>[
    {x:r.x-SHADOW.hullPadding,y:r.y-SHADOW.hullPadding},{x:r.x+r.width+SHADOW.hullPadding,y:r.y-SHADOW.hullPadding},
    {x:r.x+r.width+SHADOW.hullPadding,y:r.y+r.height+SHADOW.hullPadding},{x:r.x-SHADOW.hullPadding,y:r.y+r.height+SHADOW.hullPadding},
  ]).sort((a,b)=>a.x-b.x||a.y-b.y);
  const cross=(a:typeof points[number],b:typeof a,c:typeof a)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
  const half=(list:typeof points)=>{
    const result:typeof points=[];
    for(const p of list){while(result.length>=2&&cross(result[result.length-2],result[result.length-1],p)<=0)result.pop();result.push(p);}
    return result.slice(0,-1);
  };
  return [...half(points),...half([...points].reverse())];
}
/** Fixed 96-point topology keeps morphing and cardinal connection ports stable. */
export function shadowPoints(width:number,height:number,rects:ShadowRect[]) {
  const cx=width/2,cy=height/2,n=96;
  const hull=rects.length?memberHull(width,height,rects):[];
  const radii=Array.from({length:n},(_,i)=>{
    const angle=i*2*Math.PI/n,dx=Math.cos(angle),dy=Math.sin(angle);
    let radius=1/((dx/(width*.46))**4+(dy/(height*.46))**4)**.25;
    if(rects.length) {
      radius=45;
      for(let j=0;j<hull.length;j++) {
        const a=hull[j],b=hull[(j+1)%hull.length];
        const ex=b.x-a.x,ey=b.y-a.y,den=dx*ey-dy*ex;
        if(Math.abs(den)<1e-8)continue;
        const ax=a.x-cx,ay=a.y-cy;
        const distance=(ax*ey-ay*ex)/den,t=(ax*dy-ay*dx)/den;
        if(distance>=0&&t>=0&&t<=1)radius=Math.max(radius,distance);
      }
    }
    return radius*(1+.014*Math.sin(angle*5)+.009*Math.cos(angle*3));
  });
  for(let pass=0;pass<4;pass++){const old=[...radii];for(let i=0;i<n;i++)radii[i]=(old[(i+n-1)%n]+2*old[i]+old[(i+1)%n])/4;}
  return radii.map((r,i)=>({x:cx+Math.cos(i*2*Math.PI/n)*r,y:cy+Math.sin(i*2*Math.PI/n)*r}));
}
export function shadowContour(width:number,height:number,rects:ShadowRect[]) {
  return shadowPoints(width,height,rects).map((p,i)=>`${i?"L":"M"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ")+" Z";
}
export function insideShadow(x:number,y:number,rect:ReturnType<typeof shadowLayout>) {
  const points=shadowPoints(rect.width,rect.height,rect.rects);
  x-=rect.x;y-=rect.y;let inside=false;
  for(let i=0,j=points.length-1;i<points.length;j=i++) {
    const a=points[i],b=points[j];
    if((a.y>y)!==(b.y>y)&&x<(b.x-a.x)*(y-a.y)/(b.y-a.y)+a.x)inside=!inside;
  }
  return inside;
}
