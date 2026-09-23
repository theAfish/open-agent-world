import {afterEach,expect,it,vi} from "vitest";
import {fetchPdf} from "../../../plugins/library/frontend/fetchPdf";
afterEach(()=>vi.unstubAllGlobals());
it("returns the raw PDF bytes",async()=>{
  const fetch=vi.fn().mockResolvedValue(new Response(new Uint8Array([37,80,68,70])));vi.stubGlobal("fetch",fetch);
  const bytes=await fetchPdf("/api/nodes/p/files/raw.pdf?v=1",new AbortController().signal);
  expect([...bytes]).toEqual([37,80,68,70]);expect(fetch.mock.calls[0][0]).toBe("/api/nodes/p/files/raw.pdf?v=1");
});
it("reports missing files instead of rendering an empty PDF",async()=>{
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response("File not found",{status:404})));
  await expect(fetchPdf("/api/nodes/p/files/raw.pdf",new AbortController().signal)).rejects.toThrow("File not found");
});
