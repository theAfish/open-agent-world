import { describe,it,expect } from "vitest";
import { researchVisibleIds } from "./researchVisibility";
import type { WorldCard } from "../types/world";

const card=(id:string,type="text",parent_id?:string)=>({id,type,parent_id} as WorldCard);
describe("AutoResearch presentation membership",()=>{
  it("keeps persisted paper Skill text visible after a reload",()=>{
    const skill={...card("skill"),config:{research_projection:"paper_skill"}};
    expect([...researchVisibleIds([skill,card("ordinary")],[],[])]).toEqual(["skill"]);
  });
  it("reveals the Paper modeling Agent, source image and 3D canvas after a reload",()=>{
    const cards=["atomsculptor.agent","image","atomsculptor.structure"].map((type,index)=>({
      ...card(String(index),type),config:{research_projection:"paper_modeling",paper_id:"paper"},
    }));
    expect([...researchVisibleIds([...cards,card("ordinary")],[],[])]).toEqual(["0","1","2"]);
  });
  it("includes all Papers without selecting a research scope",()=>{
    const ids=researchVisibleIds([card("paper","library.paper"),card("outside")],[],[]);
    expect([...ids]).toEqual(["paper"]);
  });
  it("reveals a created entity and its nested workspace, not unrelated cards",()=>{
    const cards=[card("root","legion"),card("room","legion","root"),card("new","agent","room"),
      card("peer","conversation","root"),card("outside")];
    const ids=researchVisibleIds(cards,["new"],[]);
    expect([...ids].sort()).toEqual(["new","peer","room","root"]);
  });
  it("keeps created cards visible after changing research scopes",()=>{
    const cards=[card("created"),card("scope-a","literature.scope"),card("scope-b","literature.scope")];
    expect(researchVisibleIds(cards,["created"],["scope-a"]).has("created")).toBe(true);
    expect(researchVisibleIds(cards,["created"],["scope-b"]).has("created")).toBe(true);
  });
  it("reveals persisted exploration entities and their basecamp after a reload",()=>{
    const cards=[card("camp","legion"),card("index","literature.index","camp"),
      card("coordinator","agent","camp"),card("trail","literature.trail"),
      card("method","literature.finding"),card("outside","text")];
    const ids=researchVisibleIds(cards,[],[]);
    expect([...ids].sort()).toEqual(["camp","coordinator","index","method","trail"]);
    expect(ids.has("outside")).toBe(false);
  });
});
