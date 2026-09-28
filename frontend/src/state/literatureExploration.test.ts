import {describe, expect, it} from "vitest";
import {availableRoadParents, branchEntities, filterExploration, orderedRoads, reorderRoadMembers, roadConnections, scopePaperIds, type ExplorationEntity, type ExplorationLink, type ExplorationRoad} from "../../../plugins/literature/frontend/explorationModel";

const entity = (id: string, kind: ExplorationEntity['kind'], extra: Partial<ExplorationEntity> = {}): ExplorationEntity => ({id, node_id: `node-${id}`, title: id, kind, scope_revision: 1, ...extra});
const link = (source: string, target: string): ExplorationLink => ({id: `${source}-${target}`, source, target, relation: "related", rationale: "Source-attributed conceptual overlap."});

describe("literature exploration index", () => {
  it("shows a shared perspective in both branches without duplicating the entity", () => {
    const shared = entity("shared-perspective", "perspective", {title: "Segmental motion", frontier_id: "route-a"});
    const entries = [entity("trail-a","trail",{frontier_id:"route-a"}), entity("trail-b","trail",{frontier_id:"route-b"}),shared];
    const links = [link("trail-a",shared.id),link("trail-b",shared.id)];
    expect(branchEntities(entries,links,"route-a")).toContain(shared);
    expect(branchEntities(entries,links,"route-b")).toContain(shared);
    expect(branchEntities(entries,links,"route-b").filter(item=>item.id===shared.id)).toHaveLength(1);
    expect(branchEntities(entries,links,"route-b")).not.toContain(entries[0]);
  });
  it("keeps a method beside its branch's source Paper and respects explicit core membership", () => {
    const paper=entity("paper", "paper", {frontier_id:"a",paper_id:"canonical-paper"});
    const method=entity("method", "method", {paper_ids:["canonical-paper"]});
    const outside=entity("outside", "method", {paper_ids:["another-paper"]});
    const entries=[paper,method,outside];
    expect(branchEntities(entries,[],"a")).toEqual([paper,method]);
    const collection=entity("core", "collection", {frontier_id:"b",paper_ids:["canonical-paper"]});
    expect(branchEntities([...entries,collection],[],"b")).toEqual([method,collection]);
  });
  it("filters only text and kind without inferring relevance or scientific quality", () => {
    const a=entity("a","paper",{title:"PEO polymer electrolyte"});
    const b=entity("b","web",{title:"Transport data",rationale:"PEO measurements"});
    expect(filterExploration([a,b]," peo ")).toEqual([a,b]);
    expect(filterExploration([a,b],"peo","paper")).toEqual([a]);
    expect(filterExploration([a,b],"validated")).toEqual([]);
  });
  it("deduplicates source IDs and includes only current revision seeds", () => {
    expect(scopePaperIds({paper_ids:["p1","p2"],revisions:[{seed_paper_ids:["old"]},{seed_paper_ids:["p2","seed"]}]})).toEqual(["p1","p2","seed"]);
  });
});

const road = (id: string, extra: Partial<ExplorationRoad> = {}): ExplorationRoad => ({id,title:id,parent_id:null,anchor_id:null,mode:"chain",member_ids:[],scope_revision:1,...extra});

describe("research roads", () => {
  it("draws an ordered road from the physical index instead of a star of contains edges", () => {
    const entities=[entity("p1","paper"),entity("p2","paper"),entity("p3","paper")];
    expect(roadConnections([road("trunk",{member_ids:["p2","p1","p3"]})],entities,"index-node")).toEqual([
      {source:"index-node",target:"node-p2",roadId:"trunk",mode:"chain"},
      {source:"node-p2",target:"node-p1",roadId:"trunk",mode:"chain"},
      {source:"node-p1",target:"node-p3",roadId:"trunk",mode:"chain"},
    ]);
  });
  it("connects a continuing signpost at the parent tail and a branching signpost at its selected stop", () => {
    const entities=[entity("p1","paper"),entity("p2","paper"),entity("a","trail"),entity("b","trail"),entity("b-paper","paper")];
    const roads=[road("trunk",{member_ids:["p1","p2"]}),road("a",{parent_id:"trunk",anchor_id:"a",attach_after:"p1"}),road("b",{parent_id:"trunk",anchor_id:"b",mode:"branch",attach_after:"p1",member_ids:["b-paper"]})];
    const edges=roadConnections(roads,entities,"index-node");
    expect(edges).toContainEqual({source:"node-p2",target:"node-a",roadId:"a",mode:"chain"});
    expect(edges).toContainEqual({source:"node-p1",target:"node-b",roadId:"b",mode:"branch"});
    expect(edges).toContainEqual({source:"node-b",target:"node-b-paper",roadId:"b",mode:"branch"});
    expect(edges).not.toContainEqual(expect.objectContaining({source:"node-p1",target:"node-a"}));
  });
  it("continues from an empty parent signpost and omits missing physical targets", () => {
    const entities=[entity("a","trail"),entity("b","trail"),entity("paper","paper")];
    const roads=[road("trunk"),road("a",{parent_id:"trunk",anchor_id:"a"}),road("b",{parent_id:"a",anchor_id:"b",member_ids:["deleted","paper"]})];
    expect(roadConnections(roads,entities,"index-node")).toEqual([
      {source:"index-node",target:"node-a",roadId:"a",mode:"chain"},
      {source:"node-a",target:"node-b",roadId:"b",mode:"chain"},
      {source:"node-b",target:"node-paper",roadId:"b",mode:"chain"},
    ]);
  });
  it("allows a branch at the parent signpost rather than forcing it to the last Paper", () => {
    const entities=[entity("a","trail"),entity("b","trail"),entity("paper","paper")];
    const roads=[road("trunk"),road("a",{parent_id:"trunk",anchor_id:"a",member_ids:["paper"]}),road("b",{parent_id:"a",anchor_id:"b",mode:"branch",attach_after:"a"})];
    expect(roadConnections(roads,entities,"index-node")).toContainEqual({source:"node-a",target:"node-b",roadId:"b",mode:"branch"});
  });
  it("excludes descendants as possible parents and retains orphan records in the directory", () => {
    const roads=[road("trunk"),road("a",{parent_id:"trunk"}),road("b",{parent_id:"a"}),road("c",{parent_id:"b"}),road("other",{parent_id:"trunk"}),road("orphan",{parent_id:"missing"})];
    expect(availableRoadParents(roads,"a").map(item=>item.id)).toEqual(["trunk","other","orphan"]);
    expect(orderedRoads(roads).map(item=>[item.road.id,item.depth])).toEqual([["trunk",0],["a",1],["b",2],["c",3],["other",1],["orphan",0]]);
  });
  it("reorders complete membership without mutating or dropping unknown references", () => {
    const members=["paper-a","missing-paper","paper-b"];
    expect(reorderRoadMembers(members,"paper-b",-1)).toEqual(["paper-a","paper-b","missing-paper"]);
    expect(reorderRoadMembers(members,"paper-a",-1)).toEqual(members);
    expect(reorderRoadMembers(members,"unlisted",1)).toEqual(members);
    expect(members).toEqual(["paper-a","missing-paper","paper-b"]);
  });
});


it("connects independent trunks to their own scope index and skips unmounted core sets",()=>{
  const entities=[entity('a','trail'),entity('b','trail'),entity('core','collection',{node_id:null})];
  const roads:ExplorationRoad[]=['a','b'].map(id=>({id,anchor_id:id,parent_id:null,mode:'chain',member_ids:[],scope_revision:1,title:id}));
  expect(roadConnections(roads,entities,'scope-one-index').map(edge=>edge.source)).toEqual(['scope-one-index','scope-one-index']);
  expect(roadConnections(roads,entities,'scope-two-index').map(edge=>edge.source)).toEqual(['scope-two-index','scope-two-index']);
  expect(roadConnections(roads,entities,'scope-one-index').map(edge=>edge.target)).toEqual(['node-a','node-b']);
});
