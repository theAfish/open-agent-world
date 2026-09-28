import {describe,expect,it} from "vitest";
import {defaultCitationRoad,type CitationRoad} from "../../../plugins/library/frontend/citationRoads";

const roads:CitationRoad[]=[{id:"trunk",title:"Seeds",member_ids:[]},
  {id:"route:source",title:"Source road",frontier_id:"source",member_ids:["paper:p1"]},
  {id:"route:selected",title:"Selected road",frontier_id:"selected",member_ids:[]}];

describe("citation collection road defaults",()=>{
  it("honors the selected frontier only within the same scope",()=>{
    expect(defaultCitationRoad(roads,"scope","p1","scope","selected")).toBe("route:selected");
    expect(defaultCitationRoad(roads,"scope","p1","other","selected")).toBe("route:source");
  });
  it("falls back to the source road and then the trunk",()=>{
    expect(defaultCitationRoad(roads,"scope","p1","scope","missing")).toBe("route:source");
    expect(defaultCitationRoad(roads,"scope","unscoped")).toBe("trunk");
  });
});
