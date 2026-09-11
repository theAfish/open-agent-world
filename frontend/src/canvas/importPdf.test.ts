import {afterEach,expect,it,vi} from "vitest";
import {importPdf} from "./importPdf";
import {useWorldStore} from "../state/worldStore";

function mockUpload(status=200) {
  vi.stubGlobal("XMLHttpRequest",class {
    status=status;responseText="invalid PDF";
    upload:{onprogress?:(event:unknown)=>void;onload?:()=>void}={};
    onload?:()=>void;
    open(){} setRequestHeader(){}
    send(){this.upload.onprogress?.({lengthComputable:true,loaded:50,total:100});this.upload.onload?.();this.onload?.();}
  });
}
afterEach(()=>vi.unstubAllGlobals());
it("rejects oversized PDFs before creating a node",async()=>{
  const fetch=vi.fn();vi.stubGlobal("fetch",fetch);
  await expect(importPdf({name:"large.pdf",size:26*1024*1024} as File,{x:0,y:0})).rejects.toThrow("25 MiB");
  expect(fetch).not.toHaveBeenCalled();
});
it("passes drop membership and removes only the new node after import failure",async()=>{
  vi.stubGlobal("FileReader",class {result="data:application/pdf;base64,QQ==";onload?:()=>void;readAsDataURL(){this.onload?.();}});
  mockUpload(400);
  const fetch=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>({id:"created"})}).mockResolvedValueOnce({ok:true,json:async()=>({revision:1})}).mockResolvedValueOnce({ok:true});
  vi.stubGlobal("fetch",fetch);
  await expect(importPdf({name:"notes.pdf",size:1} as File,{x:10,y:20},"legion")).rejects.toThrow("invalid PDF");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({parent_id:"legion",position:{x:10,y:20}});
  expect(fetch.mock.calls[2]).toEqual(["/api/nodes/created",{method:"DELETE"}]);
});

it("synchronizes each imported node without refresh and reports real upload progress",async()=>{
  vi.stubGlobal("FileReader",class {result="data:application/pdf;base64,QQ==";onload?:()=>void;readAsDataURL(){this.onload?.();}});
  mockUpload();
  const node={id:"new-paper",type:"library.paper",name:"notes",parent_id:"legion",position:{x:10,y:20},config:{page_count:2}};
  vi.stubGlobal("fetch",vi.fn().mockResolvedValueOnce({ok:true,json:async()=>node}).mockResolvedValueOnce({ok:true,json:async()=>({revision:1})}).mockResolvedValueOnce({ok:true,json:async()=>node}));
  const progress=vi.fn();
  const card=await importPdf({name:"notes.pdf",size:1} as File,{x:10,y:20},"legion",progress);
  useWorldStore.setState({cards:[]});
  useWorldStore.getState().acceptImportedCard(card);
  useWorldStore.getState().acceptImportedCard(card);
  expect(useWorldStore.getState().cards).toHaveLength(1);
  expect(useWorldStore.getState().cards[0]).toMatchObject(node);
  expect(progress.mock.calls.map(call=>call[0])).toEqual([
    {stage:"reading",percent:0},{stage:"uploading",percent:0},{stage:"uploading",percent:50},
    {stage:"processing",percent:100},{stage:"complete",percent:100},
  ]);
});

it("keeps successful imports when the summary read fails",async()=>{
  vi.stubGlobal("FileReader",class {result="data:application/pdf;base64,QQ==";onload?:()=>void;readAsDataURL(){this.onload?.();}});
  mockUpload();
  const fetch=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>({id:"saved",type:"library.paper"})}).mockResolvedValueOnce({ok:true,json:async()=>({revision:1})}).mockRejectedValueOnce(new Error("offline"));
  vi.stubGlobal("fetch",fetch);
  expect((await importPdf({name:"notes.pdf",size:1} as File,{x:0,y:0})).id).toBe("saved");
  expect(fetch.mock.calls.some(call=>call[1]?.method==="DELETE")).toBe(false);
});
