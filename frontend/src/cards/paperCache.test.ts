import { afterEach, expect, it, vi } from "vitest";
import {loadPaper, loadPaperPreview} from "../../../plugins/library/frontend/paperCache";
afterEach(()=>vi.unstubAllGlobals());
it("previews never request full PDFs; repeated reader opens reuse a revision-checked document",async()=>{
  const value={pdf:"pdf",thumbnail:"cover",notes:"",annotations:[],pages:1,page:1};
  const fetch=vi.fn().mockImplementation(async(url:string)=>({ok:true,json:async()=>url.endsWith("preview")?{revision:1,value:{thumbnail:"cover",pages:1}}:{revision:1,value}}));
  vi.stubGlobal("fetch",fetch);
  await loadPaperPreview("cache-test");
  expect(fetch.mock.calls.every(([url])=>url.endsWith("preview"))).toBe(true);
  await loadPaper("cache-test"); await loadPaper("cache-test");
  expect(fetch.mock.calls.filter(([url])=>url.endsWith("/document"))).toHaveLength(1);
  fetch.mockImplementation(async(url:string)=>({ok:true,json:async()=>url.endsWith("preview")?{revision:2,value:{thumbnail:"cover",pages:1}}:{revision:2,value}}));
  expect((await loadPaper("cache-test")).revision).toBe(2);
  expect(fetch.mock.calls.filter(([url])=>url.endsWith("/document"))).toHaveLength(2);
});
