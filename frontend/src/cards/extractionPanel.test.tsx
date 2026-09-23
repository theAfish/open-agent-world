// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {ExtractionPanel} from "../../../plugins/library/frontend/ExtractionPanel";
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals();});
const entry={id:"v1",source:"grobid",created_at:"",extractor:"grobid/0.8.2",warnings:0,note:"",based_on:null,actor_id:null,key:"extractions/v1.json"};
const overview={version:"v1",metadata:{title:"Cathodes",authors:[],identifiers:{doi:null}},references:0,warnings:[],
  sections:[{path:"sections.0",number:"1",heading:"Introduction",level:1,loc:{page:2,bbox:[.1,.2,.5,.25]}}],figures:[],tables:[]};
function serve(manifests:unknown[]){
  return vi.fn(async(url:string)=>{
    const body=url.endsWith("/manifest")?manifests.length>1?manifests.shift():manifests[0]:overview;
    return new Response(JSON.stringify(body));
  });
}

it("polls while GROBID runs in the background and refreshes the card when it settles",async()=>{
  vi.useFakeTimers();
  const fetch=serve([{filename:"a.pdf",extractions:[],active:null,extracting:true},{filename:"a.pdf",extractions:[entry],active:"v1",extracting:false}]);
  vi.stubGlobal("fetch",fetch);const changed=vi.fn();
  render(<ExtractionPanel paperId="p" name="a" active={null} onChanged={changed} onLocate={()=>{}}/>);
  await act(async()=>{await vi.advanceTimersByTimeAsync(0);});
  expect(screen.getByRole("status").textContent).toContain("GROBID");
  expect((screen.getByRole("button",{name:"Re-extract"}) as HTMLButtonElement).disabled).toBe(true);
  await act(async()=>{await vi.advanceTimersByTimeAsync(2600);});
  expect(changed).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Cathodes")).toBeTruthy();
});

it("opens the reader at an outline entry's GROBID location",async()=>{
  vi.stubGlobal("fetch",serve([{filename:"a.pdf",extractions:[entry],active:"v1",extracting:false}]));const locate=vi.fn();
  render(<ExtractionPanel paperId="p" name="a" active="v1" onChanged={()=>{}} onLocate={locate}/>);
  fireEvent.click(await screen.findByRole("button",{name:/Introduction/}));
  expect(locate).toHaveBeenCalledWith({page:2,bbox:[.1,.2,.5,.25]});
});
