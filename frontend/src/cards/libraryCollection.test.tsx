// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {LibraryCatalog} from "../../../plugins/library/frontend/LibraryCollection";
import {useWorldStore} from "../state/worldStore";
import type {PluginViewProps} from "../plugins/sdk";
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

const library={id:"lib",type:"library.collection",name:"Cathodes",parent_id:null,position:{x:0,y:0},size:{width:720,height:480},config:{},updated_at:"t0"};
const paper={id:"p1",type:"library.paper",name:"oxide.pdf",parent_id:"lib",position:{x:40,y:170},size:{width:300,height:210},config:{},updated_at:"t0"};
const catalog={total:1,status_counts:{structured:1},papers:[{paper:"p1",name:"oxide.pdf",title:"Layered oxide cathodes",
  authors:["Alice Zhang","Bob Smith","Carol Lee","Dan Wu"],year:2024,venue:"J. Test Mater.",doi:null,pages:3,status:"structured",version:"v1"}]};
const hits={hits:[{paper:"p1",paper_name:"oxide.pdf",title:"Layered oxide cathodes",year:2024,kind:"section",heading:"3 Results",
  page:2,bbox:[.1,.2,.5,.3],path:"sections.3",snippet:"The [capacity] is <b>high</b>",cite:"p1#p2"}]};

function view(resourceAction:PluginViewProps["host"]["resourceAction"]) {
  useWorldStore.setState({cards:[library,paper]} as never);
  const props={card:library,level:"workspace",definition:{},host:{resourceAction}} as unknown as PluginViewProps;
  return render(<LibraryCatalog {...props}/>);
}

it("lists the library's papers from the catalog action",async()=>{
  const action=vi.fn(async()=>catalog);
  view(action);
  const row=(await screen.findByRole("button",{name:"Layered oxide cathodes"})).closest("tr")!;
  expect(within(row).getByText("Alice Zhang, Bob Smith, Carol Lee et al.")).toBeTruthy();
  expect(within(row).getByText("2024")).toBeTruthy();
  expect(action).toHaveBeenCalledWith("catalog",expect.objectContaining({limit:500}));
});

it("searches with year filters and marks matches without rendering snippet markup",async()=>{
  const action=vi.fn(async(name:string)=>name==="search"?hits:catalog);
  view(action);
  fireEvent.change(screen.getByRole("searchbox",{name:"Search the full text of every paper"}),{target:{value:"capacity"}});
  fireEvent.change(screen.getByRole("textbox",{name:"From year"}),{target:{value:"2020"}});
  fireEvent.click(screen.getByRole("button",{name:"Search"}));
  const result=await screen.findByRole("region",{name:"Search results"});
  expect(action).toHaveBeenCalledWith("search",{query:"capacity",limit:20,year_from:2020});
  expect(within(result).getByText("capacity").tagName).toBe("MARK");
  expect(result.querySelector("b")).toBeNull();
  expect(within(result).getByRole("button",{name:/3 Results · Page 2/})).toBeTruthy();
});

it("moves a paper out of the library beside it",async()=>{
  const fetch=vi.fn(async()=>new Response("{}"));vi.stubGlobal("fetch",fetch);
  view(vi.fn(async()=>catalog));
  fireEvent.click(await screen.findByRole("button",{name:"Move out"}));
  expect(fetch).toHaveBeenCalledWith("/api/nodes/p1",expect.objectContaining({method:"PATCH",
    body:JSON.stringify({parent_id:null,position:{x:760,y:0}})}));
});
