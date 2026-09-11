import {expect,it} from "vitest";
import {normalizeCard} from "../api/client";
import {shadowPresentation,collectionAnchorFromSurface,canReleaseMember,collectionCounts,foldedAncestor,hiddenCollectionEdge,shadowContour,shadowLayout} from "./shadowCollection";
import {TEST_CATALOG} from "./catalog.fixture";
import {roundedRectAnchor} from "../edges/geometry";
import {shadowPoints,insideShadow} from "./shadowCollection";
const collection=normalizeCard({id:"c",type:"core.shadow-collection",position:{x:300,y:300},config:{display_state:"expanded"}});
const members=[normalizeCard({id:"a",type:"agent",parent_id:"c",position:{x:0,y:400}}),normalizeCard({id:"t",type:"text",parent_id:"c",position:{x:600,y:500}})];
it("translates a whole collection without changing its local outline in every display state",()=>{
  for(const display_state of ["expanded","stacked","minimal"]){
    const card={...collection,config:{display_state}};
    const start=shadowLayout(card,members);
    for(const delta of [{x:-850,y:530},{x:1250,y:-940}]){
      const move=(c:typeof collection)=>({...c,position:{x:c.position.x+delta.x,y:c.position.y+delta.y}});
      const end=shadowLayout(move(card),members.map(move));
      expect(end.x-start.x).toBe(delta.x);
      expect(end.y-start.y).toBe(delta.y);
      expect([end.width,end.height,end.rects]).toEqual([start.width,start.height,start.rects]);
      expect(shadowContour(end.width,end.height,end.rects)).toEqual(shadowContour(start.width,start.height,start.rects));
    }
  }
});
it("uses identical bounds and contour before and after an edge-member drop",()=>{
  for(const position of [{x:-450,y:100},{x:1000,y:1100}]){
    const live=shadowPresentation(collection,members,new Map(),{a:position});
    const saved=shadowLayout(collection,members.map(m=>m.id==='a'?{...m,position}:m));
    expect(live).toEqual(saved);
    expect(shadowContour(live.width,live.height,live.rects)).toEqual(shadowContour(saved.width,saved.height,saved.rects));
  }
});
it("converts a dragged shadow origin without mutating its displayed position",()=>{
  const live=Object.freeze({x:-3892,y:950});
  const saved=collectionAnchorFromSurface(live,{x:-1863,y:1515},{x:-3431,y:1103});
  expect(saved).toEqual({x:-2324,y:1362});
  expect(live).toEqual({x:-3892,y:950});
  expect(saved).not.toBe(live);
});
it("requires explicit release mode and a boundary/outside drop",()=>{
  const rect=shadowLayout({...collection,config:{display_state:"stacked"}},members);
  const center={x:rect.x+rect.width/2,y:rect.y+rect.height/2};
  const outside={x:rect.x-100,y:rect.y-100};
  expect(canReleaseMember(false,outside,rect)).toBe(false);
  expect(canReleaseMember(true,center,rect)).toBe(false);
  expect(canReleaseMember(true,outside,rect)).toBe(true);
  const edge=shadowPoints(rect.width,rect.height,rect.rects)[0];
  expect(canReleaseMember(true,{x:rect.x+edge.x,y:rect.y+edge.y},rect)).toBe(true);
});
it("hides only folded internal connections and restores them on expansion",()=>{
  const outside=normalizeCard({id:"outside",type:"agent"});
  for(const state of ["stacked","minimal","expanded","stacked","expanded"]){
    const cards=[{...collection,config:{display_state:state}},...members,outside];
    expect(hiddenCollectionEdge("a","t",cards)).toBe(state!=="expanded");
    expect(hiddenCollectionEdge("outside","c",cards)).toBe(false);
    expect(hiddenCollectionEdge("outside","a",cards)).toBe(false);
  }
});
it("counts direct member types without overlap",()=>{
  expect(collectionCounts(members,TEST_CATALOG)).toEqual({pdf:0,agent:1,object:1});
});
it("keeps the layout independent of collapse and supports negative anchor offsets",()=>{
  const original=JSON.stringify(members);
  const expanded=shadowLayout(collection,members);
  expect(expanded.x).toBeLessThan(collection.position.x);
  const folded={...collection,config:{display_state:"stacked"}};
  expect(shadowLayout(folded,members).width).toBe(380);
  expect(shadowLayout(collection,members)).toEqual(expanded);
  expect(JSON.stringify(members)).toBe(original);
  expect(foldedAncestor(members[0],[folded,...members])?.id).toBe("c");
  expect(foldedAncestor(members[0],[collection,...members])).toBeUndefined();
});
it("builds a stable finite closed silhouette with fixed topology",()=>{
  const rect=shadowLayout(collection,members);
  const path=shadowContour(rect.width,rect.height,rect.rects);
  expect(path).not.toMatch(/NaN|Infinity/);
  expect(path.endsWith(" Z")).toBe(true);
  expect(path.match(/L/g)).toHaveLength(95);
});
it("fills the gaps between outer members without spokes into the center",()=>{
  const rect={x:0,y:0,width:1000,height:1000,rects:[
    {x:60,y:60,width:180,height:120},{x:760,y:60,width:180,height:120},
    {x:60,y:800,width:180,height:120},{x:760,y:800,width:180,height:120},
  ]};
  for(const x of [180,350,500,650,820])for(const y of [180,350,500,650,800])
    expect(insideShadow(x,y,rect)).toBe(true);
  expect(insideShadow(-100,500,rect)).toBe(false);
});
it("shares the visible contour with port/edge anchors and excludes transparent corners",()=>{
  const rect=shadowLayout({...collection,config:{display_state:"minimal"}},members);
  const outline=shadowPoints(rect.width,rect.height,rect.rects);
  const anchor=roundedRectAnchor({...rect,outline},{x:1000,y:rect.y+rect.height/2});
  expect(anchor.x).toBeCloseTo(rect.x+outline[0].x,3);
  expect(insideShadow(rect.x+1,rect.y+1,rect)).toBe(false);
  expect(insideShadow(rect.x+rect.width/2,rect.y+rect.height/2,rect)).toBe(true);
});
