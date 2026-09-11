import { expect, it } from "vitest";
import { normalizeCard } from "../api/client";
import type { CanvasNode } from "../cards/types";
import { layoutIsPresented } from "./layoutPresentation";

const card=normalizeCard({id:"collection",type:"core.shadow-collection",position:{x:100,y:200}});
const node:CanvasNode={id:card.id,type:"container",position:{x:50,y:80},style:{width:600,height:400},data:{card,surfaceLevel:"preview",displaced:false}};
it("releases drag geometry after a decorated animation frame reaches the saved layout",()=>{
  const decorated={...node,data:{...node.data,shadowWidth:600,shadowHeight:400}};
  expect(decorated.data).not.toBe(node.data);
  expect(layoutIsPresented([decorated],[node])).toBe(true);
});
it("waits for position, parent, dimensions, and authoritative card to be presented",()=>{
  for(const stale of [
    {...node,position:{x:49,y:80}}, {...node,parentId:"old-parent"},
    {...node,style:{width:599,height:400}}, {...node,data:{...node.data,card:{...card}}},
  ])expect(layoutIsPresented([stale],[node])).toBe(false);
});
it("accepts a presented rollback instead of waiting forever for the requested position",()=>{
  const restored={...node,data:{...node.data,card:{...card,position:{x:0,y:0}}}};
  expect(layoutIsPresented([restored],[restored])).toBe(true);
  expect(layoutIsPresented([node],[restored])).toBe(false);
});
