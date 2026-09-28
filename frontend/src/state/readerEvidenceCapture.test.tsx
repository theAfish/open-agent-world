// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReaderEvidenceCapture } from "../../../plugins/library/frontend/ReaderEvidenceCapture";
import { ScopeEvidence } from "../../../plugins/literature/frontend/ScopeEvidence";

vi.mock("@oaw/plugin-api",()=>({t:(text:string)=>text,useLocale:()=>"zh"}));
vi.mock("./worldStore",()=>({useWorldStore:(select:(value:unknown)=>unknown)=>select({cards:[{id:"scope",type:"literature.scope",name:"Research question"}]})}));
vi.mock("./autoResearch",()=>({useAutoResearch:(select:(value:unknown)=>unknown)=>select({scopeId:"scope"})}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

const source={id:"host-paragraph",quote:"Host original paragraph containing selected words.",paper_id:"paper",page:2,document_version_id:"a".repeat(64),quote_sha256:"b".repeat(64),rects:[[.1,.2,.3,.4]],text_parser_version:"pymupdf/host"};
const evidence={id:"evidence",revision:3,claim:"A cautious claim",kind:"author_statement",relation:"insufficient",conditions:[],sources:[source],scientific_verification:"reviewed",scientific_reviews:[{id:"old-review",decision:"insufficient",reviewer:"desktop",rationale:"Prior opinion",reviewed_at:"2026-01-01T00:00:00Z"}]};
const scopeDoc={revision:9,value:{paper_ids:["paper"],revisions:[],evidence:[evidence],methods:[]}};

function setupFetch(initialMember=true){
  let member=initialMember;
  const mock=vi.fn(async(path:string,init?:RequestInit)=>{
    if(path.endsWith("/intake-options"))return {ok:true,json:async()=>({scopes:[{id:"scope",title:"Research question",configured:true,paper_ids:member?["paper"]:[]},{id:"other",title:"Other question",configured:true,paper_ids:["paper"]}]})};
    if(path.endsWith("/link_paper")){member=true;return {ok:true,json:async()=>({revision:10})};}
    if(path.endsWith("/paper"))return {ok:true,json:async()=>({sources:[source],matching_source_ids:[source.id],match_status:"unique_text_match",confirmation_required:true})};
    if(init)return {ok:true,json:async()=>({revision:10})};
    return {ok:true,json:async()=>({...scopeDoc,value:{...scopeDoc.value,paper_ids:member?["paper"]:[]}})};
  });
  vi.stubGlobal("fetch",mock);return mock;
}
function capture(){return render(<ReaderEvidenceCapture paperId="paper" documentVersionId={source.document_version_id} page={2} text="selected words"/>);}

it("requires inspection confirmation and saves only the host paragraph with chosen classification",async()=>{
  const fetch=setupFetch();capture();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"记录研究证据"}));
  const confirm=await screen.findByRole("checkbox");
  expect(screen.getByText(source.quote)).toBeTruthy();
  expect(screen.getByRole("button",{name:"保存研究证据"}).hasAttribute("disabled")).toBe(true);
  const relocate=fetch.mock.calls.find(([path])=>path.endsWith("/paper"));
  expect(JSON.parse(String(relocate?.[1]?.body)).arguments).toEqual({paper_id:"paper",view:"relocate",page:2,selected_text:"selected words",document_version_id:source.document_version_id});
  fireEvent.click(confirm);
  fireEvent.change(screen.getByLabelText("记录类型"),{target:{value:"user_hypothesis"}});
  fireEvent.change(screen.getByLabelText("原文与主张的关系"),{target:{value:"contradicts"}});
  fireEvent.click(screen.getByRole("button",{name:"保存研究证据"}));
  await screen.findByText("已保存研究证据，等待单独复核。");
  const record=fetch.mock.calls.find(([path])=>path.endsWith("/record"));
  const body=JSON.parse(String(record?.[1]?.body));
  expect(body.expected_revision).toBe(9);
  expect(body.arguments.value).toMatchObject({kind:"user_hypothesis",relation:"contradicts",sources:[source],extracted_by:"desktop"});
  expect(body.arguments.value.scientific_reviews).toBeUndefined();
});

it("requires a new paragraph confirmation after changing research scope",async()=>{
  setupFetch();capture();fireEvent.click(screen.getByRole("button",{name:"记录研究证据"}));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("研究范围"),{target:{value:"other"}});
  await waitFor(()=>expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false));
  expect(screen.getByRole("button",{name:"保存研究证据"}).hasAttribute("disabled")).toBe(true);
});

it("links a nonmember Paper only after the explicit include action",async()=>{
  const fetch=setupFetch(false);capture();fireEvent.click(screen.getByRole("button",{name:"记录研究证据"}));
  const include=await screen.findByRole("button",{name:"纳入此范围并定位原文"});
  expect(fetch.mock.calls.some(([path])=>path.endsWith("/link_paper"))).toBe(false);
  expect(fetch.mock.calls.some(([path])=>path.endsWith("/paper"))).toBe(false);
  fireEvent.click(include);await screen.findByRole("checkbox");
  const linked=fetch.mock.calls.find(([path])=>path.endsWith("/link_paper"));
  expect(JSON.parse(String(linked?.[1]?.body))).toEqual({expected_revision:9,arguments:{paper_id:"paper"}});
});

it("revises classification using item revision without carrying scientific reviews",async()=>{
  const fetch=setupFetch();const reload=vi.fn(async()=>{});
  render(<ScopeEvidence scopeId="scope" doc={scopeDoc as any} reload={reload} openPaper={()=>{}}/>);
  fireEvent.click(screen.getByRole("button",{name:"编辑主张与分类"}));
  fireEvent.change(screen.getByLabelText("记录类型"),{target:{value:"agent_inference"}});
  fireEvent.click(screen.getByRole("button",{name:"保存修订"}));
  await waitFor(()=>expect(reload).toHaveBeenCalled());
  const record=fetch.mock.calls.find(([path])=>path.endsWith("/record"));
  const body=JSON.parse(String(record?.[1]?.body));
  expect(body.arguments.item_revision).toBe(3);
  expect(body.arguments.value.kind).toBe("agent_inference");
  expect(body.arguments.value.scientific_reviews).toBeUndefined();
  expect(body.arguments.value.scientific_verification).toBeUndefined();
});

it("submits review separately with a rationale and leaves authority fields to the host",async()=>{
  const fetch=setupFetch();const reload=vi.fn(async()=>{});
  render(<ScopeEvidence scopeId="scope" doc={scopeDoc as any} reload={reload} openPaper={()=>{}}/>);
  fireEvent.click(screen.getByRole("button",{name:"单独复核"}));
  expect(screen.getByRole("button",{name:"提交复核意见"}).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("复核理由"),{target:{value:"The source does not establish this claim."}});
  fireEvent.click(screen.getByRole("button",{name:"提交复核意见"}));
  await waitFor(()=>expect(reload).toHaveBeenCalled());
  const review=fetch.mock.calls.find(([path])=>path.endsWith("/review"));
  expect(JSON.parse(String(review?.[1]?.body)).arguments).toEqual({evidence_id:"evidence",item_revision:3,decision:"insufficient",rationale:"The source does not establish this claim."});
  expect(fetch.mock.calls.some(([path])=>path.endsWith("/record"))).toBe(false);
});
